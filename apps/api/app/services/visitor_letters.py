"""把云上的访客留言取回这台电脑，并在确认后让云端删掉副本。"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from urllib.parse import urlsplit

import httpx

from ..db import get_conn
from ..security import iso, now_utc

PULL_PATH = "/api/visitor-messages/pull"
DEFAULT_TIMEOUT = 8.0


class PullProtocolError(Exception):
    """云端返回了无法收下的收取结果。"""


@dataclass(frozen=True)
class PullReport:
    code: str
    imported: int = 0


def pull_base_url() -> str | None:
    raw = os.environ.get("VISITOR_PULL_BASE_URL", "").strip()
    if not raw:
        return None
    try:
        parsed = urlsplit(raw)
    except ValueError:
        return None
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
        or parsed.path not in {"", "/"}
    ):
        return None
    return f"https://{parsed.hostname}" if parsed.port is None else None


def pull_key() -> str:
    return os.environ.get("VISITOR_PULL_KEY", "").strip()


def _pending_ack_ids() -> list[str]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT id FROM visitor_letter WHERE cloud_ack_at IS NULL ORDER BY imported_at, id"
        ).fetchall()
    return [str(row["id"]) for row in rows]


def _store_messages(messages: list[dict]) -> list[str]:
    stored: list[str] = []
    moment = iso(now_utc())
    with get_conn() as conn:
        for message in messages:
            message_id = str(message.get("id") or "")
            if not message_id:
                continue
            exists = conn.execute("SELECT 1 FROM visitor_letter WHERE id=?", (message_id,)).fetchone()
            if exists is not None:
                continue
            conn.execute(
                "INSERT INTO visitor_letter "
                "(id, received_at, imported_at, name, contact, body, read_at, archived_at, deleted_at, cloud_ack_at) "
                "VALUES (?, ?, ?, ?, ?, ?, NULL, NULL, NULL, NULL)",
                (
                    message_id,
                    str(message.get("createdAt") or moment),
                    moment,
                    str(message.get("name") or ""),
                    str(message.get("contact") or ""),
                    str(message.get("body") or ""),
                ),
            )
            stored.append(message_id)
    return stored


def _mark_acked(ids: list[str]) -> None:
    if not ids:
        return
    moment = iso(now_utc())
    with get_conn() as conn:
        conn.executemany(
            "UPDATE visitor_letter SET cloud_ack_at=? WHERE id=? AND cloud_ack_at IS NULL",
            [(moment, message_id) for message_id in ids],
        )


def _post_pull(client: httpx.Client, url: str, key: str, acknowledge: list[str]) -> dict:
    response = client.post(
        url,
        headers={"Authorization": f"Bearer {key}", "User-Agent": "StarIsle-Desk/1"},
        json={"acknowledge": acknowledge},
    )
    if response.status_code == 401:
        raise PermissionError("VISITOR_PULL_UNAUTHORIZED")
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, dict) or not isinstance(payload.get("messages"), list):
        raise PullProtocolError("VISITOR_PULL_BAD_RESPONSE")
    return payload


def pull_visitor_letters(client: httpx.Client | None = None) -> PullReport:
    """收取新留言。云端不可达时保留本地来信，不抛给启动流程。"""

    base = pull_base_url()
    key = pull_key()
    if not base or not key:
        return PullReport("VISITOR_PULL_SKIPPED")
    url = base + PULL_PATH
    owns_client = client is None
    http = client or httpx.Client(timeout=DEFAULT_TIMEOUT)
    imported = 0
    try:
        acknowledge = _pending_ack_ids()[:50]
        for _ in range(6):
            payload = _post_pull(http, url, key, acknowledge)
            _mark_acked(acknowledge)
            messages = [item for item in payload["messages"] if isinstance(item, dict)]
            stored = _store_messages(messages)
            imported += len(stored)
            acknowledge = stored
            if not stored:
                break
        if acknowledge:
            _post_pull(http, url, key, acknowledge)
            _mark_acked(acknowledge)
        return PullReport("VISITOR_PULL_OK", imported)
    except PermissionError:
        return PullReport("VISITOR_PULL_UNAUTHORIZED")
    except (httpx.HTTPError, PullProtocolError, json.JSONDecodeError, OSError):
        return PullReport("VISITOR_PULL_OFFLINE")
    finally:
        if owns_client:
            http.close()
