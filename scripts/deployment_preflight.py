"""Offline Docker Compose/Caddy topology preflight for StarIsle.

This runner deliberately invokes only ``docker compose ... config`` against
synthetic system-temporary inputs.  It never builds, starts, pulls, creates or
stops containers, and it never reads the development API env file or repository
runtime data.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any


class PreflightError(RuntimeError):
    """Stable, non-sensitive preflight failure."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _fail(code: str) -> None:
    raise PreflightError(code)


def _require(condition: bool, code: str) -> None:
    if not condition:
        _fail(code)


def _repository_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _same_path(left: Path | str, right: Path | str) -> bool:
    return os.path.normcase(os.path.normpath(str(left))) == os.path.normcase(os.path.normpath(str(right)))


def _inside(path: Path, parent: Path) -> bool:
    try:
        common = os.path.commonpath((os.path.normcase(str(path)), os.path.normcase(str(parent))))
    except ValueError:
        return False
    return os.path.normcase(common) == os.path.normcase(str(parent))


def _controlled_subprocess_env(
    temporary_root: Path, interpolation_values: Mapping[str, str]
) -> dict[str, str]:
    """Keep Docker's host runtime intact while isolating every project input."""

    environment = os.environ.copy()
    blocked_exact = {
        "ACME_EMAIL",
        "AUTH_JWT_ACTIVE_KID",
        "AUTH_JWT_SIGNING_KEY",
        "AUTH_JWT_PREVIOUS_KEYS_JSON",
        "ADMIN_BOOTSTRAP_PASSWORD_HASH",
        "AUDIT_RETENTION_DAYS",
        "AUDIT_SOFT_CAP_ROWS",
        "CADDY_HOST_PORT",
        "CADDY_HTTP_HOST_PORT",
        "CADDY_HTTPS_HOST_PORT",
        "CHAT_MODEL_FILE",
        "CHAT_MODEL_ID",
        "COMPOSE_FILE",
        "COMPOSE_PROFILES",
        "COOKIE_SECURE",
        "DATA_HOST_ROOT",
        "EMBEDDING_DIM",
        "EMBEDDING_MODEL_ID",
        "EMBED_MODEL_FILE",
        "IDEMPOTENCY_RETENTION_DAYS",
        "MAIL_HMAC_KEY",
        "MAIL_HMAC_ACTIVE_KID",
        "MAIL_HMAC_PREVIOUS_KEYS_JSON",
        "MAIL_RETENTION_DAYS",
        "MODEL_HOST_ROOT",
        "DOCKER_CONFIG",
        "DOCKER_CONTEXT",
        "DOCKER_HOST",
        "STAR_ISLE_API_ENV_FILE",
        "STAR_ISLE_SITE_ADDRESS",
    }
    blocked_casefold = {key.casefold() for key in blocked_exact}
    blocked_prefixes = ("compose_", "star_isle_")
    for key in tuple(environment):
        normalized_key = key.casefold()
        if normalized_key in blocked_casefold or normalized_key.startswith(blocked_prefixes):
            environment.pop(key, None)
    environment.update(interpolation_values)
    environment.update(
        {
            "COMPOSE_DISABLE_ENV_FILE": "1",
            "COMPOSE_PROJECT_NAME": "starisle-preflight",
            "DOCKER_CONFIG": str(temporary_root / "docker-config"),
        }
    )
    return environment


def _write_controlled_env(path: Path, values: Mapping[str, str]) -> None:
    path.write_text("".join(f"{key}={value}\n" for key, value in values.items()), encoding="utf-8")


def _run_compose_config(
    docker: str,
    compose_files: Sequence[Path],
    profile: str | None,
    controlled_env_file: Path,
    controlled_environment: Mapping[str, str],
) -> dict[str, Any]:
    command = [docker, "compose", "--env-file", str(controlled_env_file)]
    for compose_file in compose_files:
        command.extend(("-f", str(compose_file)))
    if profile:
        command.extend(("--profile", profile))
    command.extend(("config", "--format", "json", "--no-env-resolution"))
    try:
        result = subprocess.run(
            command,
            cwd=str(_repository_root()),
            env=dict(controlled_environment),
            capture_output=True,
            check=False,
            text=True,
            timeout=30,
        )
    except (FileNotFoundError, OSError):
        _fail("PREFLIGHT_DOCKER_COMPOSE_UNAVAILABLE")
    except subprocess.TimeoutExpired:
        _fail("PREFLIGHT_COMPOSE_CONFIG_TIMEOUT")
    if result.returncode != 0:
        _fail("PREFLIGHT_COMPOSE_CONFIG_FAILED")
    try:
        document = json.loads(result.stdout)
    except (TypeError, json.JSONDecodeError):
        _fail("PREFLIGHT_COMPOSE_JSON_INVALID")
    if not isinstance(document, dict):
        _fail("PREFLIGHT_COMPOSE_JSON_INVALID")
    return document


def _services(document: Mapping[str, Any]) -> Mapping[str, Any]:
    services = document.get("services")
    if not isinstance(services, Mapping):
        _fail("PREFLIGHT_SERVICE_SHAPE_INVALID")
    return services


def _service(document: Mapping[str, Any], name: str) -> Mapping[str, Any]:
    service = _services(document).get(name)
    if not isinstance(service, Mapping):
        _fail("PREFLIGHT_SERVICE_SHAPE_INVALID")
    return service


def _environment(service: Mapping[str, Any]) -> dict[str, str]:
    raw_environment = service.get("environment", {})
    if isinstance(raw_environment, Mapping):
        return {str(key): "" if value is None else str(value) for key, value in raw_environment.items()}
    if isinstance(raw_environment, Sequence) and not isinstance(raw_environment, (str, bytes)):
        result: dict[str, str] = {}
        for item in raw_environment:
            if not isinstance(item, str) or "=" not in item:
                _fail("PREFLIGHT_ENVIRONMENT_SHAPE_INVALID")
            key, value = item.split("=", 1)
            result[key] = value
        return result
    _fail("PREFLIGHT_ENVIRONMENT_SHAPE_INVALID")


def _ports(service: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    raw_ports = service.get("ports", [])
    if raw_ports is None:
        return []
    if not isinstance(raw_ports, Sequence) or isinstance(raw_ports, (str, bytes)):
        _fail("PREFLIGHT_PORT_SHAPE_INVALID")
    ports: list[Mapping[str, Any]] = []
    for port in raw_ports:
        if not isinstance(port, Mapping):
            _fail("PREFLIGHT_PORT_SHAPE_INVALID")
        ports.append(port)
    return ports


def _bind_source(service: Mapping[str, Any], target: str) -> tuple[str, Mapping[str, Any]]:
    raw_volumes = service.get("volumes", [])
    if not isinstance(raw_volumes, Sequence) or isinstance(raw_volumes, (str, bytes)):
        _fail("PREFLIGHT_VOLUME_SHAPE_INVALID")
    for volume in raw_volumes:
        if not isinstance(volume, Mapping):
            _fail("PREFLIGHT_VOLUME_SHAPE_INVALID")
        if volume.get("type") == "bind" and volume.get("target") == target:
            source = volume.get("source")
            if not isinstance(source, str) or not source:
                _fail("PREFLIGHT_VOLUME_SHAPE_INVALID")
            return source, volume
    _fail("PREFLIGHT_REQUIRED_BIND_MISSING")


def _env_file_paths(service: Mapping[str, Any]) -> tuple[list[str], bool]:
    """Return rendered env_file paths and whether Compose retained object metadata."""

    raw_env_file = service.get("env_file")
    if raw_env_file is None:
        return [], True
    entries = raw_env_file if isinstance(raw_env_file, list) else [raw_env_file]
    paths: list[str] = []
    retained_metadata = True
    for entry in entries:
        if isinstance(entry, str):
            paths.append(entry)
            retained_metadata = False
        elif isinstance(entry, Mapping):
            path = entry.get("path")
            if not isinstance(path, str) or not path:
                _fail("PREFLIGHT_ENV_FILE_SHAPE_INVALID")
            paths.append(path)
        else:
            _fail("PREFLIGHT_ENV_FILE_SHAPE_INVALID")
    return paths, retained_metadata


def _contains_all(source: str, snippets: Sequence[str]) -> bool:
    return all(snippet in source for snippet in snippets)


def _validate_static_sources(root: Path) -> int:
    dockerignore = (root / "apps" / "api" / ".dockerignore").read_text(encoding="utf-8")
    caddyfile = (root / "infra" / "caddy" / "Caddyfile").read_text(encoding="utf-8")
    local_overlay = (root / "infra" / "compose" / "compose.local.yml").read_text(encoding="utf-8")
    prod_overlay = (root / "infra" / "compose" / "compose.prod.yml").read_text(encoding="utf-8")
    cpu_overlay = (root / "infra" / "compose" / "compose.cpu.yml").read_text(encoding="utf-8")
    base_compose = (root / "infra" / "compose" / "compose.yml").read_text(encoding="utf-8")

    allowlist = ("**\n!Dockerfile\n!requirements.txt\n!app/\n!app/**\n!.dockerignore",)
    docker_denies = (
        ".env\n.env.*\n**/.env\n**/.env.*",
        "*.key",
        "*.pem",
        "*.cert",
        "*.log",
        ".venv/",
        "tests/",
        "**/__pycache__/",
        "**/*.py[cod]",
    )
    _require(_contains_all(dockerignore, allowlist + docker_denies), "PREFLIGHT_DOCKERIGNORE_INVALID")
    _require(dockerignore.lstrip().startswith("# Deny by default"), "PREFLIGHT_DOCKERIGNORE_INVALID")

    _require("ports:" not in base_compose.split("  api:", 1)[0], "PREFLIGHT_BASE_PORTS_INVALID")
    _require("${DATA_HOST_ROOT:?" in base_compose, "PREFLIGHT_BASE_DATA_BIND_INVALID")
    _require("../../data" not in base_compose, "PREFLIGHT_BASE_DATA_BIND_INVALID")
    _require("MAIL_HMAC_KEY" not in base_compose, "PREFLIGHT_WORKER_SECRET_BOUNDARY_INVALID")
    _require("MAIL_HMAC_ACTIVE_KID" not in base_compose, "PREFLIGHT_WORKER_SECRET_BOUNDARY_INVALID")
    _require("MAIL_HMAC_PREVIOUS_KEYS_JSON" not in base_compose, "PREFLIGHT_WORKER_SECRET_BOUNDARY_INVALID")
    _require("AUTH_JWT_SIGNING_KEY" not in base_compose, "PREFLIGHT_WORKER_SECRET_BOUNDARY_INVALID")
    _require("AUTH_JWT_PREVIOUS_KEYS_JSON" not in base_compose, "PREFLIGHT_WORKER_SECRET_BOUNDARY_INVALID")
    _require("ADMIN_BOOTSTRAP_PASSWORD_HASH" not in base_compose, "PREFLIGHT_WORKER_SECRET_BOUNDARY_INVALID")

    _require(
        _contains_all(
            local_overlay,
            (
                '"127.0.0.1:${CADDY_HOST_PORT:-8080}:8080/tcp"',
                "STAR_ISLE_SITE_ADDRESS: \":8080\"",
                "ACME_EMAIL: \"admin@example.invalid\"",
                "required: false",
                "format: raw",
                "STAR_ISLE_ENV: development",
                'COOKIE_SECURE: "false"',
            ),
        ),
        "PREFLIGHT_LOCAL_OVERLAY_INVALID",
    )
    _require(
        _contains_all(
            prod_overlay,
            (
                "target: 80",
                "target: 443",
                "protocol: udp",
                "STAR_ISLE_SITE_ADDRESS: ${STAR_ISLE_SITE_ADDRESS:?",
                "ACME_EMAIL: ${ACME_EMAIL:?",
                "STAR_ISLE_API_ENV_FILE:?",
                "required: true",
                "format: raw",
                "STAR_ISLE_ENV: production",
                'COOKIE_SECURE: "true"',
            ),
        ),
        "PREFLIGHT_PROD_OVERLAY_INVALID",
    )
    _require("../../data/models" not in cpu_overlay, "PREFLIGHT_CPU_MODEL_BIND_INVALID")
    _require(cpu_overlay.count("${MODEL_HOST_ROOT:?") == 2, "PREFLIGHT_CPU_MODEL_BIND_INVALID")
    _require(cpu_overlay.count("read_only: true") == 2, "PREFLIGHT_CPU_MODEL_BIND_INVALID")

    _require("{$STAR_ISLE_SITE_ADDRESS::8080}" in caddyfile, "PREFLIGHT_CADDY_SOURCE_INVALID")
    _require("{$ACME_EMAIL:admin@example.invalid}" in caddyfile, "PREFLIGHT_CADDY_SOURCE_INVALID")
    _require(caddyfile.count("encode gzip") == 1, "PREFLIGHT_CADDY_SOURCE_INVALID")
    _require("handle /api/* {\n\t\treverse_proxy api:8000" in caddyfile, "PREFLIGHT_CADDY_SOURCE_INVALID")
    _require("handle {\n\t\tencode gzip" in caddyfile, "PREFLIGHT_CADDY_SOURCE_INVALID")
    _require("flush_interval -1" not in caddyfile, "PREFLIGHT_CADDY_SOURCE_INVALID")
    _require("try_files {path} {path}/ =404" in caddyfile, "PREFLIGHT_CADDY_SOURCE_INVALID")
    return 9


def _validate_common_topology(
    document: Mapping[str, Any],
    data_root: Path,
    repository_data_root: Path,
    expected_api_env_file: Path,
) -> int:
    services = _services(document)
    _require(set(("caddy", "api", "worker")).issubset(services), "PREFLIGHT_REQUIRED_SERVICE_MISSING")
    caddy = _service(document, "caddy")
    api = _service(document, "api")
    worker = _service(document, "worker")

    for name, service in services.items():
        if name != "caddy":
            _require(not _ports(service), "PREFLIGHT_UNEXPECTED_HOST_PORT")

    for service in (api, worker):
        source, _volume = _bind_source(service, "/data")
        source_path = Path(source).resolve(strict=False)
        _require(_same_path(source_path, data_root), "PREFLIGHT_DATA_BIND_INVALID")
        _require(not _inside(source_path, repository_data_root), "PREFLIGHT_DATA_BIND_INVALID")

    expected_caddy_binds = {
        "/etc/caddy/Caddyfile": _repository_root() / "infra" / "caddy" / "Caddyfile",
        "/srv/web": _repository_root() / "apps" / "web" / "dist",
    }
    for target, expected_source in expected_caddy_binds.items():
        source, volume = _bind_source(caddy, target)
        _require(
            _same_path(Path(source).resolve(strict=False), expected_source),
            "PREFLIGHT_CADDY_BIND_INVALID",
        )
        bind_options = volume.get("bind")
        _require(
            volume.get("read_only") is True
            and isinstance(bind_options, Mapping)
            and bind_options.get("create_host_path") is False,
            "PREFLIGHT_CADDY_BIND_INVALID",
        )

    api_environment = _environment(api)
    worker_environment = _environment(worker)
    allowed_worker_keys = {
        "DATA_ROOT",
        "MAIL_RETENTION_DAYS",
        "IDEMPOTENCY_RETENTION_DAYS",
        "AUDIT_RETENTION_DAYS",
        "AUDIT_SOFT_CAP_ROWS",
        "LLAMA_CHAT_BASE_URL",
        "LLAMA_EMBED_BASE_URL",
        "CHAT_MODEL_ID",
        "EMBEDDING_MODEL_ID",
        "EMBEDDING_DIM",
    }
    _require(set(worker_environment).issubset(allowed_worker_keys), "PREFLIGHT_WORKER_ENV_BOUNDARY_INVALID")
    _require(
        not ({"AUTH_JWT_SIGNING_KEY", "AUTH_JWT_PREVIOUS_KEYS_JSON", "ADMIN_BOOTSTRAP_PASSWORD_HASH", "MAIL_HMAC_KEY", "MAIL_HMAC_ACTIVE_KID", "MAIL_HMAC_PREVIOUS_KEYS_JSON"} & set(worker_environment)),
        "PREFLIGHT_WORKER_SECRET_BOUNDARY_INVALID",
    )
    worker_env_files, _ = _env_file_paths(worker)
    _require(not worker_env_files, "PREFLIGHT_WORKER_ENV_FILE_INVALID")

    for key, expected_value in {
        "MAIL_RETENTION_DAYS": "30",
        "IDEMPOTENCY_RETENTION_DAYS": "30",
        "EMBEDDING_DIM": "384",
    }.items():
        _require(
            api_environment.get(key) == expected_value and worker_environment.get(key) == expected_value,
            "PREFLIGHT_SHARED_RUNTIME_VALUES_INVALID",
        )

    audit_maintenance_keys = {"AUDIT_RETENTION_DAYS", "AUDIT_SOFT_CAP_ROWS"}
    _require(
        not (audit_maintenance_keys & set(api_environment)),
        "PREFLIGHT_AUDIT_MAINTENANCE_BOUNDARY_INVALID",
    )
    for key, expected_value in {"AUDIT_RETENTION_DAYS": "90", "AUDIT_SOFT_CAP_ROWS": "100000"}.items():
        _require(
            worker_environment.get(key) == expected_value,
            "PREFLIGHT_WORKER_MAINTENANCE_VALUES_INVALID",
        )

    api_env_files, _ = _env_file_paths(api)
    _require(len(api_env_files) == 1, "PREFLIGHT_API_ENV_FILE_INVALID")
    _require(_same_path(Path(api_env_files[0]).resolve(strict=False), expected_api_env_file), "PREFLIGHT_API_ENV_FILE_INVALID")
    _require(
        set(_environment(caddy)) == {"STAR_ISLE_SITE_ADDRESS", "ACME_EMAIL"},
        "PREFLIGHT_CADDY_ENV_INVALID",
    )
    return 12


def _validate_local(document: Mapping[str, Any], common_checks: int) -> int:
    caddy = _service(document, "caddy")
    ports = _ports(caddy)
    _require(len(ports) == 1, "PREFLIGHT_LOCAL_PORTS_INVALID")
    port = ports[0]
    _require(
        str(port.get("target")) == "8080"
        and str(port.get("published")) == "18080"
        and str(port.get("protocol")) == "tcp"
        and str(port.get("host_ip")) == "127.0.0.1",
        "PREFLIGHT_LOCAL_PORTS_INVALID",
    )
    environment = _environment(caddy)
    _require(environment.get("STAR_ISLE_SITE_ADDRESS") == ":8080", "PREFLIGHT_LOCAL_CADDY_ENV_INVALID")
    _require(environment.get("ACME_EMAIL") == "admin@example.invalid", "PREFLIGHT_LOCAL_CADDY_ENV_INVALID")
    api_environment = _environment(_service(document, "api"))
    _require(api_environment.get("STAR_ISLE_ENV") == "development", "PREFLIGHT_LOCAL_API_ENV_INVALID")
    _require(api_environment.get("COOKIE_SECURE") == "false", "PREFLIGHT_LOCAL_API_ENV_INVALID")
    return common_checks + 4


def _validate_prod(document: Mapping[str, Any], common_checks: int) -> int:
    caddy = _service(document, "caddy")
    observed_ports = {
        (str(port.get("target")), str(port.get("published")), str(port.get("protocol"))) for port in _ports(caddy)
    }
    expected_ports = {("80", "80", "tcp"), ("443", "443", "tcp"), ("443", "443", "udp")}
    _require(observed_ports == expected_ports, "PREFLIGHT_PROD_PORTS_INVALID")
    api = _service(document, "api")
    api_environment = _environment(api)
    _require(api_environment.get("STAR_ISLE_ENV") == "production", "PREFLIGHT_PROD_API_ENV_INVALID")
    _require(api_environment.get("COOKIE_SECURE") == "true", "PREFLIGHT_PROD_API_ENV_INVALID")
    api_env_files, metadata_retained = _env_file_paths(api)
    _require(len(api_env_files) == 1, "PREFLIGHT_PROD_ENV_FILE_INVALID")
    if metadata_retained:
        entry = api.get("env_file")
        entry = entry[0] if isinstance(entry, list) and entry else entry
        if isinstance(entry, Mapping):
            # Compose v5 retains raw format but omits the source-only ``required``
            # marker from rendered JSON.  The static production-overlay check above
            # asserts that marker; this rendered check proves the selected path is
            # still the controlled external env file.
            _require(entry.get("format") == "raw", "PREFLIGHT_PROD_ENV_FILE_INVALID")
    return common_checks + 4


def _validate_cpu(document: Mapping[str, Any], model_root: Path, common_checks: int) -> int:
    for model_service, expected_target in (("llama-chat", "8080"), ("llama-embed", "8081")):
        service = _service(document, model_service)
        _require(not _ports(service), "PREFLIGHT_UNEXPECTED_HOST_PORT")
        source, volume = _bind_source(service, "/models")
        _require(_same_path(Path(source).resolve(strict=False), model_root), "PREFLIGHT_CPU_MODEL_BIND_INVALID")
        _require(volume.get("read_only") is True, "PREFLIGHT_CPU_MODEL_BIND_INVALID")
        healthcheck = service.get("healthcheck")
        _require(isinstance(healthcheck, Mapping), "PREFLIGHT_CPU_HEALTHCHECK_INVALID")
        environment = _environment(service)
        _require(environment.get("LLAMA_ARG_PORT") == expected_target, "PREFLIGHT_CPU_INTERNAL_URL_INVALID")

    for service_name in ("api", "worker"):
        environment = _environment(_service(document, service_name))
        _require(environment.get("LLAMA_CHAT_BASE_URL") == "http://llama-chat:8080", "PREFLIGHT_CPU_INTERNAL_URL_INVALID")
        _require(environment.get("LLAMA_EMBED_BASE_URL") == "http://llama-embed:8081", "PREFLIGHT_CPU_INTERNAL_URL_INVALID")
        _require(environment.get("EMBEDDING_DIM") == "384", "PREFLIGHT_CPU_DIMENSION_INVALID")
    return common_checks + 8


def _validate_owned_temp(temporary_root: Path) -> None:
    system_temp = Path(tempfile.gettempdir()).resolve(strict=False)
    resolved_root = temporary_root.resolve(strict=False)
    if resolved_root == system_temp or not _inside(resolved_root, system_temp):
        _fail("PREFLIGHT_TEMP_OWNERSHIP_INVALID")


def _run() -> tuple[list[tuple[str, int]], int]:
    root = _repository_root()
    static_checks = _validate_static_sources(root)
    docker = shutil.which("docker")
    if not docker:
        _fail("PREFLIGHT_DOCKER_COMPOSE_UNAVAILABLE")

    temporary = tempfile.TemporaryDirectory(prefix="starisle-deployment-preflight-")
    cleanup_failed = False
    try:
        temporary_root = Path(temporary.name)
        _validate_owned_temp(temporary_root)
        data_root = temporary_root / "runtime-data"
        model_root = temporary_root / "model-files"
        api_env_file = temporary_root / "api.env"
        compose_env_file = temporary_root / "compose.env"
        docker_config = temporary_root / "docker-config"
        data_root.mkdir()
        model_root.mkdir()
        docker_config.mkdir()
        _write_controlled_env(api_env_file, {"PREFLIGHT_DUMMY": "1"})
        interpolation_values = {
            "DATA_HOST_ROOT": str(data_root),
            "MODEL_HOST_ROOT": str(model_root),
            "STAR_ISLE_API_ENV_FILE": str(api_env_file),
            "CADDY_HOST_PORT": "18080",
            "CADDY_HTTP_HOST_PORT": "80",
            "CADDY_HTTPS_HOST_PORT": "443",
            "STAR_ISLE_SITE_ADDRESS": "preflight.example.invalid",
            "ACME_EMAIL": "preflight@example.invalid",
            "MAIL_RETENTION_DAYS": "30",
            "IDEMPOTENCY_RETENTION_DAYS": "30",
            "AUDIT_RETENTION_DAYS": "90",
            "AUDIT_SOFT_CAP_ROWS": "100000",
            "CHAT_MODEL_ID": "starisle-chat",
            "EMBEDDING_MODEL_ID": "starisle-embed",
            "EMBEDDING_DIM": "384",
            "CHAT_MODEL_FILE": "CHANGE-ME.gguf",
            "EMBED_MODEL_FILE": "CHANGE-ME.gguf",
        }
        _write_controlled_env(compose_env_file, interpolation_values)
        controlled_environment = _controlled_subprocess_env(temporary_root, interpolation_values)
        base = root / "infra" / "compose" / "compose.yml"
        local = root / "infra" / "compose" / "compose.local.yml"
        prod = root / "infra" / "compose" / "compose.prod.yml"
        cpu = root / "infra" / "compose" / "compose.cpu.yml"
        repository_data_root = root / "data"
        reports: list[tuple[str, int]] = []
        for name, files, profile, mode in (
            ("local", (base, local), None, "local"),
            ("prod", (base, prod), None, "prod"),
            ("local-cpu", (base, local, cpu), "cpu", "local"),
            ("prod-cpu", (base, prod, cpu), "cpu", "prod"),
        ):
            document = _run_compose_config(docker, files, profile, compose_env_file, controlled_environment)
            common_checks = _validate_common_topology(document, data_root, repository_data_root, api_env_file)
            checks = _validate_local(document, common_checks) if mode == "local" else _validate_prod(document, common_checks)
            if profile == "cpu":
                checks = _validate_cpu(document, model_root, checks)
            reports.append((name, checks))
        return reports, static_checks
    finally:
        try:
            temporary.cleanup()
        except Exception:
            cleanup_failed = True
        if cleanup_failed:
            _fail("PREFLIGHT_TEMP_CLEANUP_FAILED")


def main() -> int:
    try:
        reports, static_checks = _run()
    except PreflightError as exc:
        print(f"CHECK preflight status=FAIL code={exc.code}")
        return 1
    except Exception:
        print("CHECK preflight status=FAIL code=PREFLIGHT_INTERNAL_ERROR")
        return 1

    for profile, checks in reports:
        print(f"CHECK profile={profile} status=PASS securityChecks={checks}")
    print(f"CHECK preflight status=PASS securityChecks={static_checks + sum(checks for _, checks in reports)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
