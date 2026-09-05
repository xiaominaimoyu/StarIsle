"""Local API smoke: upload -> worker indexing -> retrieval -> streamed RAG.

Run from ``apps/api`` with ``python -B -m tests.e2e_smoke``. This harness
always owns and removes its system-temporary data root, and intentionally emits
only stable stage names and safe counters.
"""

from __future__ import annotations

import io
import json
import os
import sys
from contextlib import contextmanager, redirect_stderr, redirect_stdout
from pathlib import Path

from tests._isolation import owned_temporary_directory


E2E_PASSWORD = "e2e-password-123"
E2E_PASSWORD_HASH = (
    "$argon2id$v=19$m=65536,t=3,p=4$qffCl1ldJ3CS1YJJgBWCDQ$"
    "Q01W2jKlaL8mT3zpEfUyhl/gtniKxcmw9FfL3HsQe2w"
)


class SmokeFailure(RuntimeError):
    """A stable, non-sensitive failure used for all smoke assertions."""


def _require(condition: bool, stage: str) -> None:
    if not condition:
        raise SmokeFailure(stage)


def _configure_environment(temporary_root: Path) -> None:
    data_root = temporary_root / "data"
    data_host_root = temporary_root / "data-host"
    os.environ.update(
        {
            "DATA_ROOT": str(data_root),
            "DATA_HOST_ROOT": str(data_host_root),
            "AUTH_JWT_SIGNING_KEY": "e2e-test-only-jwt-key-0123456789abcdef",
            "AUTH_JWT_ACTIVE_KID": "e2e",
            "COOKIE_SECURE": "false",
            "MAIL_HMAC_KEY": "e2e-test-only-mail-hmac-key",
            "LLAMA_CHAT_BASE_URL": "",
            "LLAMA_EMBED_BASE_URL": "",
            "EMBEDDING_MODEL_ID": "",
            "PYTHON_DOTENV_DISABLED": "1",
            "ADMIN_BOOTSTRAP_PASSWORD_HASH": E2E_PASSWORD_HASH,
        }
    )


@contextmanager
def _discard_application_output():
    """Do not let application observability/logging escape the smoke harness."""

    with open(os.devnull, "w", encoding="utf-8") as discarded:
        with redirect_stdout(discarded), redirect_stderr(discarded):
            yield


def _run() -> int:
    with owned_temporary_directory("starisle-e2e-") as temporary:
        _configure_environment(Path(temporary))

        # Imports must remain after the test-only environment is installed.
        # Some optional application dependencies emit diagnostics at import
        # time, so keep those private to this harness as well.
        with _discard_application_output():
            from fastapi.testclient import TestClient

            from app.db import get_conn, init_db
            from app.main import app
            from app.services import rag_engine
            from app.services.jobs import claim_next_job, succeed_job
            from app.worker_runner import process_ingest

            init_db()
        with TestClient(app) as client:
            with _discard_application_output():
                csrf_response = client.get("/api/v1/auth/csrf")
            _require(csrf_response.status_code == 200, "csrf")
            csrf_token = csrf_response.json().get("csrfToken")
            _require(isinstance(csrf_token, str) and bool(csrf_token), "csrf")

            with _discard_application_output():
                login_response = client.post(
                    "/api/v1/auth/login",
                    json={"password": E2E_PASSWORD},
                    headers={"X-CSRF-Token": csrf_token, "Origin": "http://testserver"},
                )
            _require(login_response.status_code == 200, "login")

            document = (
                "# StarIsle smoke document\n\n"
                "Private retrieval is available only after the worker marks the document ready.\n\n"
                "A backup must be verified through an isolated recovery drill."
            )
            with _discard_application_output():
                upload_response = client.post(
                    "/api/v1/knowledge/documents",
                    files={"file": ("smoke.md", io.BytesIO(document.encode()), "text/markdown")},
                    headers={"X-CSRF-Token": csrf_token, "Origin": "http://testserver"},
                )
            _require(upload_response.status_code == 202, "upload")
            document_id = upload_response.json().get("documentId")
            _require(isinstance(document_id, str) and bool(document_id), "upload")
            print("STAGE upload accepted")

            with _discard_application_output():
                with get_conn() as connection:
                    job = claim_next_job(connection, "e2e-worker")
                    _require(job is not None, "worker-claim")
                    process_ingest(connection, job["id"], json.loads(job["payload"]))
                    succeed_job(connection, job["id"])
            print("STAGE worker complete")

            with _discard_application_output():
                listing_response = client.get("/api/v1/knowledge/documents")
            _require(listing_response.status_code == 200, "ready-list")
            items = listing_response.json().get("items")
            _require(isinstance(items, list), "ready-list")
            matched = next((item for item in items if item.get("id") == document_id), None)
            _require(isinstance(matched, dict) and matched.get("indexState") == "ready", "ready")
            print("STAGE ready confirmed")

            with _discard_application_output():
                with get_conn() as connection:
                    hits = rag_engine.search(connection, "isolated backup verification")
            _require(bool(hits), "retrieval")
            print(f"STAGE retrieval nonempty count={len(hits)}")

            with _discard_application_output():
                query_response = client.post(
                    "/api/v1/rag/query",
                    json={"query": "What verification is required?"},
                    headers={"X-CSRF-Token": csrf_token, "Origin": "http://testserver"},
                )
            _require(query_response.status_code == 200, "rag-status")
            events: list[dict[str, object]] = []
            for line in query_response.text.strip().split("\n\n"):
                if line.startswith("data: "):
                    event = json.loads(line[6:])
                    _require(isinstance(event, dict), "rag-event")
                    events.append(event)
            kinds = {str(event.get("event", "")) for event in events}
            _require("stage" in kinds and "done" in kinds, "rag-events")
            token_count = sum(1 for event in events if event.get("event") == "token" and event.get("token"))
            citation_count = sum(1 for event in events if event.get("event") == "citation")
            _require(token_count > 0, "rag-token")
            _require(citation_count > 0, "rag-citation")
            print(f"STAGE rag complete events={len(events)} tokens={token_count} citations={citation_count}")
    print("E2E smoke passed")
    return 0


def main() -> int:
    try:
        return _run()
    except Exception:
        # A local harness must not reflect exception text, response bodies,
        # paths, credentials, document identifiers, or generated answers.
        print("E2E smoke failed", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
