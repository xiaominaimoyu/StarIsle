"""Fail-closed validation for the static production release artifact.

This runner deliberately reads only ``PUBLIC_SITE_URL``, a fixed set of build
inputs, and ``apps/web/dist``.  It never loads dotenv files, contacts a
network service, or reports artifact contents in its output.
"""

from __future__ import annotations

import argparse
import hashlib
import ipaddress
import json
import os
import re
import sys
import xml.etree.ElementTree as ElementTree
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Iterable, Sequence
from urllib.parse import urlsplit


_SCRIPT_DIRECTORY = Path(__file__).resolve().parent
if str(_SCRIPT_DIRECTORY) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIRECTORY))

from harness_safety import (  # noqa: E402
    HarnessSafetyError,
    has_link_or_reparse_component,
    is_link_or_reparse,
)


REPOSITORY_ROOT = _SCRIPT_DIRECTORY.parent
DIST_ROOT = REPOSITORY_ROOT / "apps" / "web" / "dist"
RELEASE_INPUT_FILES: tuple[tuple[str, Path], ...] = (
    ("package.json", REPOSITORY_ROOT / "package.json"),
    ("pnpm-lock.yaml", REPOSITORY_ROOT / "pnpm-lock.yaml"),
    ("apps/web/package.json", REPOSITORY_ROOT / "apps" / "web" / "package.json"),
    ("apps/api/requirements.txt", REPOSITORY_ROOT / "apps" / "api" / "requirements.txt"),
    ("apps/api/Dockerfile", REPOSITORY_ROOT / "apps" / "api" / "Dockerfile"),
)

VISITOR_FUNCTION_FILES = (
    "functions/api/visitor-messages.js",
    "functions/api/visitor-messages/pull.js",
    "functions/_lib/visitor-messages.js",
    "functions/schema.sql",
)
REQUIRED_ARTIFACTS = frozenset(
    {
        "index.html",
        "projects/index.html",
        "resume/index.html",
        "404.html",
        "login/index.html",
        "console/index.html",
        "console/files/index.html",
        "console/gallery/index.html",
        "console/mail/index.html",
        "console/kb/index.html",
        "enter/index.html",
        "robots.txt",
        "sitemap.xml",
        "favicon.svg",
    }
)
PUBLIC_HTML_ROUTES = {
    "index.html": "/",
    "projects/index.html": "/projects/",
    "resume/index.html": "/resume/",
}
PRIVATE_HTML_ARTIFACTS = frozenset(
    {
        "404.html",
        "login/index.html",
        "console/index.html",
        "console/files/index.html",
        "console/gallery/index.html",
        "console/mail/index.html",
        "console/kb/index.html",
    }
)
NARRATIVE_ENTRY_ARTIFACT = "enter/index.html"
NARRATIVE_ENTRY_FALLBACK_HREFS = frozenset({"/", "/login"})
RENDERED_TEXT_EXTENSIONS = frozenset({".html", ".htm", ".xml", ".txt", ".json", ".md", ".svg"})
SCANNED_TEXT_EXTENSIONS = RENDERED_TEXT_EXTENSIONS | frozenset({".js", ".mjs", ".cjs", ".css"})
DANGEROUS_EXTENSIONS = frozenset(
    {".key", ".pem", ".p12", ".pfx", ".crt", ".cer", ".db", ".sqlite", ".sqlite3", ".log", ".pyc", ".map"}
)
DANGEROUS_COMPONENTS = frozenset({"node_modules", "__pycache__", "cache", "logs", "models"})
CONTENT_PLACEHOLDERS = (
    "你的名字",
    "you@example.com",
    "star-isle.example.com",
    "github.com/yourname",
    "待填写",
    "暂无精选项目数据",
    "暂未配置仓库来源",
    "暂无曲目",
)
SPECIAL_USE_SUFFIXES = (
    "example",
    "invalid",
    "localhost",
    "test",
    "local",
    "onion",
    "alt",
    "home.arpa",
    "example.com",
    "example.net",
    "example.org",
)

_HOST_LABEL = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")
_LOCAL_URL_REFERENCE = re.compile(r"(?i)(?:(?:https?|wss?)://|//)[^\s\"'<>]+")
_ROBOTS_DIRECTIVE_SPLIT = re.compile(r"[, \t\r\n\f\x0b]+")
_SOURCE_MAP_REFERENCE = re.compile(r"sourceMappingURL", re.IGNORECASE)
_PEM_HEADER = re.compile(r"-----BEGIN [A-Z0-9][A-Z0-9 -]*-----")
_AWS_ACCESS_KEY = re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b")
_GITHUB_TOKEN = re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9_]{36,}|github_pat_[A-Za-z0-9_]{70,})\b")


@dataclass(frozen=True)
class GateResult:
    """A caller-safe result; no artifact content or paths are retained."""

    error_codes: frozenset[str]
    dist_file_count: int | None = None
    dist_fingerprint: str | None = None
    input_file_count: int | None = None
    input_inventory: str | None = None


class _MetadataParser(HTMLParser):
    """Collect only the metadata needed for deterministic release checks."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.title_parts: list[str] = []
        self._in_title = False
        self.metas: list[dict[str, str]] = []
        self.canonicals: list[str] = []
        self.anchor_hrefs: list[str] = []
        self.json_ld_documents: list[str] = []
        self._json_ld_chunks: list[str] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        normalized_tag = tag.casefold()
        attributes = {name.casefold(): (value or "") for name, value in attrs}
        if normalized_tag == "title":
            self._in_title = True
        elif normalized_tag == "meta":
            self.metas.append(attributes)
        elif normalized_tag == "link":
            relation = attributes.get("rel", "").casefold().split()
            if "canonical" in relation:
                self.canonicals.append(attributes.get("href", "").strip())
        elif normalized_tag == "a":
            self.anchor_hrefs.append(attributes.get("href", "").strip())
        elif normalized_tag == "script":
            script_type = attributes.get("type", "").casefold().split(";", 1)[0].strip()
            if script_type == "application/ld+json":
                self._json_ld_chunks = []

    def handle_endtag(self, tag: str) -> None:
        normalized_tag = tag.casefold()
        if normalized_tag == "title":
            self._in_title = False
        elif normalized_tag == "script" and self._json_ld_chunks is not None:
            self.json_ld_documents.append("".join(self._json_ld_chunks))
            self._json_ld_chunks = None

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self.title_parts.append(data)
        if self._json_ld_chunks is not None:
            self._json_ld_chunks.append(data)

    @property
    def title(self) -> str:
        return "".join(self.title_parts).strip()

    def values_for(self, key: str) -> list[str]:
        target = key.casefold()
        return [
            attributes.get("content", "").strip()
            for attributes in self.metas
            if attributes.get("name", "").casefold() == target
            or attributes.get("property", "").casefold() == target
        ]

    def noindex_semantic_count(self) -> int:
        count = 0
        for attributes in self.metas:
            if attributes.get("name", "").casefold() != "robots":
                continue
            directives = _ROBOTS_DIRECTIVE_SPLIT.split(attributes.get("content", "").casefold().strip())
            count += sum(directive in {"noindex", "none"} for directive in directives)
        return count


class _GateArgumentParser(argparse.ArgumentParser):
    """Do not echo arbitrary invalid command-line arguments."""

    def error(self, message: str) -> None:  # noqa: ARG002
        self.exit(2, "RELEASE_ARTIFACT_GATE: FAILED\nERROR_COUNT: 1\nARGUMENT_ERROR\n")


def canonicalize_public_site_url(raw_value: str | None) -> str | None:
    """Return the only supported production site origin, or ``None``.

    The intentionally narrow policy makes the generated Astro URLs compatible
    with the current root Caddy deployment without DNS, TLS, or network I/O.
    """

    if not isinstance(raw_value, str) or not raw_value or len(raw_value) > 2048:
        return None
    if not raw_value.isascii() or any(character.isspace() or ord(character) < 32 or ord(character) == 127 for character in raw_value):
        return None
    if not raw_value.startswith("https://") or "?" in raw_value or "#" in raw_value:
        return None
    try:
        parsed = urlsplit(raw_value)
        port = parsed.port
    except (TypeError, ValueError):
        return None
    if (
        parsed.scheme != "https"
        or not parsed.netloc
        or parsed.username is not None
        or parsed.password is not None
        or port is not None
        or parsed.query
        or parsed.fragment
        or parsed.path not in {"", "/"}
    ):
        return None

    host = parsed.hostname
    if not host or ":" in parsed.netloc:
        return None
    host = host.casefold()
    if host.endswith(".") or len(host) > 253:
        return None
    try:
        ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        return None
    if re.fullmatch(r"\d+(?:\.\d+){3}", host):
        return None

    labels = host.split(".")
    if len(labels) < 2 or any(not _HOST_LABEL.fullmatch(label) for label in labels):
        return None
    if any(label.startswith("xn--") for label in labels):
        return None
    if labels[-1].isdigit():
        return None
    if any(host == suffix or host.endswith(f".{suffix}") for suffix in SPECIAL_USE_SUFFIXES):
        return None
    return f"https://{host}/"


def _is_dangerous_relative_path(relative_path: Path) -> bool:
    parts = tuple(part.casefold() for part in relative_path.parts)
    if any(part.startswith(".") or part in DANGEROUS_COMPONENTS for part in parts):
        return True
    filename = relative_path.name.casefold()
    return filename.startswith(".env") or any(filename.endswith(extension) for extension in DANGEROUS_EXTENSIONS)


def _collect_dist_files(dist_root: Path, errors: set[str]) -> list[tuple[str, Path]]:
    """List only regular, non-link artifacts beneath the fixed dist root."""

    try:
        if has_link_or_reparse_component(dist_root):
            errors.add("DIST_LINK_OR_REPARSE")
            return []
        if not dist_root.is_dir():
            errors.add("DIST_ROOT_INVALID")
            return []
    except (HarnessSafetyError, OSError):
        errors.add("DIST_LINK_OR_REPARSE")
        return []

    artifacts: list[tuple[str, Path]] = []
    try:
        for current_root, directory_names, filenames in os.walk(dist_root, followlinks=False):
            current = Path(current_root)
            try:
                if is_link_or_reparse(current) or has_link_or_reparse_component(current):
                    errors.add("DIST_LINK_OR_REPARSE")
                    directory_names[:] = []
                    continue
            except (HarnessSafetyError, OSError):
                errors.add("DIST_LINK_OR_REPARSE")
                directory_names[:] = []
                continue

            safe_directories: list[str] = []
            for directory_name in directory_names:
                child = current / directory_name
                try:
                    linked = is_link_or_reparse(child) or has_link_or_reparse_component(child)
                except (HarnessSafetyError, OSError):
                    linked = True
                if linked:
                    errors.add("DIST_LINK_OR_REPARSE")
                    continue
                relative = child.relative_to(dist_root)
                if _is_dangerous_relative_path(relative):
                    errors.add("UNSAFE_ARTIFACT")
                    continue
                safe_directories.append(directory_name)
            directory_names[:] = safe_directories

            for filename in filenames:
                artifact = current / filename
                try:
                    linked = is_link_or_reparse(artifact) or has_link_or_reparse_component(artifact)
                except (HarnessSafetyError, OSError):
                    linked = True
                if linked:
                    errors.add("DIST_LINK_OR_REPARSE")
                    continue
                relative = artifact.relative_to(dist_root)
                if _is_dangerous_relative_path(relative):
                    errors.add("UNSAFE_ARTIFACT")
                    continue
                if not artifact.is_file():
                    errors.add("UNSAFE_ARTIFACT")
                    continue
                artifacts.append((relative.as_posix(), artifact))
    except OSError:
        errors.add("ARTIFACT_READ_INVALID")
    return artifacts


def _read_text(path: Path, errors: set[str]) -> str | None:
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        errors.add("ARTIFACT_READ_INVALID")
        return None


def _browser_ipv4_address(host: str) -> ipaddress.IPv4Address | None:
    """Parse the legacy numeric IPv4 forms browsers may treat as an address."""

    parts = host.split(".")
    if not 1 <= len(parts) <= 4 or any(not part for part in parts):
        return None
    numbers: list[int] = []
    for part in parts:
        normalized_part = part.casefold()
        if normalized_part.startswith("0x"):
            if len(normalized_part) == 2 or not re.fullmatch(r"0x[0-9a-f]+", normalized_part):
                return None
            number = int(normalized_part, 16)
        elif len(normalized_part) > 1 and normalized_part.startswith("0"):
            if not re.fullmatch(r"[0-7]+", normalized_part):
                return None
            number = int(normalized_part, 8)
        elif re.fullmatch(r"[0-9]+", normalized_part):
            number = int(normalized_part, 10)
        else:
            return None
        numbers.append(number)
    if any(number > 255 for number in numbers[:-1]):
        return None
    final_limit = (1 << (8 * (5 - len(numbers)))) - 1
    if numbers[-1] > final_limit:
        return None
    prefix_value = 0
    for number in numbers[:-1]:
        prefix_value = (prefix_value << 8) | number
    address_value = (prefix_value << (8 * (5 - len(numbers)))) | numbers[-1]
    try:
        return ipaddress.IPv4Address(address_value)
    except ipaddress.AddressValueError:
        return None


def _is_loopback_or_unspecified_host(host: str) -> bool:
    normalized_host = host.casefold().rstrip(".")
    if normalized_host == "localhost" or normalized_host.endswith(".localhost"):
        return True
    try:
        address = ipaddress.ip_address(normalized_host)
    except ValueError:
        address = _browser_ipv4_address(normalized_host)
    if address is None:
        return False
    if address.is_loopback or address.is_unspecified:
        return True
    mapped_address = getattr(address, "ipv4_mapped", None)
    return bool(mapped_address and (mapped_address.is_loopback or mapped_address.is_unspecified))


def _contains_loopback_url(text: str) -> bool:
    for match in _LOCAL_URL_REFERENCE.finditer(text):
        candidate = match.group(0).rstrip(".,;:!?)]}")
        try:
            host = urlsplit(candidate).hostname
        except ValueError:
            continue
        if not host:
            continue
        if _is_loopback_or_unspecified_host(host):
            return True
    return False


def _contains_workspace_path(text: str) -> bool:
    repository_path = str(REPOSITORY_ROOT)
    normalized_repository = repository_path.replace("\\", "/").casefold()
    normalized_text = text.replace("\\\\", "\\").replace("\\", "/").casefold()
    return bool(normalized_repository and normalized_repository in normalized_text)


def _scan_textual_artifact(relative_name: str, path: Path, errors: set[str]) -> None:
    suffix = path.suffix.casefold()
    if suffix not in SCANNED_TEXT_EXTENSIONS:
        return
    text = _read_text(path, errors)
    if text is None:
        return
    if _SOURCE_MAP_REFERENCE.search(text):
        errors.add("SOURCE_MAP_REFERENCE")
    if _PEM_HEADER.search(text) or _AWS_ACCESS_KEY.search(text) or _GITHUB_TOKEN.search(text):
        errors.add("SECRET_INDICATOR")
    if _contains_loopback_url(text):
        errors.add("LOCAL_URL")
    if _contains_workspace_path(text):
        errors.add("SOURCE_PATH_LEAK")
    if suffix in RENDERED_TEXT_EXTENSIONS:
        lowered = text.casefold()
        if "[object object]" in lowered:
            errors.add("OBJECT_RENDERING")
        if any(placeholder.casefold() in lowered for placeholder in CONTENT_PLACEHOLDERS):
            errors.add("CONTENT_PLACEHOLDER")


def _parse_html(path: Path, errors: set[str]) -> _MetadataParser | None:
    text = _read_text(path, errors)
    if text is None:
        return None
    parser = _MetadataParser()
    try:
        parser.feed(text)
        parser.close()
    except Exception:
        errors.add("HTML_METADATA_INVALID")
        return None
    return parser


def _has_one_nonempty(values: Sequence[str]) -> bool:
    return len(values) == 1 and bool(values[0].strip())


def _expected_page_url(site_url: str, route: str) -> str:
    return f"{site_url.rstrip('/')}{route}"


def _check_public_metadata(parser: _MetadataParser, expected_url: str, errors: set[str]) -> None:
    if not parser.title or not _has_one_nonempty(parser.values_for("description")) or parser.noindex_semantic_count():
        errors.add("PUBLIC_SEO_INVALID")
    if parser.canonicals != [expected_url] or parser.values_for("og:url") != [expected_url]:
        errors.add("PUBLIC_SEO_INVALID")
    for metadata_name in (
        "og:title",
        "og:description",
        "og:type",
        "og:site_name",
        "twitter:card",
        "twitter:title",
        "twitter:description",
    ):
        if not _has_one_nonempty(parser.values_for(metadata_name)):
            errors.add("PUBLIC_SEO_INVALID")
    if any("/api/v1" in value.casefold() for value in (*parser.canonicals, *parser.values_for("og:url"))):
        errors.add("SEO_API_URL")


def _check_noindex_metadata(parser: _MetadataParser, error_code: str, errors: set[str]) -> None:
    if parser.noindex_semantic_count() != 1 or parser.canonicals or parser.values_for("og:url"):
        errors.add(error_code)
    if any("/api/v1" in value.casefold() for value in (*parser.canonicals, *parser.values_for("og:url"))):
        errors.add("SEO_API_URL")


def _check_private_metadata(parser: _MetadataParser, errors: set[str]) -> None:
    _check_noindex_metadata(parser, "PRIVATE_SEO_INVALID", errors)


def _check_narrative_entry_metadata(parser: _MetadataParser, errors: set[str]) -> None:
    _check_noindex_metadata(parser, "ENTER_SEO_INVALID", errors)
    if not NARRATIVE_ENTRY_FALLBACK_HREFS.issubset(parser.anchor_hrefs):
        errors.add("ENTER_FALLBACK_INVALID")


def _iter_json_values(value: Any) -> Iterable[Any]:
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _iter_json_values(child)
    elif isinstance(value, list):
        for child in value:
            yield from _iter_json_values(child)


def _has_schema_type(value: Any, expected_type: str) -> bool:
    if isinstance(value, str):
        return value == expected_type
    if isinstance(value, list):
        return expected_type in value
    return False


def _json_url_matches_site(value: Any, site_url: str) -> bool:
    if not isinstance(value, str) or not value:
        return False
    try:
        candidate = urlsplit(value)
        expected = urlsplit(site_url)
        port = candidate.port
    except ValueError:
        return False
    return bool(
        candidate.scheme == "https"
        and candidate.hostname == expected.hostname
        and candidate.username is None
        and candidate.password is None
        and port is None
    )


def _json_url_is_api_v1(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    try:
        path = urlsplit(value).path.casefold()
    except ValueError:
        return False
    return bool(re.search(r"(?:^|/)api/v1(?:/|$)", path))


def _check_json_ld(parser: _MetadataParser, required_types: frozenset[str], site_url: str, errors: set[str]) -> None:
    nodes: list[Any] = []
    for document in parser.json_ld_documents:
        try:
            nodes.extend(_iter_json_values(json.loads(document)))
        except (TypeError, ValueError, json.JSONDecodeError):
            errors.add("JSON_LD_INVALID")
            return
    found_types = {
        expected_type
        for expected_type in required_types
        if any(isinstance(node, dict) and _has_schema_type(node.get("@type"), expected_type) for node in nodes)
    }
    if found_types != required_types:
        errors.add("JSON_LD_INVALID")
        return
    for node in nodes:
        if isinstance(node, dict) and "url" in node:
            if not _json_url_matches_site(node["url"], site_url):
                errors.add("JSON_LD_INVALID")
                return
            if _json_url_is_api_v1(node["url"]):
                errors.add("SEO_API_URL")
                return


def _check_robots(path: Path, site_url: str, errors: set[str]) -> None:
    text = _read_text(path, errors)
    if text is None:
        return
    if "/api/v1" in text.casefold():
        errors.add("SEO_API_URL")
    sitemap_values: list[str] = []
    has_user_agent = False
    has_allow = False
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or ":" not in stripped:
            continue
        directive, value = stripped.split(":", 1)
        key = directive.strip().casefold()
        normalized_value = value.strip()
        if key == "sitemap":
            sitemap_values.append(normalized_value)
        elif key == "user-agent" and normalized_value:
            has_user_agent = True
        elif key == "allow" and normalized_value:
            has_allow = True
    expected_sitemap = f"{site_url}sitemap.xml"
    if not has_user_agent or not has_allow or sitemap_values != [expected_sitemap]:
        errors.add("ROBOTS_INVALID")


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _check_sitemap(path: Path, site_url: str, errors: set[str]) -> None:
    text = _read_text(path, errors)
    if text is None:
        return
    if "/api/v1" in text.casefold():
        errors.add("SEO_API_URL")
    try:
        root = ElementTree.fromstring(text)
    except ElementTree.ParseError:
        errors.add("SITEMAP_INVALID")
        return
    if _local_name(root.tag) != "urlset":
        errors.add("SITEMAP_INVALID")
        return
    locations: list[str] = []
    for url_element in root:
        if _local_name(url_element.tag) != "url":
            errors.add("SITEMAP_INVALID")
            return
        loc_elements = [element for element in url_element if _local_name(element.tag) == "loc"]
        if len(loc_elements) != 1 or not (loc_elements[0].text or "").strip():
            errors.add("SITEMAP_INVALID")
            return
        locations.append((loc_elements[0].text or "").strip())
    expected_locations = {_expected_page_url(site_url, route) for route in PUBLIC_HTML_ROUTES.values()}
    if len(locations) != 3 or len(set(locations)) != 3 or set(locations) != expected_locations:
        errors.add("SITEMAP_INVALID")


def _fingerprint_files(files: Iterable[tuple[str, Path]], errors: set[str], error_code: str) -> tuple[int, str] | None:
    records: list[bytes] = []
    count = 0
    for relative_name, path in sorted(files, key=lambda item: item[0]):
        try:
            file_hash = hashlib.sha256()
            with path.open("rb") as artifact:
                for chunk in iter(lambda: artifact.read(1024 * 1024), b""):
                    file_hash.update(chunk)
            size = path.stat().st_size
        except OSError:
            errors.add(error_code)
            return None
        records.append(f"{relative_name}\0{size}\0{file_hash.hexdigest()}\n".encode("utf-8"))
        count += 1
    aggregate = hashlib.sha256()
    for record in records:
        aggregate.update(record)
    return count, aggregate.hexdigest()


def _release_input_fingerprint(errors: set[str]) -> tuple[int, str] | None:
    safe_inputs: list[tuple[str, Path]] = []
    for relative_name, path in RELEASE_INPUT_FILES:
        try:
            if has_link_or_reparse_component(path) or is_link_or_reparse(path) or not path.is_file():
                errors.add("RELEASE_INPUT_INVALID")
                return None
        except (HarnessSafetyError, OSError):
            errors.add("RELEASE_INPUT_INVALID")
            return None
        safe_inputs.append((relative_name, path))
    return _fingerprint_files(safe_inputs, errors, "RELEASE_INPUT_INVALID")


def _check_visitor_functions(errors: set[str]) -> None:
    """Pages Functions are deployed beside dist, so they belong to the release."""

    for relative_name in VISITOR_FUNCTION_FILES:
        path = REPOSITORY_ROOT / relative_name
        try:
            if has_link_or_reparse_component(path) or is_link_or_reparse(path) or not path.is_file():
                errors.add("VISITOR_FUNCTION_MISSING")
                return
            text = path.read_text(encoding="utf-8")
        except (HarnessSafetyError, OSError, UnicodeError):
            errors.add("VISITOR_FUNCTION_MISSING")
            return
        if not text.strip():
            errors.add("VISITOR_FUNCTION_MISSING")
            return
        if relative_name.startswith("functions/api/") and "onRequestPost" not in text:
            errors.add("VISITOR_FUNCTION_INVALID")
        if _contains_loopback_url(text) or "127.0.0.1" in text:
            errors.add("LOCAL_URL")
        if 'VISITOR_PULL_KEY="' in text or 'TURNSTILE_SECRET_KEY="' in text:
            errors.add("SECRET_INDICATOR")


_PRIVATE_DESK_MARKERS = (
    "/private/",
    "/api/v1/desk-todos",
    "/api/v1/desk-home",
    "desk-todo",
)


def _check_private_desk_absent(artifacts: list[tuple[str, Path]], errors: set[str]) -> None:
    """公开制品不能带上书桌总览、待办或私人路由。"""

    for _relative_name, path in artifacts:
        if path.suffix.casefold() not in SCANNED_TEXT_EXTENSIONS:
            continue
        text = _read_text(path, errors)
        if text is None:
            continue
        if any(marker in text for marker in _PRIVATE_DESK_MARKERS):
            errors.add("PRIVATE_DESK_LEAK")


def _check_visitor_secret_leak(artifacts: list[tuple[str, Path]], errors: set[str]) -> None:
    for _relative_name, path in artifacts:
        if path.suffix.casefold() not in SCANNED_TEXT_EXTENSIONS:
            continue
        text = _read_text(path, errors)
        if text is None:
            continue
        if "VISITOR_PULL_KEY" in text or "TURNSTILE_SECRET" in text or "siteverify" in text:
            errors.add("VISITOR_SECRET_LEAK")


def evaluate_gate(raw_site_url: str | None, *, config_only: bool = False) -> GateResult:
    """Evaluate the fixed production configuration and, unless requested, dist."""

    errors: set[str] = set()
    site_url = canonicalize_public_site_url(raw_site_url)
    if site_url is None:
        errors.add("CONFIG_INVALID_URL")
    if config_only:
        return GateResult(frozenset(errors))

    artifacts = _collect_dist_files(DIST_ROOT, errors)
    artifact_names = {relative_name for relative_name, _ in artifacts}
    if not REQUIRED_ARTIFACTS.issubset(artifact_names):
        errors.add("REQUIRED_FILE_MISSING")
    for relative_name, path in artifacts:
        _scan_textual_artifact(relative_name, path, errors)

    if site_url is not None:
        for relative_name, route in PUBLIC_HTML_ROUTES.items():
            artifact = DIST_ROOT / Path(relative_name)
            if relative_name not in artifact_names:
                continue
            parser = _parse_html(artifact, errors)
            if parser is None:
                continue
            _check_public_metadata(parser, _expected_page_url(site_url, route), errors)
            if relative_name == "index.html":
                _check_json_ld(parser, frozenset({"Person", "WebSite"}), site_url, errors)
            elif relative_name == "resume/index.html":
                _check_json_ld(parser, frozenset({"Person"}), site_url, errors)
        for relative_name in PRIVATE_HTML_ARTIFACTS:
            artifact = DIST_ROOT / Path(relative_name)
            if relative_name not in artifact_names:
                continue
            parser = _parse_html(artifact, errors)
            if parser is not None:
                _check_private_metadata(parser, errors)
        entry_artifact = DIST_ROOT / Path(NARRATIVE_ENTRY_ARTIFACT)
        if NARRATIVE_ENTRY_ARTIFACT in artifact_names:
            parser = _parse_html(entry_artifact, errors)
            if parser is not None:
                _check_narrative_entry_metadata(parser, errors)
        robots_path = DIST_ROOT / "robots.txt"
        if "robots.txt" in artifact_names:
            _check_robots(robots_path, site_url, errors)
        sitemap_path = DIST_ROOT / "sitemap.xml"
        if "sitemap.xml" in artifact_names:
            _check_sitemap(sitemap_path, site_url, errors)

    _check_visitor_functions(errors)
    _check_visitor_secret_leak(artifacts, errors)
    _check_private_desk_absent(artifacts, errors)

    if errors:
        return GateResult(frozenset(errors))
    dist_fingerprint = _fingerprint_files(artifacts, errors, "ARTIFACT_READ_INVALID")
    input_inventory = _release_input_fingerprint(errors)
    if errors or dist_fingerprint is None or input_inventory is None:
        return GateResult(frozenset(errors or {"INTERNAL_ERROR"}))
    return GateResult(
        frozenset(),
        dist_file_count=dist_fingerprint[0],
        dist_fingerprint=dist_fingerprint[1],
        input_file_count=input_inventory[0],
        input_inventory=input_inventory[1],
    )


def _write_failure(error_codes: Iterable[str]) -> None:
    codes = sorted(set(error_codes))
    print("RELEASE_ARTIFACT_GATE: FAILED", file=sys.stderr)
    print(f"ERROR_COUNT: {len(codes)}", file=sys.stderr)
    for error_code in codes:
        print(error_code, file=sys.stderr)


def _write_success(result: GateResult, *, config_only: bool) -> None:
    print("RELEASE_ARTIFACT_GATE: PASSED")
    if config_only:
        print("MODE: CONFIG_ONLY")
        print("CONFIGURATION: VALID")
        return
    print("MODE: FULL")
    print(f"DIST_FINGERPRINT_FILE_COUNT: {result.dist_file_count}")
    print(f"DIST_FINGERPRINT_SHA256: {result.dist_fingerprint}")
    print(f"RELEASE_INPUT_INVENTORY_FILE_COUNT: {result.input_file_count}")
    print(f"RELEASE_INPUT_INVENTORY_SHA256: {result.input_inventory}")


def _argument_parser() -> argparse.ArgumentParser:
    parser = _GateArgumentParser(prog="release_artifact_gate.py", add_help=True, allow_abbrev=False)
    parser.add_argument("--config-only", action="store_true", help="validate only PUBLIC_SITE_URL before building")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _argument_parser().parse_args(argv)
    try:
        result = evaluate_gate(os.environ.get("PUBLIC_SITE_URL"), config_only=args.config_only)
    except Exception:
        _write_failure(("INTERNAL_ERROR",))
        return 1
    if result.error_codes:
        _write_failure(result.error_codes)
        return 1
    _write_success(result, config_only=args.config_only)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
