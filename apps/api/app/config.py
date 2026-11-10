"""配置:所有变量名与 docs/总体具体开发计划 §6 一致;secret 仅环境注入。"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

from argon2 import extract_parameters
from argon2.exceptions import InvalidHashError

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover
    load_dotenv = None

if load_dotenv and os.environ.get("PYTHON_DOTENV_DISABLED") != "1":
    load_dotenv()

API_ROOT = "/api/v1"

_VALID_ENVIRONMENTS = {"development", "production"}
_DEVELOPMENT_SECRET_PREFIX = "dev-only-"
_JWT_KID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
_MAX_PREVIOUS_JWT_KEYS = 4


def _has_required_secret_bytes(value: str) -> bool:
    """Return whether a non-template secret meets the production minimum."""

    normalized = value.strip()
    return (
        value == normalized
        and len(normalized.encode("utf-8")) >= 32
        and not normalized.lower().startswith(_DEVELOPMENT_SECRET_PREFIX)
    )


def _is_argon2id_hash(value: str) -> bool:
    """Validate hash structure without ever attempting to verify a password."""

    if not value.startswith("$argon2id$"):
        return False
    try:
        extract_parameters(value)
    except InvalidHashError:
        return False
    return True


def _is_safe_jwt_kid(value: object) -> bool:
    return isinstance(value, str) and _JWT_KID.fullmatch(value) is not None


def _positive_int_setting(name: str, default: str) -> int:
    """Reject blank, non-integer, zero, and negative security/storage budgets at startup."""

    raw = os.environ.get(name, default)
    if not isinstance(raw, str):
        raise ValueError(f"{name}:positive-integer")
    try:
        value = int(raw.strip())
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name}:positive-integer") from exc
    if value < 1:
        raise ValueError(f"{name}:positive-integer")
    return value


def _parse_previous_jwt_keys(value: str, active_kid: str) -> dict[str, str]:
    """Parse only the bounded previous-key verification map without exposing values."""

    try:
        raw = json.loads(value)
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise ValueError("AUTH_JWT_PREVIOUS_KEYS_JSON:json-object") from exc
    if not isinstance(raw, dict) or len(raw) > _MAX_PREVIOUS_JWT_KEYS:
        raise ValueError("AUTH_JWT_PREVIOUS_KEYS_JSON:object-max-4")

    previous: dict[str, str] = {}
    for kid, secret in raw.items():
        if not _is_safe_jwt_kid(kid):
            raise ValueError("AUTH_JWT_PREVIOUS_KEYS_JSON:safe-kid")
        if kid == active_kid:
            raise ValueError("AUTH_JWT_PREVIOUS_KEYS_JSON:active-kid-collision")
        if not isinstance(secret, str) or not secret:
            raise ValueError("AUTH_JWT_PREVIOUS_KEYS_JSON:nonempty-string-secret")
        previous[kid] = secret
    return previous


def _parse_previous_mail_hmac_keys(value: str, active_kid: str, active_secret: str) -> dict[str, str]:
    """Parse the bounded mail retry key map without exposing secret values."""

    try:
        raw = json.loads(value)
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise ValueError("MAIL_HMAC_PREVIOUS_KEYS_JSON:json-object") from exc
    if not isinstance(raw, dict) or len(raw) > _MAX_PREVIOUS_JWT_KEYS:
        raise ValueError("MAIL_HMAC_PREVIOUS_KEYS_JSON:object-max-4")

    previous: dict[str, str] = {}
    seen_secrets = {active_secret}
    for kid, secret in raw.items():
        if not _is_safe_jwt_kid(kid):
            raise ValueError("MAIL_HMAC_PREVIOUS_KEYS_JSON:safe-kid")
        if kid == active_kid:
            raise ValueError("MAIL_HMAC_PREVIOUS_KEYS_JSON:active-kid-collision")
        if not isinstance(secret, str) or not secret:
            raise ValueError("MAIL_HMAC_PREVIOUS_KEYS_JSON:nonempty-string-secret")
        if secret in seen_secrets:
            raise ValueError("MAIL_HMAC_PREVIOUS_KEYS_JSON:duplicate-secret")
        seen_secrets.add(secret)
        previous[kid] = secret
    return previous


class Settings:
    def __init__(self) -> None:
        self.star_isle_env: str = os.environ.get("STAR_ISLE_ENV", "development").strip().lower()
        if self.star_isle_env not in _VALID_ENVIRONMENTS:
            raise ValueError("STAR_ISLE_ENV must be development or production")

        # 鉴权
        self.auth_jwt_signing_key: str = os.environ.get("AUTH_JWT_SIGNING_KEY", "dev-only-change-me")
        self.auth_jwt_active_kid: str = os.environ.get("AUTH_JWT_ACTIVE_KID", "v1")
        if not _is_safe_jwt_kid(self.auth_jwt_active_kid):
            raise ValueError("AUTH_JWT_ACTIVE_KID:safe-kid")
        self.auth_jwt_previous_keys = _parse_previous_jwt_keys(
            os.environ.get("AUTH_JWT_PREVIOUS_KEYS_JSON", "{}"), self.auth_jwt_active_kid
        )
        self.admin_bootstrap_password_hash: str = os.environ.get("ADMIN_BOOTSTRAP_PASSWORD_HASH", "")
        self.auth_access_ttl_minutes: int = _positive_int_setting("AUTH_ACCESS_TTL_MINUTES", "15")
        self.auth_refresh_ttl_days: int = _positive_int_setting("AUTH_REFRESH_TTL_DAYS", "7")
        self.cookie_secure: bool = os.environ.get("COOKIE_SECURE", "false").lower() == "true"

        # 运行时数据。生产环境必须显式提供，避免回退到仓库相对路径。
        configured_data_root = os.environ.get("DATA_ROOT")
        if self.star_isle_env == "production" and not (configured_data_root and configured_data_root.strip()):
            raise ValueError("invalid production configuration: DATA_ROOT:explicit-nonempty")
        self.data_root: Path = Path(configured_data_root or "../data").resolve()

        # 上传
        self.file_max_bytes: int = _positive_int_setting("FILE_MAX_BYTES", str(100 * 1024 * 1024))
        self.image_max_bytes: int = _positive_int_setting("IMAGE_MAX_BYTES", str(20 * 1024 * 1024))
        # 所有对象写入前均检查配额。默认 10 GiB，部署可按实际磁盘容量下调。
        self.storage_quota_bytes: int = _positive_int_setting(
            "STORAGE_QUOTA_BYTES", str(10 * 1024 * 1024 * 1024)
        )
        self.thumbnail_max_edge: int = 480
        self.thumbnail_quality: int = 80

        # 邮件
        self.mail_hmac_key: str = os.environ.get("MAIL_HMAC_KEY", "")
        self.mail_hmac_active_kid: str = os.environ.get("MAIL_HMAC_ACTIVE_KID", "v1")
        if not _is_safe_jwt_kid(self.mail_hmac_active_kid):
            raise ValueError("MAIL_HMAC_ACTIVE_KID:safe-kid")
        self.mail_hmac_previous_keys = _parse_previous_mail_hmac_keys(
            os.environ.get("MAIL_HMAC_PREVIOUS_KEYS_JSON", "{}"),
            self.mail_hmac_active_kid,
            self.mail_hmac_key,
        )
        self.mail_max_bytes: int = _positive_int_setting("MAIL_MAX_BYTES", str(5 * 1024 * 1024))
        self.mail_retention_days: int = int(os.environ.get("MAIL_RETENTION_DAYS", "30"))
        if self.mail_retention_days < 1:
            raise ValueError("MAIL_RETENTION_DAYS 必须大于或等于 1")

        # 幂等记录
        self.idempotency_retention_days: int = int(os.environ.get("IDEMPOTENCY_RETENTION_DAYS", "30"))
        if self.idempotency_retention_days < 1:
            raise ValueError("IDEMPOTENCY_RETENTION_DAYS 必须大于或等于 1")

        # 审计事件
        self.audit_retention_days: int = int(os.environ.get("AUDIT_RETENTION_DAYS", "90"))
        if self.audit_retention_days < 1:
            raise ValueError("AUDIT_RETENTION_DAYS 必须大于或等于 1")
        self.audit_soft_cap_rows: int = int(os.environ.get("AUDIT_SOFT_CAP_ROWS", "100000"))
        if self.audit_soft_cap_rows < 1:
            raise ValueError("AUDIT_SOFT_CAP_ROWS 必须大于或等于 1")

        # RAG
        self.llama_chat_base_url: str = os.environ.get("LLAMA_CHAT_BASE_URL", "").strip()
        self.llama_embed_base_url: str = os.environ.get("LLAMA_EMBED_BASE_URL", "").strip()
        self.chat_model_id: str = os.environ.get("CHAT_MODEL_ID", "").strip()
        self.embedding_model_id: str = os.environ.get("EMBEDDING_MODEL_ID", "").strip()
        self.embedding_dim: int = _positive_int_setting("EMBEDDING_DIM", "384")

        if self.star_isle_env == "production":
            self._validate_production_configuration()

        # 路径
        self.db_path: Path = self.data_root / "db" / "star-isle.db"
        self.objects_root: Path = self.data_root / "objects"
        self.originals_dir: Path = self.objects_root / "originals"
        self.thumbnails_dir: Path = self.objects_root / "thumbnails"
        self.mail_attachments_dir: Path = self.objects_root / "mail-attachments"
        self.mail_raw_dir: Path = self.objects_root / "mail-raw"

    def _validate_production_configuration(self) -> None:
        """Reject incomplete production security settings without exposing values."""

        failures: list[str] = []
        if not _has_required_secret_bytes(self.auth_jwt_signing_key):
            failures.append("AUTH_JWT_SIGNING_KEY:min-32-bytes-nonblank-trimmed-non-development-placeholder")
        for secret in self.auth_jwt_previous_keys.values():
            if not _has_required_secret_bytes(secret):
                failures.append("AUTH_JWT_PREVIOUS_KEYS_JSON:old-secret-min-32-bytes-nonblank-trimmed-non-development-placeholder")
                break
        if not _has_required_secret_bytes(self.mail_hmac_key):
            failures.append("MAIL_HMAC_KEY:min-32-bytes-nonblank-trimmed-non-development-placeholder")
        for secret in self.mail_hmac_previous_keys.values():
            if not _has_required_secret_bytes(secret):
                failures.append("MAIL_HMAC_PREVIOUS_KEYS_JSON:old-secret-min-32-bytes-nonblank-trimmed-non-development-placeholder")
                break
        if not _is_argon2id_hash(self.admin_bootstrap_password_hash):
            failures.append("ADMIN_BOOTSTRAP_PASSWORD_HASH:parseable-argon2id")
        if not self.cookie_secure:
            failures.append("COOKIE_SECURE:true")
        if failures:
            raise ValueError("invalid production configuration: " + "; ".join(failures))

    def ensure_dirs(self) -> None:
        for d in (
            self.db_path.parent,
            self.originals_dir,
            self.thumbnails_dir,
            self.mail_attachments_dir,
            self.mail_raw_dir,
        ):
            d.mkdir(parents=True, exist_ok=True)


settings = Settings()
