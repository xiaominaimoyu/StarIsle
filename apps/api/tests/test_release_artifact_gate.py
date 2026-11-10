"""Coverage for the static, fail-closed production release artifact gate."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType

import pytest


_PROJECT_ROOT = Path(__file__).resolve().parents[3]
_GATE_PATH = _PROJECT_ROOT / "scripts" / "release_artifact_gate.py"


def _load_gate_module() -> ModuleType:
    module_name = "starisle_release_artifact_gate_test"
    spec = importlib.util.spec_from_file_location(module_name, _GATE_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _public_page(site_url: str, route: str, json_ld: object | None = None) -> str:
    expected_url = f"{site_url.rstrip('/')}{route}"
    document = f"""<!doctype html>
<html><head>
<title>Release candidate</title>
<meta name="description" content="A reviewed public page">
<link rel="canonical" href="{expected_url}">
<meta property="og:url" content="{expected_url}">
<meta property="og:title" content="Release candidate">
<meta property="og:description" content="A reviewed public page">
<meta property="og:type" content="website">
<meta property="og:site_name" content="Release candidate">
<meta name="twitter:card" content="summary">
<meta name="twitter:title" content="Release candidate">
<meta name="twitter:description" content="A reviewed public page">
"""
    if json_ld is not None:
        document += f'<script type="application/ld+json">{json.dumps(json_ld)}</script>\n'
    return document + "</head><body>Reviewed public content</body></html>\n"


def _private_page() -> str:
    return """<!doctype html>
<html><head><meta name="robots" content="noindex"></head>
<body>Private space</body></html>
"""


def _narrative_entry_page() -> str:
    return """<!doctype html>
<html><head><meta name="robots" content="noindex"></head>
<body><a href="/">Continue to the public site</a><a href="/login">Sign in</a></body></html>
"""


def _sitemap(site_url: str, locations: list[str] | None = None) -> str:
    expected_locations = locations or [
        f"{site_url}",
        f"{site_url}projects/",
        f"{site_url}resume/",
    ]
    entries = "".join(f"<url><loc>{location}</loc></url>" for location in expected_locations)
    return f'<?xml version="1.0" encoding="UTF-8"?><urlset xmlns="https://www.sitemaps.org/schemas/sitemap/0.9">{entries}</urlset>'


def _write_complete_dist(dist_root: Path, site_url: str) -> None:
    index_json_ld = {
        "@context": "https://schema.org",
        "@graph": [
            {"@type": "Person", "url": site_url},
            {"@type": "WebSite", "url": site_url},
        ],
    }
    resume_json_ld = {"@context": "https://schema.org", "@type": "Person", "url": f"{site_url}resume/"}
    _write(dist_root / "index.html", _public_page(site_url, "/", index_json_ld))
    _write(dist_root / "projects" / "index.html", _public_page(site_url, "/projects/"))
    _write(dist_root / "resume" / "index.html", _public_page(site_url, "/resume/", resume_json_ld))
    for relative_name in (
        "404.html",
        "login/index.html",
        "console/index.html",
        "console/files/index.html",
        "console/gallery/index.html",
        "console/mail/index.html",
        "console/kb/index.html",
    ):
        _write(dist_root / relative_name, _private_page())
    _write(dist_root / "enter" / "index.html", _narrative_entry_page())
    _write(dist_root / "robots.txt", f"User-agent: *\nAllow: /\nSitemap: {site_url}sitemap.xml\n")
    _write(dist_root / "sitemap.xml", _sitemap(site_url))
    _write(dist_root / "favicon.svg", '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1 1"/>')


def _write_release_inputs(root: Path) -> tuple[tuple[str, Path], ...]:
    files = (
        ("package.json", root / "package.json"),
        ("pnpm-lock.yaml", root / "pnpm-lock.yaml"),
        ("apps/web/package.json", root / "apps" / "web" / "package.json"),
        ("apps/api/requirements.txt", root / "apps" / "api" / "requirements.txt"),
        ("apps/api/Dockerfile", root / "apps" / "api" / "Dockerfile"),
    )
    for relative_name, path in files:
        _write(path, f"release-input:{relative_name}\n")
    return files


@pytest.fixture
def release_fixture(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[ModuleType, Path, Path]:
    gate = _load_gate_module()
    site_url = "https://candidate.dev/"
    dist_root = tmp_path / "dist"
    source_root = tmp_path / "source-root"
    source_root.mkdir()
    _write_complete_dist(dist_root, site_url)
    for relative_name in (
        "functions/api/visitor-messages.js",
        "functions/api/visitor-messages/pull.js",
    ):
        _write(source_root / relative_name, "export async function onRequestPost(){return new Response(null,{status:204})}\n")
    _write(source_root / "functions" / "_lib" / "visitor-messages.js", "export const visitorMessages = true;\n")
    _write(source_root / "functions" / "schema.sql", "CREATE TABLE visitor_message (id TEXT PRIMARY KEY);\n")
    monkeypatch.setattr(gate, "REPOSITORY_ROOT", source_root)
    monkeypatch.setattr(gate, "DIST_ROOT", dist_root)
    monkeypatch.setattr(gate, "RELEASE_INPUT_FILES", _write_release_inputs(tmp_path / "inputs"))
    monkeypatch.setenv("PUBLIC_SITE_URL", site_url)
    return gate, dist_root, source_root


def _result(gate: ModuleType):
    return gate.evaluate_gate("https://candidate.dev/")


def test_complete_fixture_passes_and_reports_fingerprints(release_fixture: tuple[ModuleType, Path, Path]):
    gate, _, _ = release_fixture

    result = _result(gate)

    assert result.error_codes == frozenset()
    assert result.dist_file_count == 14
    assert result.dist_fingerprint is not None and len(result.dist_fingerprint) == 64
    assert result.input_file_count == 5
    assert result.input_inventory is not None and len(result.input_inventory) == 64


@pytest.mark.parametrize(
    "raw_url",
    (
        None,
        "",
        "http://candidate.dev/",
        "https://user:password@candidate.dev/",
        "https://candidate.dev/?query=value",
        "https://candidate.dev/#fragment",
        "https://candidate.dev:443/",
        "https://candidate.dev:8443/",
        "https://candidate.dev/release/",
        "https://candidate.dev /",
        "https://candidate.dev/\x01",
        "\t",
        "https://localhost/",
        "https://sub.localhost/",
        "https://127.0.0.1/",
        "https://127.1/",
        "https://127.0.1/",
        "https://0x7f.1/",
        "https://192.168.1.8/",
        "https://169.254.10.9/",
        "https://[::1]/",
        "https://münich.dev/",
        "https://xn--mnich-kva.dev/",
        "https://site.xn--p1ai/",
        "https://singlelabel/",
        "https://bad_label.dev/",
        "https://example.com/",
        "https://branch.example.com/",
        "https://candidate.example/",
        "https://example.net/",
        "https://example.org/",
        "https://candidate.local/",
        "https://candidate.onion/",
        "https://candidate.home.arpa/",
        "https://candidate.alt/",
        "https://candidate.invalid/",
        "https://candidate.test/",
    ),
)
def test_url_policy_rejects_non_production_inputs(raw_url: str | None):
    gate = _load_gate_module()

    assert gate.canonicalize_public_site_url(raw_url) is None


def test_url_policy_normalizes_only_the_allowed_root_origin():
    gate = _load_gate_module()

    assert gate.canonicalize_public_site_url("https://Candidate.Dev") == "https://candidate.dev/"


def test_missing_required_artifact_fails(release_fixture: tuple[ModuleType, Path, Path]):
    gate, dist_root, _ = release_fixture
    (dist_root / "resume" / "index.html").unlink()

    assert "REQUIRED_FILE_MISSING" in _result(gate).error_codes


def test_missing_narrative_entry_artifact_fails(release_fixture: tuple[ModuleType, Path, Path]):
    gate, dist_root, _ = release_fixture
    (dist_root / "enter" / "index.html").unlink()

    assert "REQUIRED_FILE_MISSING" in _result(gate).error_codes


@pytest.mark.parametrize(
    ("needle", "replacement"),
    (
        ('<link rel="canonical" href="https://candidate.dev/">', ""),
        ('content="https://candidate.dev/"', 'content="https://candidate.dev/projects/"'),
    ),
)
def test_public_canonical_and_og_url_are_strict(
    release_fixture: tuple[ModuleType, Path, Path], needle: str, replacement: str
):
    gate, dist_root, _ = release_fixture
    index = dist_root / "index.html"
    _write(index, index.read_text(encoding="utf-8").replace(needle, replacement, 1))

    assert "PUBLIC_SEO_INVALID" in _result(gate).error_codes


@pytest.mark.parametrize("robots_content", ("noindex", "none", "noindex nofollow"))
def test_public_page_cannot_include_noindex_semantics(
    release_fixture: tuple[ModuleType, Path, Path], robots_content: str
):
    gate, dist_root, _ = release_fixture
    index = dist_root / "index.html"
    _write(
        index,
        index.read_text(encoding="utf-8").replace(
            "</head>", f'<meta name="robots" content="{robots_content}"></head>'
        ),
    )

    assert "PUBLIC_SEO_INVALID" in _result(gate).error_codes


def test_private_page_cannot_publish_canonical_or_lose_noindex(
    release_fixture: tuple[ModuleType, Path, Path]
):
    gate, dist_root, _ = release_fixture
    login = dist_root / "login" / "index.html"
    _write(login, login.read_text(encoding="utf-8").replace("</head>", '<link rel="canonical" href="https://candidate.dev/"> </head>'))

    assert "PRIVATE_SEO_INVALID" in _result(gate).error_codes


@pytest.mark.parametrize(
    "replacement",
    (
        '<meta name="robots" content="index">',
        '<meta name="robots" content="noindex"><meta property="og:url" content="https://candidate.dev/">',
    ),
)
def test_private_page_requires_noindex_and_rejects_og_url(
    release_fixture: tuple[ModuleType, Path, Path], replacement: str
):
    gate, dist_root, _ = release_fixture
    login = dist_root / "login" / "index.html"
    _write(login, login.read_text(encoding="utf-8").replace('<meta name="robots" content="noindex">', replacement))

    assert "PRIVATE_SEO_INVALID" in _result(gate).error_codes


@pytest.mark.parametrize(
    "replacement",
    (
        '<meta name="robots" content="index">',
        '<meta name="robots" content="noindex"><link rel="canonical" href="https://candidate.dev/">',
        '<meta name="robots" content="noindex"><meta property="og:url" content="https://candidate.dev/">',
    ),
)
def test_narrative_entry_requires_noindex_without_canonical_or_og_url(
    release_fixture: tuple[ModuleType, Path, Path], replacement: str
):
    gate, dist_root, _ = release_fixture
    entry = dist_root / "enter" / "index.html"
    _write(entry, entry.read_text(encoding="utf-8").replace('<meta name="robots" content="noindex">', replacement))

    assert "ENTER_SEO_INVALID" in _result(gate).error_codes


@pytest.mark.parametrize("fallback_href", ("/", "/login"))
def test_narrative_entry_requires_both_static_fallback_links(
    release_fixture: tuple[ModuleType, Path, Path], fallback_href: str
):
    gate, dist_root, _ = release_fixture
    entry = dist_root / "enter" / "index.html"
    _write(entry, entry.read_text(encoding="utf-8").replace(f'href="{fallback_href}"', 'href="/missing"', 1))

    assert "ENTER_FALLBACK_INVALID" in _result(gate).error_codes


@pytest.mark.parametrize(
    "robots",
    (
        "User-agent: *\nAllow: /\n",
        "User-agent: *\nAllow: /\nSitemap: https://candidate.dev/not-sitemap.xml\n",
        "User-agent: *\nAllow: /\nSitemap: https://candidate.dev/sitemap.xml\nSitemap: https://candidate.dev/sitemap.xml\n",
    ),
)
def test_robots_requires_the_single_expected_sitemap(
    release_fixture: tuple[ModuleType, Path, Path], robots: str
):
    gate, dist_root, _ = release_fixture
    _write(dist_root / "robots.txt", robots)

    assert "ROBOTS_INVALID" in _result(gate).error_codes


@pytest.mark.parametrize(
    "sitemap",
    (
        _sitemap("https://candidate.dev/", ["https://candidate.dev/", "https://candidate.dev/projects/"]),
        _sitemap(
            "https://candidate.dev/",
            [
                "https://candidate.dev/",
                "https://candidate.dev/projects/",
                "https://candidate.dev/resume/",
                "https://candidate.dev/extra/",
            ],
        ),
        _sitemap(
            "https://candidate.dev/",
            [
                "https://candidate.dev/",
                "https://candidate.dev/projects/",
                "https://candidate.dev/projects/",
            ],
        ),
        "<urlset><url><loc>not closed</urlset>",
    ),
)
def test_sitemap_requires_exactly_the_three_public_locations(
    release_fixture: tuple[ModuleType, Path, Path], sitemap: str
):
    gate, dist_root, _ = release_fixture
    _write(dist_root / "sitemap.xml", sitemap)

    assert "SITEMAP_INVALID" in _result(gate).error_codes


@pytest.mark.parametrize(
    "json_ld",
    (
        {"@context": "https://schema.org", "@type": "WebSite", "url": "https://candidate.dev/"},
        {"@context": "https://schema.org", "@type": "Person", "url": "https://outside.dev/"},
    ),
)
def test_json_ld_requires_types_and_site_domain(
    release_fixture: tuple[ModuleType, Path, Path], json_ld: object
):
    gate, dist_root, _ = release_fixture
    _write(dist_root / "index.html", _public_page("https://candidate.dev/", "/", json_ld))

    assert "JSON_LD_INVALID" in _result(gate).error_codes


def test_json_ld_api_v1_url_is_a_seo_failure_without_v10_false_positive(
    release_fixture: tuple[ModuleType, Path, Path]
):
    gate, dist_root, _ = release_fixture
    api_json_ld = {
        "@context": "https://schema.org",
        "@graph": [
            {"@type": "Person", "url": "https://candidate.dev/api/v1/me"},
            {"@type": "WebSite", "url": "https://candidate.dev/"},
        ],
    }
    _write(dist_root / "index.html", _public_page("https://candidate.dev/", "/", api_json_ld))

    assert "SEO_API_URL" in _result(gate).error_codes
    v10_json_ld = {
        "@context": "https://schema.org",
        "@graph": [
            {"@type": "Person", "url": "https://candidate.dev/api/v10/me"},
            {"@type": "WebSite", "url": "https://candidate.dev/"},
        ],
    }
    _write(dist_root / "index.html", _public_page("https://candidate.dev/", "/", v10_json_ld))

    assert "SEO_API_URL" not in _result(gate).error_codes


def test_invalid_json_ld_fails(release_fixture: tuple[ModuleType, Path, Path]):
    gate, dist_root, _ = release_fixture
    index = dist_root / "index.html"
    _write(index, index.read_text(encoding="utf-8").replace("</head>", '<script type="application/ld+json">{bad json}</script></head>'))

    assert "JSON_LD_INVALID" in _result(gate).error_codes


@pytest.mark.parametrize(
    ("relative_name", "content", "expected_code"),
    (
        ("visible.html", "<p>暂无曲目</p>", "CONTENT_PLACEHOLDER"),
        ("visible.html", "<p>[object Object]</p>", "OBJECT_RENDERING"),
        ("visible.txt", "https://localhost:4321/private", "LOCAL_URL"),
        ("visible.txt", "//localhost/path", "LOCAL_URL"),
        ("visible.txt", "ws://127.0.0.1/socket", "LOCAL_URL"),
        ("visible.txt", "wss://localhost/socket", "LOCAL_URL"),
        ("visible.txt", "http://127.1", "LOCAL_URL"),
        ("visible.txt", "http://127.0.1", "LOCAL_URL"),
        ("visible.txt", "http://0x7f.1", "LOCAL_URL"),
        ("visible.txt", "http://2130706433", "LOCAL_URL"),
        ("visible.txt", "http://[::ffff:127.0.0.1]/", "LOCAL_URL"),
        ("bundle.js", "//# sourceMappingURL=client.js.map", "SOURCE_MAP_REFERENCE"),
        ("bundle.js", "-----BEGIN PRIVATE KEY-----", "SECRET_INDICATOR"),
        ("bundle.js", "AKIAAAAAAAAAAAAAAAAA", "SECRET_INDICATOR"),
        ("bundle.js", "const token = 'ghp_AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA';", "SECRET_INDICATOR"),
        ("bundle.js", "github_pat_" + "A" * 70, "SECRET_INDICATOR"),
    ),
)
def test_text_artifacts_reject_visible_placeholders_and_strong_indicators(
    release_fixture: tuple[ModuleType, Path, Path], relative_name: str, content: str, expected_code: str
):
    gate, dist_root, _ = release_fixture
    _write(dist_root / relative_name, content)

    assert expected_code in _result(gate).error_codes


def test_workspace_absolute_path_is_redacted_as_a_fixed_failure(
    release_fixture: tuple[ModuleType, Path, Path]
):
    gate, dist_root, source_root = release_fixture
    _write(dist_root / "leak.txt", str(source_root))

    assert "SOURCE_PATH_LEAK" in _result(gate).error_codes


@pytest.mark.parametrize(
    "relative_name",
    (
        ".env.production",
        ".env",
        ".hidden/entry.js",
        "private.key",
        "private.pem",
        "certificate.p12",
        "certificate.pfx",
        "certificate.crt",
        "certificate.cer",
        "runtime.db",
        "runtime.sqlite",
        "runtime.sqlite3",
        "events.log",
        "cache/entry.js",
        "node_modules/entry.js",
        "__pycache__/entry.pyc",
        "logs/entry.txt",
        "models/entry.bin",
        "bytecode.pyc",
        "bundle.map",
    ),
)
def test_dangerous_artifact_names_extensions_and_components_fail(
    release_fixture: tuple[ModuleType, Path, Path], relative_name: str
):
    gate, dist_root, _ = release_fixture
    _write(dist_root / relative_name, "not a release artifact")

    assert "UNSAFE_ARTIFACT" in _result(gate).error_codes


def test_symlink_or_reparse_artifact_fails_when_supported(
    release_fixture: tuple[ModuleType, Path, Path], tmp_path: Path
):
    gate, dist_root, _ = release_fixture
    target = tmp_path / "symlink-target.js"
    _write(target, "export {};\n")
    link = dist_root / "linked.js"
    try:
        link.symlink_to(target)
    except OSError:
        pytest.skip("the current platform does not permit test symlinks")

    assert "DIST_LINK_OR_REPARSE" in _result(gate).error_codes


def test_private_bundle_api_variable_name_and_js_diagnostic_remain_allowed(
    release_fixture: tuple[ModuleType, Path, Path]
):
    gate, dist_root, _ = release_fixture
    _write(
        dist_root / "private-bundle.js",
        "const endpoint = '/api/v1/files'; const variableName = 'GITHUB_TOKEN'; const diagnostic = '[object Object]'; const localPattern = /(?:https?|wss?):\\/\\/127\\.0\\.0\\.1/; const protocolPattern = /\\/\\/localhost/;",
    )

    assert _result(gate).error_codes == frozenset()


def test_fingerprint_is_stable_and_changes_with_artifact_content(
    release_fixture: tuple[ModuleType, Path, Path]
):
    gate, dist_root, _ = release_fixture

    first = _result(gate)
    second = _result(gate)
    _write(dist_root / "asset.js", "export const release = 1;\n")
    third = _result(gate)

    assert first.dist_fingerprint == second.dist_fingerprint
    assert first.dist_fingerprint != third.dist_fingerprint


def test_release_input_inventory_is_stable_and_tracks_input_content(
    release_fixture: tuple[ModuleType, Path, Path]
):
    gate, _, _ = release_fixture

    first = _result(gate)
    second = _result(gate)
    _, changed_input = gate.RELEASE_INPUT_FILES[0]
    _write(changed_input, "release-input:changed\n")
    third = _result(gate)

    assert first.input_inventory == second.input_inventory
    assert first.input_inventory != third.input_inventory


def test_cli_output_never_echoes_url_paths_or_secret_canaries(
    release_fixture: tuple[ModuleType, Path, Path], monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    gate, dist_root, source_root = release_fixture
    secret = "ghp_" + "A" * 36
    canary_url = f"https://user:{secret}@candidate.dev/?secret={secret}"
    canary_path = source_root / secret
    _write(dist_root / "canary.txt", f"{secret}\n{canary_path}")
    monkeypatch.setenv("PUBLIC_SITE_URL", canary_url)

    assert gate.main([]) == 1
    captured = capsys.readouterr()
    output = captured.out + captured.err

    assert "CONFIG_INVALID_URL" in output
    for forbidden in (secret, canary_url, str(canary_path)):
        assert forbidden not in output


def test_cli_internal_error_is_fixed_and_redacted(
    release_fixture: tuple[ModuleType, Path, Path], monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    gate, _, _ = release_fixture
    secret = "ghp_" + "B" * 36

    def raise_canary(*args: object, **kwargs: object) -> object:
        raise RuntimeError(secret)

    monkeypatch.setattr(gate, "evaluate_gate", raise_canary)

    assert gate.main([]) == 1
    captured = capsys.readouterr()

    assert captured.out == ""
    assert secret not in captured.err
    assert captured.err == "RELEASE_ARTIFACT_GATE: FAILED\nERROR_COUNT: 1\nINTERNAL_ERROR\n"


def test_cli_argument_error_is_redacted(release_fixture: tuple[ModuleType, Path, Path], capsys: pytest.CaptureFixture[str]):
    gate, _, _ = release_fixture
    secret = "PRIVATE_RELEASE_ARGUMENT"

    with pytest.raises(SystemExit) as exit_status:
        gate.main([f"--unknown={secret}"])
    captured = capsys.readouterr()

    assert exit_status.value.code == 2
    assert captured.out == ""
    assert secret not in captured.err
    assert captured.err == "RELEASE_ARTIFACT_GATE: FAILED\nERROR_COUNT: 1\nARGUMENT_ERROR\n"


def test_cli_does_not_accept_abbreviated_gate_options(
    release_fixture: tuple[ModuleType, Path, Path], capsys: pytest.CaptureFixture[str]
):
    gate, _, _ = release_fixture

    with pytest.raises(SystemExit) as exit_status:
        gate.main(["--config"])
    captured = capsys.readouterr()

    assert exit_status.value.code == 2
    assert captured.out == ""
    assert captured.err == "RELEASE_ARTIFACT_GATE: FAILED\nERROR_COUNT: 1\nARGUMENT_ERROR\n"


def test_config_only_never_requires_or_reads_dist(
    release_fixture: tuple[ModuleType, Path, Path], tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    gate, _, _ = release_fixture
    monkeypatch.setattr(gate, "DIST_ROOT", tmp_path / "missing-dist")
    monkeypatch.setattr(gate, "RELEASE_INPUT_FILES", (("missing", tmp_path / "missing-input"),))

    assert gate.main(["--config-only"]) == 0
    captured = capsys.readouterr()

    assert captured.err == ""
    assert captured.out == "RELEASE_ARTIFACT_GATE: PASSED\nMODE: CONFIG_ONLY\nCONFIGURATION: VALID\n"


def test_config_only_reports_only_the_configuration_failure(
    release_fixture: tuple[ModuleType, Path, Path], tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    gate, _, _ = release_fixture
    monkeypatch.setattr(gate, "DIST_ROOT", tmp_path / "missing-dist")
    monkeypatch.delenv("PUBLIC_SITE_URL", raising=False)

    assert gate.main(["--config-only"]) == 1
    captured = capsys.readouterr()

    assert captured.out == ""
    assert captured.err == "RELEASE_ARTIFACT_GATE: FAILED\nERROR_COUNT: 1\nCONFIG_INVALID_URL\n"


def test_missing_visitor_function_fails_the_gate(release_fixture: tuple[ModuleType, Path, Path]):
    gate, _, source_root = release_fixture
    (source_root / "functions" / "schema.sql").unlink()

    assert "VISITOR_FUNCTION_MISSING" in _result(gate).error_codes


def test_public_dist_cannot_carry_the_private_dashboard(release_fixture: tuple[ModuleType, Path, Path]):
    gate, dist_root, _ = release_fixture
    _write(dist_root / "leak.js", "fetch('/api/v1/desk-todos');\n")

    assert "PRIVATE_DESK_LEAK" in _result(gate).error_codes


def test_public_dist_cannot_carry_visitor_secrets(release_fixture: tuple[ModuleType, Path, Path]):
    gate, dist_root, _ = release_fixture
    _write(dist_root / "leak.js", "const key = 'VISITOR_PULL_KEY';\n")

    assert "VISITOR_SECRET_LEAK" in _result(gate).error_codes
