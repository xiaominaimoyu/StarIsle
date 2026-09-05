"""Production-only deployment configuration boundaries."""

from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path
from types import ModuleType

import pytest

from app.config import Settings


_VALID_ARGON2ID_HASH = (
    "$argon2id$v=19$m=65536,t=3,p=4$HVWAthrotBYyUyMdgYIK9Q$"
    "BHM39lGOmFZkENTS3fKFvfw6exwBvM48JSQyTTmyVyU"
)
_PROJECT_ROOT = Path(__file__).resolve().parents[3]
_COMPOSE_ENV_EXAMPLE = _PROJECT_ROOT / "infra" / "compose" / ".env.example"
_BASE_COMPOSE = _PROJECT_ROOT / "infra" / "compose" / "compose.yml"
_COMPOSE_DEFAULT = re.compile(r"\$\{(?P<name>[A-Z][A-Z0-9_]*):-(?P<value>[^}]*)\}")


def _load_deployment_preflight() -> ModuleType:
    """Load the stdlib-only runner without invoking its command entry point."""

    module_name = "starisle_deployment_preflight_environment_test"
    script_path = Path(__file__).resolve().parents[3] / "scripts" / "deployment_preflight.py"
    spec = importlib.util.spec_from_file_location(module_name, script_path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    try:
        spec.loader.exec_module(module)
    finally:
        sys.modules.pop(module_name, None)
    return module


def _configure_valid_production(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Supply wholly synthetic production inputs without loading a dotenv file."""

    monkeypatch.setenv("PYTHON_DOTENV_DISABLED", "1")
    monkeypatch.setenv("STAR_ISLE_ENV", "production")
    monkeypatch.setenv("AUTH_JWT_SIGNING_KEY", "j" * 48)
    monkeypatch.setenv("AUTH_JWT_ACTIVE_KID", "current")
    monkeypatch.setenv("AUTH_JWT_PREVIOUS_KEYS_JSON", "{}")
    monkeypatch.setenv("MAIL_HMAC_KEY", "m" * 48)
    monkeypatch.setenv("MAIL_HMAC_ACTIVE_KID", "current")
    monkeypatch.setenv("MAIL_HMAC_PREVIOUS_KEYS_JSON", "{}")
    monkeypatch.setenv("ADMIN_BOOTSTRAP_PASSWORD_HASH", _VALID_ARGON2ID_HASH)
    monkeypatch.setenv("COOKIE_SECURE", "true")
    monkeypatch.setenv("DATA_ROOT", str(tmp_path / "isolated-production-data"))


def test_valid_production_settings_are_accepted(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    _configure_valid_production(monkeypatch, tmp_path)

    configured = Settings()

    assert configured.star_isle_env == "production"
    assert configured.cookie_secure is True
    assert configured.data_root == (tmp_path / "isolated-production-data").resolve()


def test_development_remains_the_default(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    monkeypatch.setenv("PYTHON_DOTENV_DISABLED", "1")
    monkeypatch.delenv("STAR_ISLE_ENV", raising=False)
    monkeypatch.setenv("DATA_ROOT", str(tmp_path / "development-data"))

    assert Settings().star_isle_env == "development"


def test_unknown_environment_is_rejected(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("PYTHON_DOTENV_DISABLED", "1")
    monkeypatch.setenv("STAR_ISLE_ENV", "staging")

    with pytest.raises(ValueError, match="STAR_ISLE_ENV"):
        Settings()


@pytest.mark.parametrize(
    ("variable", "value", "expected_name"),
    (
        ("AUTH_JWT_SIGNING_KEY", "short", "AUTH_JWT_SIGNING_KEY"),
        ("AUTH_JWT_SIGNING_KEY", "dev-only-change-me-0123456789abcdef0123456789abcdef", "AUTH_JWT_SIGNING_KEY"),
        ("AUTH_JWT_SIGNING_KEY", " " * 40, "AUTH_JWT_SIGNING_KEY"),
        ("AUTH_JWT_SIGNING_KEY", " " * 20 + "short" + " " * 20, "AUTH_JWT_SIGNING_KEY"),
        ("MAIL_HMAC_KEY", "short", "MAIL_HMAC_KEY"),
        ("MAIL_HMAC_KEY", "dev-only-mail-hmac-key", "MAIL_HMAC_KEY"),
        ("MAIL_HMAC_KEY", "\t" * 40, "MAIL_HMAC_KEY"),
        ("MAIL_HMAC_KEY", " " * 20 + "short" + " " * 20, "MAIL_HMAC_KEY"),
        ("ADMIN_BOOTSTRAP_PASSWORD_HASH", "not-a-hash", "ADMIN_BOOTSTRAP_PASSWORD_HASH"),
        (
            "ADMIN_BOOTSTRAP_PASSWORD_HASH",
            _VALID_ARGON2ID_HASH.replace("$argon2id$", "$argon2i$", 1),
            "ADMIN_BOOTSTRAP_PASSWORD_HASH",
        ),
        ("COOKIE_SECURE", "false", "COOKIE_SECURE"),
    ),
)
def test_production_rejects_each_security_boundary(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    variable: str,
    value: str,
    expected_name: str,
):
    _configure_valid_production(monkeypatch, tmp_path)
    monkeypatch.setenv(variable, value)

    with pytest.raises(ValueError) as error:
        Settings()

    assert expected_name in str(error.value)
    assert value not in str(error.value)


def test_production_requires_an_explicit_data_root(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    _configure_valid_production(monkeypatch, tmp_path)
    monkeypatch.delenv("DATA_ROOT")

    with pytest.raises(ValueError) as error:
        Settings()

    assert "DATA_ROOT" in str(error.value)


@pytest.mark.parametrize("active_kid", ("", "not safe", "../escape", "x" * 65, "旧密钥"))
def test_jwt_active_kid_rejects_unsafe_values_without_echoing_them(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, active_kid: str
):
    _configure_valid_production(monkeypatch, tmp_path)
    monkeypatch.setenv("AUTH_JWT_ACTIVE_KID", active_kid)

    with pytest.raises(ValueError) as error:
        Settings()

    assert "AUTH_JWT_ACTIVE_KID" in str(error.value)
    if active_kid:
        assert active_kid not in str(error.value)


def test_production_accepts_four_safe_previous_jwt_keys(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    _configure_valid_production(monkeypatch, tmp_path)
    monkeypatch.setenv(
        "AUTH_JWT_PREVIOUS_KEYS_JSON",
        '{"old-1":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",'
        '"old-2":"bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",'
        '"old-3":"cccccccccccccccccccccccccccccccc",'
        '"old-4":"dddddddddddddddddddddddddddddddd"}',
    )

    configured = Settings()

    assert tuple(configured.auth_jwt_previous_keys) == ("old-1", "old-2", "old-3", "old-4")


@pytest.mark.parametrize(
    "value",
    ("[]", "{", '{"current":"old-key"}', '{"old": 3}', '{"not safe":"old-key"}', '{"a":"x","b":"y","c":"z","d":"w","e":"v"}'),
)
def test_jwt_previous_key_config_rejects_invalid_shapes_without_echoing_input(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, value: str
):
    _configure_valid_production(monkeypatch, tmp_path)
    monkeypatch.setenv("AUTH_JWT_PREVIOUS_KEYS_JSON", value)

    with pytest.raises(ValueError) as error:
        Settings()

    assert "AUTH_JWT_PREVIOUS_KEYS_JSON" in str(error.value)
    assert value not in str(error.value)


@pytest.mark.parametrize("old_secret", ("short", "dev-only-old-key-0123456789abcdef0123456789", " " * 40))
def test_production_rejects_unsafe_previous_jwt_secret_without_echoing_it(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, old_secret: str
):
    _configure_valid_production(monkeypatch, tmp_path)
    monkeypatch.setenv("AUTH_JWT_PREVIOUS_KEYS_JSON", '{"previous":"' + old_secret + '"}')

    with pytest.raises(ValueError) as error:
        Settings()

    assert "AUTH_JWT_PREVIOUS_KEYS_JSON" in str(error.value)
    assert old_secret not in str(error.value)


@pytest.mark.parametrize("active_kid", ("", "not safe", "../escape", "x" * 65, "旧密钥"))
def test_mail_hmac_active_kid_rejects_unsafe_values_without_echoing_them(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, active_kid: str
):
    _configure_valid_production(monkeypatch, tmp_path)
    monkeypatch.setenv("MAIL_HMAC_ACTIVE_KID", active_kid)

    with pytest.raises(ValueError) as error:
        Settings()

    assert "MAIL_HMAC_ACTIVE_KID" in str(error.value)
    if active_kid:
        assert active_kid not in str(error.value)


def test_production_accepts_four_safe_previous_mail_hmac_keys(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    _configure_valid_production(monkeypatch, tmp_path)
    monkeypatch.setenv(
        "MAIL_HMAC_PREVIOUS_KEYS_JSON",
        '{"old-1":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",'
        '"old-2":"bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",'
        '"old-3":"cccccccccccccccccccccccccccccccc",'
        '"old-4":"dddddddddddddddddddddddddddddddd"}',
    )

    configured = Settings()

    assert tuple(configured.mail_hmac_previous_keys) == ("old-1", "old-2", "old-3", "old-4")


@pytest.mark.parametrize(
    "value",
    (
        "[]",
        "{",
        '{"current":"old-key"}',
        '{"old": 3}',
        '{"not safe":"old-key"}',
        '{"a":"x","b":"y","c":"z","d":"w","e":"v"}',
        '{"old":"' + "m" * 48 + '"}',
        '{"old-1":"same-secret","old-2":"same-secret"}',
    ),
)
def test_mail_hmac_previous_key_config_rejects_invalid_shapes_without_echoing_input(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, value: str
):
    _configure_valid_production(monkeypatch, tmp_path)
    monkeypatch.setenv("MAIL_HMAC_PREVIOUS_KEYS_JSON", value)

    with pytest.raises(ValueError) as error:
        Settings()

    assert "MAIL_HMAC_PREVIOUS_KEYS_JSON" in str(error.value)
    assert value not in str(error.value)


@pytest.mark.parametrize("old_secret", ("short", "dev-only-old-key-0123456789abcdef0123456789", " " * 40))
def test_production_rejects_unsafe_previous_mail_hmac_secret_without_echoing_it(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, old_secret: str
):
    _configure_valid_production(monkeypatch, tmp_path)
    monkeypatch.setenv("MAIL_HMAC_PREVIOUS_KEYS_JSON", '{"previous":"' + old_secret + '"}')

    with pytest.raises(ValueError) as error:
        Settings()

    assert "MAIL_HMAC_PREVIOUS_KEYS_JSON" in str(error.value)
    assert old_secret not in str(error.value)


def test_preflight_casefolds_host_environment_overrides(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    preflight = _load_deployment_preflight()
    leaked_values = {
        "data_host_root": "mixed-case-host-data-marker",
        "cOmPoSe_FiLe": "mixed-case-host-compose-marker",
        "sTaR_iSlE_Api_Env_File": "mixed-case-host-api-env-marker",
        "dOcKeR_cOnFiG": "mixed-case-host-docker-config-marker",
        "iDeMpOtEnCy_ReTeNtIoN_DaYs": "mixed-case-host-idempotency-marker",
        "aUdIt_ReTeNtIoN_DaYs": "mixed-case-host-audit-retention-marker",
        "AuDiT_sOfT_cAp_RoWs": "mixed-case-host-audit-cap-marker",
        "aUtH_JwT_aCtIvE_kId": "mixed-case-host-active-jwt-marker",
        "aUtH_JwT_pReViOuS_kEyS_jSoN": "mixed-case-host-old-jwt-marker",
        "mAiL_hMaC_aCtIvE_kId": "mixed-case-host-active-mail-marker",
        "mAiL_hMaC_pReViOuS_kEyS_jSoN": "mixed-case-host-old-mail-marker",
    }
    for key, value in leaked_values.items():
        monkeypatch.setenv(key, value)
    controlled_values = {
        "DATA_HOST_ROOT": "controlled-data-root",
        "STAR_ISLE_API_ENV_FILE": "controlled-api-env-file",
        "IDEMPOTENCY_RETENTION_DAYS": "30",
        "AUDIT_RETENTION_DAYS": "90",
        "AUDIT_SOFT_CAP_ROWS": "100000",
    }

    environment = preflight._controlled_subprocess_env(tmp_path, controlled_values)
    normalized_environment = {key.casefold(): value for key, value in environment.items()}

    assert normalized_environment["data_host_root"] == "controlled-data-root"
    assert normalized_environment["star_isle_api_env_file"] == "controlled-api-env-file"
    assert normalized_environment["docker_config"] == str(tmp_path / "docker-config")
    assert normalized_environment["idempotency_retention_days"] == "30"
    assert normalized_environment["audit_retention_days"] == "90"
    assert normalized_environment["audit_soft_cap_rows"] == "100000"
    assert "auth_jwt_active_kid" not in normalized_environment
    assert "auth_jwt_previous_keys_json" not in normalized_environment
    assert "mail_hmac_active_kid" not in normalized_environment
    assert "mail_hmac_previous_keys_json" not in normalized_environment
    assert not set(leaked_values.values()) & set(environment.values())


def _common_topology_document(data_root: Path, api_env_file: Path) -> dict[str, object]:
    return {
        "services": {
            "caddy": {
                "environment": {
                    "STAR_ISLE_SITE_ADDRESS": ":8080",
                    "ACME_EMAIL": "admin@example.invalid",
                },
                "volumes": [
                    {
                        "type": "bind",
                        "source": str(_PROJECT_ROOT / "infra" / "caddy" / "Caddyfile"),
                        "target": "/etc/caddy/Caddyfile",
                        "read_only": True,
                        "bind": {"create_host_path": False},
                    },
                    {
                        "type": "bind",
                        "source": str(_PROJECT_ROOT / "apps" / "web" / "dist"),
                        "target": "/srv/web",
                        "read_only": True,
                        "bind": {"create_host_path": False},
                    },
                ],
            },
            "api": {
                "environment": {
                    "DATA_ROOT": "/data",
                    "MAIL_RETENTION_DAYS": "30",
                    "IDEMPOTENCY_RETENTION_DAYS": "30",
                    "EMBEDDING_DIM": "384",
                },
                "volumes": [{"type": "bind", "source": str(data_root), "target": "/data"}],
                "env_file": [{"path": str(api_env_file), "format": "raw"}],
            },
            "worker": {
                "environment": {
                    "DATA_ROOT": "/data",
                    "MAIL_RETENTION_DAYS": "30",
                    "IDEMPOTENCY_RETENTION_DAYS": "30",
                    "AUDIT_RETENTION_DAYS": "90",
                    "AUDIT_SOFT_CAP_ROWS": "100000",
                    "EMBEDDING_DIM": "384",
                },
                "volumes": [{"type": "bind", "source": str(data_root), "target": "/data"}],
            },
        }
    }


def test_preflight_rejects_api_worker_shared_runtime_drift(tmp_path: Path):
    preflight = _load_deployment_preflight()
    data_root = tmp_path / "synthetic-data"
    api_env_file = tmp_path / "synthetic-api.env"
    document = _common_topology_document(data_root, api_env_file)

    assert preflight._validate_common_topology(document, data_root, tmp_path / "repository-data", api_env_file) == 12
    document["services"]["api"]["environment"]["EMBEDDING_DIM"] = "385"

    with pytest.raises(preflight.PreflightError) as error:
        preflight._validate_common_topology(document, data_root, tmp_path / "repository-data", api_env_file)

    assert error.value.code == "PREFLIGHT_SHARED_RUNTIME_VALUES_INVALID"


def test_preflight_rejects_api_worker_idempotency_retention_drift(tmp_path: Path):
    preflight = _load_deployment_preflight()
    data_root = tmp_path / "synthetic-data"
    api_env_file = tmp_path / "synthetic-api.env"
    document = _common_topology_document(data_root, api_env_file)
    document["services"]["worker"]["environment"]["IDEMPOTENCY_RETENTION_DAYS"] = "29"

    with pytest.raises(preflight.PreflightError) as error:
        preflight._validate_common_topology(document, data_root, tmp_path / "repository-data", api_env_file)

    assert error.value.code == "PREFLIGHT_SHARED_RUNTIME_VALUES_INVALID"


def test_preflight_rejects_extra_worker_environment_key_with_stable_code(tmp_path: Path):
    preflight = _load_deployment_preflight()
    data_root = tmp_path / "synthetic-data"
    api_env_file = tmp_path / "synthetic-api.env"
    document = _common_topology_document(data_root, api_env_file)
    document["services"]["worker"]["environment"]["UNRELATED_WORKER_SETTING"] = "synthetic"

    with pytest.raises(preflight.PreflightError) as error:
        preflight._validate_common_topology(document, data_root, tmp_path / "repository-data", api_env_file)

    assert error.value.code == "PREFLIGHT_WORKER_ENV_BOUNDARY_INVALID"


@pytest.mark.parametrize(
    ("service", "key", "value", "expected_code"),
    [
        ("api", "AUDIT_RETENTION_DAYS", "90", "PREFLIGHT_AUDIT_MAINTENANCE_BOUNDARY_INVALID"),
        ("worker", "AUDIT_RETENTION_DAYS", "89", "PREFLIGHT_WORKER_MAINTENANCE_VALUES_INVALID"),
        ("worker", "AUDIT_SOFT_CAP_ROWS", "99999", "PREFLIGHT_WORKER_MAINTENANCE_VALUES_INVALID"),
    ],
)
def test_preflight_enforces_worker_only_audit_maintenance_values(
    tmp_path: Path, service: str, key: str, value: str, expected_code: str
):
    preflight = _load_deployment_preflight()
    data_root = tmp_path / "synthetic-data"
    api_env_file = tmp_path / "synthetic-api.env"
    document = _common_topology_document(data_root, api_env_file)
    document["services"][service]["environment"][key] = value

    with pytest.raises(preflight.PreflightError) as error:
        preflight._validate_common_topology(document, data_root, tmp_path / "repository-data", api_env_file)

    assert error.value.code == expected_code


def test_preflight_rejects_extra_caddy_environment_keys(tmp_path: Path):
    preflight = _load_deployment_preflight()
    data_root = tmp_path / "synthetic-data"
    api_env_file = tmp_path / "synthetic-api.env"
    document = _common_topology_document(data_root, api_env_file)
    caddy_environment = document["services"]["caddy"]["environment"]
    caddy_environment["MAIL_HMAC_KEY"] = "synthetic-marker-not-reflected"

    with pytest.raises(preflight.PreflightError) as error:
        preflight._validate_common_topology(document, data_root, tmp_path / "repository-data", api_env_file)

    assert error.value.code == "PREFLIGHT_CADDY_ENV_INVALID"
    assert "synthetic-marker-not-reflected" not in str(error.value)


@pytest.mark.parametrize(
    ("field", "unsafe_value"),
    (("read_only", False), ("create_host_path", True)),
)
def test_preflight_rejects_unsafe_caddy_bind_options(tmp_path: Path, field: str, unsafe_value: bool):
    preflight = _load_deployment_preflight()
    data_root = tmp_path / "synthetic-data"
    api_env_file = tmp_path / "synthetic-api.env"
    document = _common_topology_document(data_root, api_env_file)
    caddyfile_bind = document["services"]["caddy"]["volumes"][0]
    if field == "read_only":
        caddyfile_bind[field] = unsafe_value
    else:
        caddyfile_bind["bind"][field] = unsafe_value

    with pytest.raises(preflight.PreflightError) as error:
        preflight._validate_common_topology(document, data_root, tmp_path / "repository-data", api_env_file)

    assert error.value.code == "PREFLIGHT_CADDY_BIND_INVALID"


def test_compose_example_locks_nonsecret_retention_defaults():
    """示例只作文本解析；不加载 dotenv 或真实运行时 .env。"""
    example_values = {
        name: value
        for raw_line in _COMPOSE_ENV_EXAMPLE.read_text(encoding="utf-8").splitlines()
        if (line := raw_line.strip()) and not line.startswith("#") and "=" in line
        for name, value in (line.split("=", 1),)
    }
    compose_defaults: dict[str, set[str]] = {}
    for match in _COMPOSE_DEFAULT.finditer(_BASE_COMPOSE.read_text(encoding="utf-8")):
        compose_defaults.setdefault(match["name"], set()).add(match["value"])

    expected = {
        "IDEMPOTENCY_RETENTION_DAYS": "30",
        "AUDIT_RETENTION_DAYS": "90",
        "AUDIT_SOFT_CAP_ROWS": "100000",
    }
    assert {name: example_values.get(name) for name in expected} == expected
    assert {name: compose_defaults.get(name) for name in expected} == {
        name: {value} for name, value in expected.items()
    }
