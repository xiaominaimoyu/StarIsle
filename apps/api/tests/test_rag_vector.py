"""RAG vector 契约：迁移、远程响应、原子替换与 top20→top6。"""

from __future__ import annotations

import asyncio
import json
import sqlite3
from datetime import timedelta
from pathlib import Path

import pytest

from app.config import settings
from app.db import (
    MIGRATION_V2,
    MIGRATION_V3,
    SCHEMA_V1,
    SCHEMA_V3,
    SchemaVersionError,
    get_conn,
    init_db,
)
from app.security import iso, now_utc
from app.services import rag_engine, vector_store
from app.services.jobs import MAX_ATTEMPTS, enqueue_job, recover_stale_running_jobs
from app import worker_runner


@pytest.fixture
def isolated_db(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    root = tmp_path / "data"
    monkeypatch.setattr(settings, "data_root", root)
    monkeypatch.setattr(settings, "db_path", root / "db" / "star-isle.db")
    monkeypatch.setattr(settings, "objects_root", root / "objects")
    monkeypatch.setattr(settings, "originals_dir", root / "objects" / "originals")
    monkeypatch.setattr(settings, "thumbnails_dir", root / "objects" / "thumbnails")
    monkeypatch.setattr(settings, "mail_attachments_dir", root / "objects" / "mail-attachments")
    monkeypatch.setattr(settings, "mail_raw_dir", root / "objects" / "mail-raw")
    monkeypatch.setattr(settings, "llama_embed_base_url", "")
    monkeypatch.setattr(settings, "embedding_model_id", "")
    monkeypatch.setattr(settings, "embedding_dim", 4)
    return root


def _insert_document(conn, document_id: str, *, state: str = "ready", deleted: bool = False) -> None:
    now = iso(now_utc())
    asset_id = f"asset-{document_id}"
    conn.execute(
        "INSERT INTO asset (id, original_name, mime_type, size, object_key, sha256, storage_class, status, created_at) "
        "VALUES (?, ?, 'text/plain', 1, ?, 'h', 'knowledge', 'active', ?)",
        (asset_id, f"{document_id}.txt", f"{document_id}.txt", now),
    )
    conn.execute(
        "INSERT INTO knowledge_document (id, asset_id, title, document_type, hash, index_state, created_at, deleted_at) "
        "VALUES (?, ?, ?, 'txt', 'h', ?, ?, ?)",
        (document_id, asset_id, document_id, state, now, now if deleted else None),
    )


def test_v1_forward_migration_and_future_version_rejection(isolated_db: Path):
    isolated_db.joinpath("db").mkdir(parents=True)
    conn = sqlite3.connect(settings.db_path)
    try:
        conn.executescript(SCHEMA_V1)
        conn.execute(
            "CREATE TABLE schema_version (id INTEGER PRIMARY KEY, version INTEGER NOT NULL, applied_at TEXT NOT NULL)"
        )
        conn.execute("INSERT INTO schema_version VALUES (1, 1, '2026-01-01T00:00:00+00:00')")
        conn.commit()
    finally:
        conn.close()

    init_db()
    with get_conn() as conn:
        assert conn.execute("SELECT version FROM schema_version WHERE id=1").fetchone()[0] == 5
        assert conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='chunk_embedding'"
        ).fetchone()
        assert conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='index' AND name='idx_chunk_document_id'"
        ).fetchone()
        assert "retention_queued_at" in {
            row["name"] for row in conn.execute("PRAGMA table_info(mail_message)")
        }
        assert "raw_state" in {
            row["name"] for row in conn.execute("PRAGMA table_info(mail_message)")
        }
        assert conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='mail_raw'"
        ).fetchone()
        assert conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='index' AND name='idx_mail_raw_object_key'"
        ).fetchone()
        for index in (
            "idx_mail_retention",
            "idx_mail_attachment_message",
            "idx_mail_attachment_asset",
            "idx_mail_nonce_expires",
        ):
            assert conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='index' AND name=?",
                (index,),
            ).fetchone()
        assert "storage_class" in {row["name"] for row in conn.execute("PRAGMA table_info(asset)")}
        assert conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='index' AND name='idx_asset_gc_due'"
        ).fetchone()
        conn.execute("UPDATE schema_version SET version=6 WHERE id=1")

    with pytest.raises(SchemaVersionError):
        init_db()


def test_v2_v3_forward_migration_and_fresh_schema_are_v5(isolated_db: Path):
    isolated_db.joinpath("db").mkdir(parents=True)
    conn = sqlite3.connect(settings.db_path)
    try:
        conn.executescript(SCHEMA_V1)
        for statement in MIGRATION_V2:
            conn.execute(statement)
        conn.execute(
            "CREATE TABLE schema_version (id INTEGER PRIMARY KEY, version INTEGER NOT NULL, applied_at TEXT NOT NULL)"
        )
        conn.execute("INSERT INTO schema_version VALUES (1, 2, '2026-01-01T00:00:00+00:00')")
        conn.commit()
    finally:
        conn.close()

    init_db()
    init_db()
    with get_conn() as conn:
        assert conn.execute("SELECT version FROM schema_version WHERE id=1").fetchone()[0] == 5
        assert conn.execute(
            "SELECT 1 FROM pragma_table_info('mail_message') WHERE name='retention_queued_at'"
        ).fetchone()
        assert conn.execute(
            "SELECT 1 FROM pragma_table_info('mail_message') WHERE name='raw_state'"
        ).fetchone()

    fresh = isolated_db.parent / "fresh-data"
    settings.data_root = fresh
    settings.db_path = fresh / "db" / "star-isle.db"
    settings.objects_root = fresh / "objects"
    settings.originals_dir = fresh / "objects" / "originals"
    settings.thumbnails_dir = fresh / "objects" / "thumbnails"
    settings.mail_attachments_dir = fresh / "objects" / "mail-attachments"
    settings.mail_raw_dir = fresh / "objects" / "mail-raw"
    init_db()
    init_db()
    with get_conn() as conn:
        assert conn.execute("SELECT version FROM schema_version WHERE id=1").fetchone()[0] == 5
        assert conn.execute(
            "SELECT 1 FROM pragma_table_info('mail_message') WHERE name='retention_queued_at'"
        ).fetchone()
        assert conn.execute(
            "SELECT 1 FROM pragma_table_info('mail_message') WHERE name='raw_state'"
        ).fetchone()

    legacy_v3 = isolated_db.parent / "legacy-v3-data"
    settings.data_root = legacy_v3
    settings.db_path = legacy_v3 / "db" / "star-isle.db"
    settings.objects_root = legacy_v3 / "objects"
    settings.originals_dir = legacy_v3 / "objects" / "originals"
    settings.thumbnails_dir = legacy_v3 / "objects" / "thumbnails"
    settings.mail_attachments_dir = legacy_v3 / "objects" / "mail-attachments"
    settings.mail_raw_dir = legacy_v3 / "objects" / "mail-raw"
    legacy_v3.joinpath("db").mkdir(parents=True)
    conn = sqlite3.connect(settings.db_path)
    try:
        conn.executescript(SCHEMA_V3)
        for statement in MIGRATION_V2 + MIGRATION_V3[1:]:
            conn.execute(statement)
        conn.execute(
            "CREATE TABLE schema_version (id INTEGER PRIMARY KEY, version INTEGER NOT NULL, applied_at TEXT NOT NULL)"
        )
        conn.execute("INSERT INTO schema_version VALUES (1, 3, '2026-01-01T00:00:00+00:00')")
        conn.execute(
            "INSERT INTO mail_message (id, message_id, from_addr, received_at, read_flag) "
            "VALUES ('legacy-mail', 'legacy@example.test', 'sender@example.test', '2026-01-01T00:00:00+00:00', 0)"
        )
        conn.commit()
    finally:
        conn.close()

    init_db()
    with get_conn() as conn:
        assert conn.execute("SELECT version FROM schema_version WHERE id=1").fetchone()[0] == 5
        legacy = conn.execute(
            "SELECT raw_state FROM mail_message WHERE id='legacy-mail'"
        ).fetchone()
        assert legacy and legacy["raw_state"] == "legacy"
        assert conn.execute("SELECT 1 FROM mail_raw WHERE message_id='legacy-mail'").fetchone() is None


def test_v4_missing_raw_layout_is_rejected_explicitly(isolated_db: Path):
    del isolated_db
    init_db()
    with get_conn() as conn:
        conn.execute("DROP INDEX idx_mail_raw_object_key")
    with pytest.raises(SchemaVersionError, match="raw 对象索引"):
        init_db()


def test_v1_legacy_ready_rows_are_preserved_but_queued_for_rebuild(isolated_db: Path):
    isolated_db.joinpath("db").mkdir(parents=True)
    conn = sqlite3.connect(settings.db_path)
    try:
        conn.executescript(SCHEMA_V1)
        conn.execute(
            "CREATE TABLE schema_version (id INTEGER PRIMARY KEY, version INTEGER NOT NULL, applied_at TEXT NOT NULL)"
        )
        conn.execute("INSERT INTO schema_version VALUES (1, 1, '2026-01-01T00:00:00+00:00')")
        conn.execute(
            "CREATE TABLE chunk_embedding (chunk_id TEXT PRIMARY KEY, embedding BLOB NOT NULL CHECK(length(embedding) % 4 = 0))"
        )
        conn.execute("CREATE INDEX idx_chunk_document_id ON chunk(document_id)")
        now = "2026-01-01T00:00:00+00:00"
        for doc_id in ("legacy", "complete"):
            conn.execute(
                "INSERT INTO asset (id, original_name, mime_type, size, object_key, sha256, status, created_at) "
                "VALUES (?, 'x.txt', 'text/plain', 1, ?, 'h', 'active', ?)",
                (f"asset-{doc_id}", f"{doc_id}.txt", now),
            )
            conn.execute(
                "INSERT INTO knowledge_document (id, asset_id, title, document_type, hash, index_state, created_at) "
                "VALUES (?, ?, ?, 'txt', 'h', 'ready', ?)",
                (doc_id, f"asset-{doc_id}", doc_id, now),
            )
            conn.execute(
                "INSERT INTO chunk (id, document_id, seq, text, token_count, hash) VALUES (?, ?, 0, 'text', 1, 'h')",
                (f"chunk-{doc_id}", doc_id),
            )
        conn.execute(
            "INSERT INTO vector_ref (chunk_id, store, vector_id, model_id, dim) "
            "VALUES ('chunk-legacy', 'keyword', 'legacy-key', NULL, NULL)"
        )
        conn.execute(
            "INSERT INTO vector_ref (chunk_id, store, vector_id, model_id, dim) "
            "VALUES ('chunk-complete', 'sqlite-vec', 'chunk-complete', 'hash-v1', 2)"
        )
        conn.execute(
            "INSERT INTO chunk_embedding (chunk_id, embedding) VALUES ('chunk-complete', ?)",
            (sqlite3.Binary(b"\x00\x00\x80?\x00\x00\x00\x00"),),
        )
        conn.commit()
    finally:
        conn.close()

    init_db()
    with get_conn() as conn:
        legacy = conn.execute(
            "SELECT index_state, index_error_code FROM knowledge_document WHERE id='legacy'"
        ).fetchone()
        complete = conn.execute(
            "SELECT index_state, index_error_code FROM knowledge_document WHERE id='complete'"
        ).fetchone()
        assert legacy and tuple(legacy) == ("queued", "INDEX_REBUILD_REQUIRED")
        assert complete and tuple(complete) == ("ready", None)
        assert conn.execute("SELECT 1 FROM chunk WHERE id='chunk-legacy'").fetchone()
        assert conn.execute("SELECT store FROM vector_ref WHERE chunk_id='chunk-legacy'").fetchone()[0] == "keyword"
        assert conn.execute("SELECT 1 FROM chunk_embedding WHERE chunk_id='chunk-legacy'").fetchone() is None


def test_v2_missing_vector_layout_is_rejected_explicitly(isolated_db: Path):
    del isolated_db
    init_db()
    with get_conn() as conn:
        conn.execute("DROP INDEX idx_chunk_document_id")
    with pytest.raises(SchemaVersionError, match="idx_chunk_document_id"):
        init_db()

    with get_conn() as conn:
        conn.execute("CREATE INDEX idx_chunk_document_id ON chunk(document_id)")
        conn.execute("DROP TABLE chunk_embedding")
    with pytest.raises(SchemaVersionError, match="chunk_embedding"):
        init_db()


def test_remote_embedding_is_reordered_and_invalid_count_never_hashes(
    isolated_db: Path, monkeypatch: pytest.MonkeyPatch
):
    del isolated_db
    monkeypatch.setattr(settings, "llama_embed_base_url", "http://embed.internal")
    monkeypatch.setattr(settings, "embedding_model_id", "embed-test")
    monkeypatch.setattr(settings, "embedding_dim", 3)

    class Response:
        status_code = 200

        def __init__(self, payload):
            self.payload = payload

        def json(self):
            return self.payload

    class Client:
        def __init__(self, payload):
            self.payload = payload

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def post(self, *_args, **_kwargs):
            return Response(self.payload)

    good = {
        "model": "embed-test",
        "data": [
            {"index": 1, "embedding": [0.0, 1.0, 0.0]},
            {"index": 0, "embedding": [1.0, 0.0, 0.0]},
        ],
    }
    monkeypatch.setattr(rag_engine.httpx, "Client", lambda **_kwargs: Client(good))
    assert rag_engine.embed_texts(["first", "second"]) == [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0]]

    bad = {"model": "embed-test", "data": [{"index": 0, "embedding": [1.0, 0.0, 0.0]}]}
    monkeypatch.setattr(rag_engine.httpx, "Client", lambda **_kwargs: Client(bad))
    with pytest.raises(rag_engine.EmbeddingError, match="EMBED_RESPONSE_COUNT"):
        rag_engine.embed_texts(["first", "second"])


def test_remote_embedding_rejects_http_and_response_contract_matrix(
    isolated_db: Path, monkeypatch: pytest.MonkeyPatch
):
    del isolated_db
    monkeypatch.setattr(settings, "llama_embed_base_url", "http://embed.internal")
    monkeypatch.setattr(settings, "embedding_model_id", "embed-test")
    monkeypatch.setattr(settings, "embedding_dim", 3)

    class Response:
        def __init__(self, status_code: int, payload):
            self.status_code = status_code
            self.payload = payload

        def json(self):
            return self.payload

    class Client:
        def __init__(self, response):
            self.response = response

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def post(self, *_args, **_kwargs):
            return self.response

    cases = [
        (Response(500, {}), "EMBED_HTTP_RETRYABLE"),
        (Response(400, {}), "EMBED_HTTP_TERMINAL"),
        (Response(200, {"model": "wrong", "data": []}), "EMBED_RESPONSE_MODEL"),
        (
            Response(200, {"model": "embed-test", "data": [{"index": 0, "embedding": [1.0, 0.0]}]}),
            "EMBED_RESPONSE_DIM",
        ),
        (
            Response(200, {"model": "embed-test", "data": [{"index": 0, "embedding": [0.0, 0.0, 0.0]}]}),
            "EMBED_RESPONSE_ZERO",
        ),
        (
            Response(200, {"model": "embed-test", "data": [{"index": 3, "embedding": [1.0, 0.0, 0.0]}]}),
            "EMBED_RESPONSE_INDEX",
        ),
    ]
    for response, code in cases:
        monkeypatch.setattr(rag_engine.httpx, "Client", lambda **_kwargs: Client(response))
        with pytest.raises(rag_engine.EmbeddingError, match=code):
            rag_engine.embed_texts(["first"])

    monkeypatch.setattr(settings, "embedding_model_id", "")
    with pytest.raises(rag_engine.EmbeddingError, match="EMBED_MODEL_ID_REQUIRED"):
        rag_engine.embed_texts(["first"])


def test_remote_embedding_full_contract_never_falls_back_to_hash(
    isolated_db: Path, monkeypatch: pytest.MonkeyPatch
):
    del isolated_db
    monkeypatch.setattr(settings, "llama_embed_base_url", "http://embed.internal")
    monkeypatch.setattr(settings, "embedding_model_id", "embed-test")
    monkeypatch.setattr(settings, "embedding_dim", 3)
    monkeypatch.setattr(
        rag_engine,
        "_hash_embedding",
        lambda *_args: pytest.fail("远程 embedding 失败时不得回退 hash"),
    )
    real_client = rag_engine.httpx.Client

    class Response:
        status_code = 200

        def __init__(self, payload=None, *, json_error: bool = False, status_code: int = 200):
            self.payload = payload
            self.json_error = json_error
            self.status_code = status_code

        def json(self):
            if self.json_error:
                raise json.JSONDecodeError("bad", "{", 0)
            return self.payload

    class Client:
        def __init__(self, response):
            self.response = response

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def post(self, *_args, **_kwargs):
            return self.response

    vector = [1.0, 0.0, 0.0]
    cases = [
        (Response({"model": "embed-test", "data": []}), ["one"], "EMBED_RESPONSE_COUNT", False),
        (
            Response({"model": "embed-test", "data": [{"index": 0, "embedding": vector}, {"index": 1, "embedding": vector}]}),
            ["one"],
            "EMBED_RESPONSE_COUNT",
            False,
        ),
        (
            Response({"model": "embed-test", "data": [{"index": 0, "embedding": vector}, {"index": 0, "embedding": vector}]}),
            ["one", "two"],
            "EMBED_RESPONSE_INDEX",
            False,
        ),
        (
            Response({"model": "embed-test", "data": [{"index": 0, "embedding": vector}, {"index": 3, "embedding": vector}]}),
            ["one", "two"],
            "EMBED_RESPONSE_INDEX",
            False,
        ),
        (
            Response({"model": "embed-test", "data": [{"index": 0, "embedding": [float("nan"), 0.0, 0.0]}]}),
            ["one"],
            "EMBED_RESPONSE_VALUE",
            False,
        ),
        (
            Response({"model": "embed-test", "data": [{"index": 0, "embedding": [float("inf"), 0.0, 0.0]}]}),
            ["one"],
            "EMBED_RESPONSE_VALUE",
            False,
        ),
        (Response(json_error=True), ["one"], "EMBED_RESPONSE_JSON", False),
        (Response({}, status_code=503), ["one"], "EMBED_HTTP_RETRYABLE", True),
        (Response({}, status_code=422), ["one"], "EMBED_HTTP_TERMINAL", False),
    ]
    for response, texts, code, retryable in cases:
        monkeypatch.setattr(rag_engine.httpx, "Client", lambda **_kwargs: Client(response))
        with pytest.raises(rag_engine.EmbeddingError) as raised:
            rag_engine.embed_texts(texts)
        assert raised.value.code == code
        assert raised.value.retryable is retryable

    def timeout_handler(request):
        raise rag_engine.httpx.ReadTimeout("simulated timeout", request=request)

    def timeout_client(**kwargs):
        return real_client(transport=rag_engine.httpx.MockTransport(timeout_handler), **kwargs)

    monkeypatch.setattr(rag_engine.httpx, "Client", timeout_client)
    with pytest.raises(rag_engine.EmbeddingError) as raised:
        rag_engine.embed_texts(["one"])
    assert raised.value.code == "EMBED_TIMEOUT"
    assert raised.value.retryable is True


def test_atomic_replace_rolls_back_old_index_on_write_error(
    isolated_db: Path, monkeypatch: pytest.MonkeyPatch
):
    init_db()
    profile = vector_store.VectorProfile("sqlite-vec", "hash-v1", 4)
    with get_conn() as conn:
        _insert_document(conn, "doc-atomic")
        job_id = enqueue_job(conn, "ingest", {"documentId": "doc-atomic", "assetId": "asset-doc-atomic"})
        conn.execute("UPDATE ingestion_job SET status='running' WHERE id=?", (job_id,))
        vector_store.VectorStore(conn).replace_document(
            "doc-atomic",
            [vector_store.VectorChunk("chunk-old", 0, "old index", 1, "old-hash")],
            [[1.0, 0.0, 0.0, 0.0]],
            profile,
        )

    class Storage:
        def open(self, *_args):
            return b"new index content"

    monkeypatch.setattr(worker_runner, "storage", Storage())
    original = vector_store.VectorStore.replace_document

    def fail_after_delete(self, document_id, chunks, vectors, active_profile):
        self.conn.execute("DELETE FROM chunk WHERE document_id=?", (document_id,))
        raise vector_store.VectorStoreError("VECTOR_SERIALIZE_FAILED")

    monkeypatch.setattr(vector_store.VectorStore, "replace_document", fail_after_delete)
    with get_conn() as conn:
        with pytest.raises(vector_store.VectorStoreError):
            worker_runner.process_ingest(
                conn,
                job_id,
                {"documentId": "doc-atomic", "assetId": "asset-doc-atomic"},
            )
        assert conn.execute("SELECT id FROM chunk WHERE id='chunk-old'").fetchone()
        assert conn.execute("SELECT chunk_id FROM vector_ref WHERE chunk_id='chunk-old'").fetchone()
        assert conn.execute("SELECT chunk_id FROM chunk_embedding WHERE chunk_id='chunk-old'").fetchone()
    monkeypatch.setattr(vector_store.VectorStore, "replace_document", original)


def test_vector_search_is_profile_isolated_and_returns_top_six(isolated_db: Path):
    init_db()
    profile = vector_store.VectorProfile("sqlite-vec", "hash-v1", 4)
    other_profile = vector_store.VectorProfile("sqlite-vec", "other-v1", 4)
    with get_conn() as conn:
        store = vector_store.VectorStore(conn)
        for number in range(22):
            document_id = f"doc-{number:02d}"
            _insert_document(conn, document_id, state="queued" if number == 20 else "ready", deleted=number == 21)
            store.replace_document(
                document_id,
                [vector_store.VectorChunk(f"chunk-{number:02d}", 0, "alpha query corpus", 1, f"h-{number}")],
                [[1.0, 0.0, 0.0, 0.0]],
                profile,
            )
        _insert_document(conn, "doc-other")
        store.replace_document(
            "doc-other",
            [vector_store.VectorChunk("chunk-other", 0, "alpha query corpus", 1, "h-other")],
            [[1.0, 0.0, 0.0, 0.0]],
            other_profile,
        )
        hits = rag_engine.search(conn, "alpha query", top_k=20)

    assert len(hits) == 6
    returned = {hit["document_id"] for hit in hits}
    assert "doc-20" not in returned
    assert "doc-21" not in returned
    assert "doc-other" not in returned


def test_chat_stream_requires_done_and_never_templates_after_partial(
    isolated_db: Path, monkeypatch: pytest.MonkeyPatch
):
    del isolated_db
    monkeypatch.setattr(settings, "llama_chat_base_url", "http://chat.internal")

    class Response:
        def __init__(self, lines: list[str], status_code: int = 200):
            self.lines = lines
            self.status_code = status_code

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

        async def aiter_lines(self):
            for line in self.lines:
                yield line

    class Client:
        def __init__(self, lines: list[str], status_code: int = 200, fail: bool = False):
            self.lines = lines
            self.status_code = status_code
            self.fail = fail

        async def __aenter__(self):
            if self.fail:
                raise rag_engine.httpx.TimeoutException("test timeout")
            return self

        async def __aexit__(self, *_args):
            return False

        def stream(self, *_args, **_kwargs):
            return Response(self.lines, self.status_code)

    async def collect() -> list[dict]:
        return [event async for event in rag_engine.stream_chat([], [])]

    valid = [
        ": ping",
        "event: ping",
        "id: 1",
        'data: {"choices":[{"delta":{"content":"ok"}}]}',
        "data: [DONE]",
    ]
    monkeypatch.setattr(rag_engine.httpx, "AsyncClient", lambda **_kwargs: Client(valid))
    assert asyncio.run(collect()) == [{"event": "token", "token": "ok"}]

    partial_bad = ['data: {"choices":[{"delta":{"content":"partial"}}]}', 'data: {"choices":[]}']
    monkeypatch.setattr(rag_engine.httpx, "AsyncClient", lambda **_kwargs: Client(partial_bad))
    with pytest.raises(rag_engine.ChatStreamError, match="CHAT_STREAM_INVALID"):
        asyncio.run(collect())

    incomplete = ['data: {"choices":[{"delta":{"content":"partial"}}]}']
    monkeypatch.setattr(rag_engine.httpx, "AsyncClient", lambda **_kwargs: Client(incomplete))
    with pytest.raises(rag_engine.ChatStreamError, match="CHAT_STREAM_INCOMPLETE"):
        asyncio.run(collect())

    empty_done = ["data: [DONE]"]
    monkeypatch.setattr(rag_engine.httpx, "AsyncClient", lambda **_kwargs: Client(empty_done))
    fallback = asyncio.run(collect())
    assert fallback and fallback[0]["event"] == "token"
    assert "模型服务当前不可用" in "".join(event["token"] for event in fallback)

    invalid = ['data: {"choices":[]}']
    monkeypatch.setattr(rag_engine.httpx, "AsyncClient", lambda **_kwargs: Client(invalid))
    assert "模型服务当前不可用" in "".join(event["token"] for event in asyncio.run(collect()))

    monkeypatch.setattr(rag_engine.httpx, "AsyncClient", lambda **_kwargs: Client([], status_code=503))
    assert "模型服务当前不可用" in "".join(event["token"] for event in asyncio.run(collect()))

    monkeypatch.setattr(rag_engine.httpx, "AsyncClient", lambda **_kwargs: Client([], fail=True))
    assert "模型服务当前不可用" in "".join(event["token"] for event in asyncio.run(collect()))


def test_unknown_job_is_terminal_not_retried(isolated_db: Path):
    del isolated_db
    init_db()
    with get_conn() as conn:
        job_id = enqueue_job(conn, "not-a-real-job", {"documentId": "missing"})
    assert worker_runner.run_once("test-worker")
    with get_conn() as conn:
        row = conn.execute("SELECT status, error_code FROM ingestion_job WHERE id=?", (job_id,)).fetchone()
    assert row and row["status"] == "failed" and row["error_code"] == "UNKNOWN_JOB_TYPE"


def test_asset_missing_is_terminal_but_embedding_timeout_is_queued(
    isolated_db: Path, monkeypatch: pytest.MonkeyPatch
):
    del isolated_db
    init_db()
    with get_conn() as conn:
        _insert_document(conn, "doc-missing-asset")
        missing_job = enqueue_job(
            conn,
            "ingest",
            {"documentId": "doc-missing-asset", "assetId": "asset-does-not-exist"},
        )
    worker_runner.run_once("test-worker")
    with get_conn() as conn:
        missing = conn.execute(
            "SELECT status, error_code FROM ingestion_job WHERE id=?", (missing_job,)
        ).fetchone()
        doc = conn.execute(
            "SELECT index_state, index_error_code FROM knowledge_document WHERE id='doc-missing-asset'"
        ).fetchone()
    assert missing and missing["status"] == "failed" and missing["error_code"] == "INVALID_JOB_PAYLOAD"
    assert doc and doc["index_state"] == "failed" and doc["index_error_code"] == "INVALID_JOB_PAYLOAD"

    with get_conn() as conn:
        _insert_document(conn, "doc-timeout")
        timeout_job = enqueue_job(
            conn,
            "ingest",
            {"documentId": "doc-timeout", "assetId": "asset-doc-timeout"},
        )

    class Storage:
        def open(self, *_args):
            return b"retryable embedding text"

    monkeypatch.setattr(worker_runner, "storage", Storage())
    monkeypatch.setattr(
        worker_runner.rag_engine,
        "prepare_embeddings",
        lambda _texts: (_ for _ in ()).throw(rag_engine.EmbeddingError("EMBED_TIMEOUT", retryable=True)),
    )
    worker_runner.run_once("test-worker")
    with get_conn() as conn:
        timed_out = conn.execute(
            "SELECT status, error_code FROM ingestion_job WHERE id=?", (timeout_job,)
        ).fetchone()
        doc = conn.execute(
            "SELECT index_state, index_error_code FROM knowledge_document WHERE id='doc-timeout'"
        ).fetchone()
    assert timed_out and timed_out["status"] == "queued" and timed_out["error_code"] == "EMBED_TIMEOUT"
    assert doc and doc["index_state"] == "queued" and doc["index_error_code"] == "EMBED_TIMEOUT"


def test_retry_exhaustion_sets_job_and_document_failed_together(isolated_db: Path):
    del isolated_db
    init_db()
    with get_conn() as conn:
        _insert_document(conn, "doc-max-attempts")
        job_id = enqueue_job(
            conn,
            "ingest",
            {"documentId": "doc-max-attempts", "assetId": "asset-doc-max-attempts"},
        )
        conn.execute(
            "UPDATE ingestion_job SET status='running', attempts=? WHERE id=?",
            (MAX_ATTEMPTS, job_id),
        )
    worker_runner._record_failure(
        {
            "id": job_id,
            "payload": json.dumps({"documentId": "doc-max-attempts"}),
        },
        "EMBED_TIMEOUT",
        True,
        "doc-max-attempts",
    )
    with get_conn() as conn:
        job = conn.execute("SELECT status, error_code FROM ingestion_job WHERE id=?", (job_id,)).fetchone()
        document = conn.execute(
            "SELECT index_state, index_error_code FROM knowledge_document WHERE id='doc-max-attempts'"
        ).fetchone()
    assert job and tuple(job) == ("failed", "EMBED_TIMEOUT")
    assert document and tuple(document) == ("failed", "EMBED_TIMEOUT")


def test_stale_max_attempts_fails_document_without_changing_recovery_count(isolated_db: Path):
    del isolated_db
    init_db()
    with get_conn() as conn:
        _insert_document(conn, "doc-stale")
        job_id = enqueue_job(
            conn,
            "ingest",
            {"documentId": "doc-stale", "assetId": "asset-doc-stale"},
        )
        conn.execute(
            "UPDATE ingestion_job SET status='running', attempts=?, updated_at=? WHERE id=?",
            (MAX_ATTEMPTS, iso(now_utc() - timedelta(hours=1)), job_id),
        )
        assert recover_stale_running_jobs(conn, stale_after_seconds=1) == 0
        job = conn.execute("SELECT status, error_code FROM ingestion_job WHERE id=?", (job_id,)).fetchone()
        document = conn.execute(
            "SELECT index_state, index_error_code FROM knowledge_document WHERE id='doc-stale'"
        ).fetchone()
    assert job and tuple(job) == ("failed", "STALE_MAX_ATTEMPTS")
    assert document and tuple(document) == ("failed", "STALE_MAX_ATTEMPTS")


def test_ingest_and_purge_reject_conflicting_payload_asset_ids(isolated_db: Path):
    del isolated_db
    init_db()
    with get_conn() as conn:
        _insert_document(conn, "doc-ingest")
        ingest_job = enqueue_job(
            conn,
            "ingest",
            {"documentId": "doc-ingest", "assetId": "asset-other"},
        )
    assert worker_runner.run_once("asset-contract")
    with get_conn() as conn:
        ingest = conn.execute("SELECT status, error_code FROM ingestion_job WHERE id=?", (ingest_job,)).fetchone()
        ingest_doc = conn.execute(
            "SELECT index_state, index_error_code FROM knowledge_document WHERE id='doc-ingest'"
        ).fetchone()
    assert ingest and tuple(ingest) == ("failed", "INVALID_JOB_PAYLOAD")
    assert ingest_doc and tuple(ingest_doc) == ("failed", "INVALID_JOB_PAYLOAD")

    with get_conn() as conn:
        _insert_document(conn, "doc-purge")
        _insert_document(conn, "doc-unrelated")
        purge_job = enqueue_job(
            conn,
            "purge",
            {"documentId": "doc-purge", "assetId": "asset-doc-unrelated"},
        )
    assert worker_runner.run_once("asset-contract")
    with get_conn() as conn:
        purge = conn.execute("SELECT status, error_code FROM ingestion_job WHERE id=?", (purge_job,)).fetchone()
        assets = conn.execute(
            "SELECT id, status FROM asset WHERE id IN ('asset-doc-purge', 'asset-doc-unrelated') ORDER BY id"
        ).fetchall()
        purged = conn.execute("SELECT deleted_at FROM knowledge_document WHERE id='doc-purge'").fetchone()
    assert purge and tuple(purge) == ("failed", "INVALID_JOB_PAYLOAD")
    assert [(row["id"], row["status"]) for row in assets] == [
        ("asset-doc-purge", "active"),
        ("asset-doc-unrelated", "active"),
    ]
    assert purged and purged["deleted_at"] is None


def test_cpu_compose_passes_explicit_embedding_dim_to_api_and_worker():
    compose = (Path(__file__).resolve().parents[3] / "infra" / "compose" / "compose.cpu.yml").read_text(
        encoding="utf-8"
    )
    assert compose.count("EMBEDDING_DIM: ${EMBEDDING_DIM:-384}") == 2
