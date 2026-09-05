"""隐私安全 JSONL、请求标识与维护状态的隔离回归。"""

from __future__ import annotations

import json
import re
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient


from app import deps, observability, worker_runner  # noqa: E402
from app.config import settings  # noqa: E402
from app.db import get_conn, init_db  # noqa: E402
import app.main as main_module  # noqa: E402
from app.main import app  # noqa: E402
from app.routers import mail, misc  # noqa: E402
from app.services import asset_gc, mail_retention  # noqa: E402
from app.services.jobs import enqueue_job  # noqa: E402


_HEX_12 = re.compile(r"^[0-9a-f]{12}$")


def _set_data_root(monkeypatch: pytest.MonkeyPatch, root: Path) -> None:
    monkeypatch.setattr(settings, "data_root", root)
    monkeypatch.setattr(settings, "db_path", root / "db" / "star-isle.db")
    monkeypatch.setattr(settings, "objects_root", root / "objects")
    monkeypatch.setattr(settings, "originals_dir", root / "objects" / "originals")
    monkeypatch.setattr(settings, "thumbnails_dir", root / "objects" / "thumbnails")
    monkeypatch.setattr(settings, "mail_attachments_dir", root / "objects" / "mail-attachments")
    monkeypatch.setattr(settings, "mail_raw_dir", root / "objects" / "mail-raw")


def _json_lines(output: str) -> list[dict]:
    lines = [line for line in output.splitlines() if line]
    return [json.loads(line) for line in lines]


def test_jsonl_helper_is_single_line_and_rejects_untrusted_values(capsys: pytest.CaptureFixture[str]):
    record = observability.emit_event(
        component="api",
        event="request_completed",
        result="ok",
        request_id="accepted-id_01",
        method="GET",
        route="/files/{file_id}",
        status=200,
    )
    captured = capsys.readouterr()
    assert captured.err == ""
    lines = _json_lines(captured.out)
    assert len(lines) == 1
    assert lines[0] == record
    assert lines[0]["requestId"] == "accepted-id_01"

    canary = "PRIVATE_CANARY C:\\private\\object\n"
    with pytest.raises(ValueError):
        observability.emit_event(
            component="api",
            event="request_completed",
            result="ok",
            request_id="accepted-id_01",
            route=canary,
        )
    with pytest.raises(ValueError):
        observability.emit_event(
            component="api",
            event="request_completed",
            result="ok",
            request_id=[canary],  # type: ignore[arg-type]
        )
    with pytest.raises(ValueError):
        observability.emit_event(
            component="api",
            event="request_completed",
            result="ok",
            request_id="accepted-id_01",
            route="/" + ("a" * 160),
        )
    with pytest.raises(TypeError):
        observability.emit_event(  # type: ignore[call-arg]
            component="api",
            event="request_completed",
            result="ok",
            secret=canary,
        )
    rejected = capsys.readouterr()
    assert rejected.out == "" and rejected.err == ""


def test_route_template_prefers_only_a_valid_canonical_scope_value() -> None:
    trusted_key = "starisle.canonical_route_template"
    fallback_route = SimpleNamespace(path="/files/{file_id}")
    assert observability.route_template(
        SimpleNamespace(scope={trusted_key: "/api/v1/files/{file_id}", "route": fallback_route})
    ) == "/api/v1/files/{file_id}"

    canary = "PRIVATE_CANARY"
    route = observability.route_template(
        SimpleNamespace(
            scope={
                trusted_key: f"/api/v1/private?query={canary}",
                "route": fallback_route,
                "path": "/api/v1/private",
                "raw_path": f"/api/v1/private/{canary}".encode(),
                "query_string": f"query={canary}".encode(),
                "body": canary.encode(),
            }
        )
    )
    assert route == "/__unmatched__"
    assert canary not in route
    assert observability.route_template(SimpleNamespace(scope={"path": "/api/v1/unknown"})) == "/__unmatched__"


def test_request_ids_use_valid_state_and_never_reexpose_invalid_header(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    _set_data_root(monkeypatch, tmp_path / "data")
    init_db()
    client = TestClient(app)
    valid = "trusted.request-01:part"
    valid_response = client.get("/api/v1/health/live", headers={"X-Request-Id": valid})
    assert valid_response.status_code == 200
    assert valid_response.headers["X-Request-Id"] == valid

    for unsafe in ("", " contains-space", "line\nPRIVATE_CANARY", "a" * 65):
        response = client.get("/api/v1/auth/me", headers={"X-Request-Id": unsafe})
        assert response.status_code == 401
        safe_id = response.headers["X-Request-Id"]
        assert _HEX_12.fullmatch(safe_id)
        assert response.json()["error"]["requestId"] == safe_id
        if unsafe:
            assert unsafe not in response.text

    direct_state = SimpleNamespace(state=SimpleNamespace(request_id=valid), headers={"x-request-id": "ignored"})
    assert deps.get_request_id(direct_state) == valid
    direct_header = SimpleNamespace(state=SimpleNamespace(), headers={"x-request-id": "bad\nPRIVATE_CANARY"})
    assert _HEX_12.fullmatch(deps.get_request_id(direct_header))
    direct_unicode = SimpleNamespace(state=SimpleNamespace(), headers={"x-request-id": "\u4e2d\u6587"})
    assert _HEX_12.fullmatch(deps.get_request_id(direct_unicode))

    output = capsys.readouterr().out
    assert "PRIVATE_CANARY" not in output
    events = _json_lines(output)
    assert any(event["component"] == "api" and event["requestId"] == valid for event in events)


def test_unhandled_exception_uses_fixed_audit_response_and_jsonl(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    _set_data_root(monkeypatch, tmp_path / "data")
    init_db()
    canary = "PRIVATE_CANARY C:\\private\\source\n"
    captured_audit: list[dict] = []

    def record_audit(*_args, **kwargs):
        captured_audit.append(kwargs)

    monkeypatch.setattr(main_module, "audit", record_audit)
    route_count = len(app.router.routes)

    def broken_route():
        raise RuntimeError(canary)

    app.add_api_route("/api/v1/__observability_boom", broken_route, methods=["GET"])
    try:
        client = TestClient(app, raise_server_exceptions=False)
        response = client.get(
            "/api/v1/__observability_boom?private=PRIVATE_CANARY",
            headers={"X-Request-Id": "safe-error-id"},
        )
    finally:
        del app.router.routes[route_count:]

    assert response.status_code == 500
    assert response.headers["X-Request-Id"] == "safe-error-id"
    assert response.json() == {
        "error": {
            "code": "INTERNAL",
            "message": "服务异常,请稍后重试。",
            "requestId": "safe-error-id",
        }
    }
    assert captured_audit == [
        {"resource_type": "api", "request_id": "safe-error-id", "result": "INTERNAL"}
    ]
    captured = capsys.readouterr()
    combined = captured.out + captured.err + response.text
    assert canary not in combined and "?private=" not in combined
    events = _json_lines(captured.out)
    assert events == [
        {
            "timestamp": events[0]["timestamp"],
            "level": "ERROR",
            "component": "api",
            "event": "request_completed",
            "result": "server_error",
            "requestId": "safe-error-id",
            "method": "GET",
            "route": "/api/v1/__observability_boom",
            "status": 500,
        }
    ]

    monkeypatch.setattr(
        main_module,
        "audit",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError(canary)),
    )
    route_count = len(app.router.routes)
    app.add_api_route("/api/v1/__observability_boom_again", broken_route, methods=["GET"])
    try:
        response = TestClient(app, raise_server_exceptions=False).get("/api/v1/__observability_boom_again")
    finally:
        del app.router.routes[route_count:]
    assert response.status_code == 500
    assert response.json()["error"]["code"] == "INTERNAL"
    after_audit_failure = capsys.readouterr()
    assert canary not in after_audit_failure.out + after_audit_failure.err + response.text


def test_ready_events_keep_contract_and_suppress_failures(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    _set_data_root(monkeypatch, tmp_path / "data")
    init_db()
    client = TestClient(app)
    canary = "PRIVATE_CANARY C:\\private\\health\n"

    class StorageTrue:
        def healthcheck(self) -> bool:
            return True

    monkeypatch.setattr(misc, "storage", StorageTrue())
    ready = client.get("/api/v1/health/ready")
    assert ready.status_code == 200
    assert ready.json() == {"status": "ready", "checks": {"db": True, "storage": True}}

    class StorageFalse:
        def healthcheck(self) -> bool:
            return False

    monkeypatch.setattr(misc, "storage", StorageFalse())
    not_ready = client.get("/api/v1/health/ready")
    assert not_ready.status_code == 503
    assert not_ready.json() == {"status": "not_ready", "checks": {"db": True, "storage": False}}

    def broken_conn():
        raise RuntimeError(canary)

    class StorageBroken:
        def healthcheck(self) -> bool:
            raise RuntimeError(canary)

    monkeypatch.setattr(misc, "get_conn", broken_conn)
    monkeypatch.setattr(misc, "storage", StorageBroken())
    unavailable = client.get("/api/v1/health/ready")
    assert unavailable.status_code == 503
    assert unavailable.json() == {"status": "not_ready", "checks": {"db": False, "storage": False}}
    captured = capsys.readouterr()
    assert canary not in captured.out + captured.err
    events = _json_lines(captured.out)
    health_events = [event for event in events if event["component"] == "health"]
    assert [event["result"] for event in health_events] == ["ready", "not_ready", "not_ready"]
    assert health_events[-1]["db"] is False and health_events[-1]["storage"] is False


def test_worker_and_maintenance_events_are_sanitized(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    _set_data_root(monkeypatch, tmp_path / "data")
    init_db()
    canary = "PRIVATE_CANARY C:\\private\\worker\n"
    with get_conn() as conn:
        enqueue_job(conn, "ingest", {"documentId": canary})
    monkeypatch.setattr(worker_runner, "process_ingest", lambda *_args: None)
    assert worker_runner.run_once("test-worker")
    observability.emit_worker_event(event="job_cancelled", result="cancelled", job_type="ingest")
    with get_conn() as conn:
        enqueue_job(conn, "not-a-real-job", {"documentId": canary})
    assert worker_runner.run_once("test-worker")

    monkeypatch.setattr(
        worker_runner,
        "run_mail_retention",
        lambda: (_ for _ in ()).throw(RuntimeError(canary)),
    )
    monkeypatch.setattr(worker_runner, "run_asset_gc", lambda: None)
    assert worker_runner.run_maintenance() is False

    mail_retention.run_mail_retention()
    asset_gc.run_asset_gc()
    monkeypatch.setattr(mail_retention, "audit", lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError(canary)))
    mail_retention.run_mail_retention()
    monkeypatch.setattr(asset_gc, "audit", lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError(canary)))
    asset_gc.run_asset_gc()

    captured = capsys.readouterr()
    assert canary not in captured.out + captured.err
    events = _json_lines(captured.out)
    assert any(event["event"] == "job_succeeded" for event in events)
    assert any(event["event"] == "job_cancelled" and event["result"] == "cancelled" for event in events)
    job = next(event for event in events if event["event"] == "job_failed")
    assert job["jobType"] == "unknown" and job["errorCode"] == "UNKNOWN_JOB_TYPE"
    assert any(event["event"] == "mail_retention_failed" for event in events)
    mail_events = [event for event in events if event["component"] == "mail_retention"]
    gc_events = [event for event in events if event["component"] == "asset_gc"]
    assert [event["auditStatus"] for event in mail_events] == ["written", "unavailable"]
    assert [event["auditStatus"] for event in gc_events] == ["written", "unavailable"]


def test_mail_receive_fallbacks_emit_fixed_private_jsonl(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    canary = "PRIVATE_CANARY C:\\private\\mail\\object\n"
    request = SimpleNamespace(
        state=SimpleNamespace(request_id="mail-audit-01"),
        headers={"x-request-id": canary},
    )

    def broken_audit(*_args, **_kwargs):
        raise RuntimeError(canary)

    class BrokenStorage:
        def delete(self, _subdir: str, _object_key: str) -> None:
            raise RuntimeError(canary)

    monkeypatch.setattr(mail, "audit", broken_audit)
    monkeypatch.setattr(mail, "storage", BrokenStorage())

    mail._safe_verified_audit("mail_receive", request, "ok")
    mail._cleanup_saved_objects([("mail-raw", canary)])
    mail._cleanup_saved_objects([("invalid-subdir", canary)])

    captured = capsys.readouterr()
    assert canary not in captured.out + captured.err
    events = _json_lines(captured.out)
    assert events == [
        {
            "timestamp": events[0]["timestamp"],
            "level": "ERROR",
            "component": "mail",
            "event": "audit_unavailable",
            "result": "failed",
            "requestId": "mail-audit-01",
        },
        {
            "timestamp": events[1]["timestamp"],
            "level": "ERROR",
            "component": "mail",
            "event": "object_cleanup_unavailable",
            "result": "failed",
        },
        {
            "timestamp": events[2]["timestamp"],
            "level": "ERROR",
            "component": "mail",
            "event": "object_cleanup_unavailable",
            "result": "failed",
        },
    ]
