"""RAG eval 严格数据集、只读 corpus 预检与报告门禁。"""

from __future__ import annotations

import importlib.util
import json
import os
import sqlite3
import sys
from pathlib import Path

import pytest

from app.db import SCHEMA
from app.config import settings
from app.services import rag_engine, vector_store


SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "rag_eval.py"
SPEC = importlib.util.spec_from_file_location("starisle_rag_eval", SCRIPT)
assert SPEC and SPEC.loader
rag_eval = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = rag_eval
SPEC.loader.exec_module(rag_eval)


def _dataset(
    *,
    kind: str = "acceptance",
    authorized: bool = True,
    document_count: int = 20,
    question_count: int = 30,
) -> dict:
    document_ids = [f"doc-{number}" for number in range(document_count)]
    return {
        "schemaVersion": 1,
        "datasetId": f"{kind}-dataset",
        "kind": kind,
        "corpus": {
            "id": "fixture-corpus",
            "version": "v1",
            "authorized": authorized,
            "documentIds": document_ids,
        },
        "questions": [
            {
                "id": f"q-{number}",
                "query": f"question {number}",
                "relevantDocumentIds": [document_ids[number % len(document_ids)]],
            }
            for number in range(question_count)
        ],
    }


def test_synthetic_fixture_is_not_acceptance_and_acceptance_gates_are_strict():
    fixture = Path(__file__).parent / "fixtures" / "rag_eval_synthetic.json"
    synthetic = json.loads(fixture.read_text(encoding="utf-8"))
    summary = rag_eval.validate_dataset(synthetic)
    assert summary["schemaVersion"] == 1
    assert summary["datasetId"] == "synthetic-smoke-v1"
    assert summary["kind"] == "synthetic-smoke"
    assert summary["corpus"]["authorized"] is False

    with pytest.raises(rag_eval.EvalError, match="authorized"):
        rag_eval.validate_dataset(_dataset(authorized=False))
    with pytest.raises(rag_eval.EvalError, match="20"):
        rag_eval.validate_dataset(_dataset(document_count=19))
    with pytest.raises(rag_eval.EvalError, match="30"):
        rag_eval.validate_dataset(_dataset(question_count=29))

    accepted = rag_eval.validate_dataset(_dataset())
    assert accepted["questionCount"] == 30
    assert accepted["corpus"]["documentCount"] == 20


def test_dataset_contract_rejects_unknown_fields_and_out_of_corpus_relevance():
    dataset = _dataset()
    dataset["unexpected"] = True
    with pytest.raises(rag_eval.EvalError, match="严格匹配"):
        rag_eval.validate_dataset(dataset)

    dataset = _dataset()
    dataset["questions"][0]["relevantDocumentIds"] = ["not-in-corpus"]
    with pytest.raises(rag_eval.EvalError, match="属于 corpus"):
        rag_eval.validate_dataset(dataset)

    dataset = _dataset(kind="synthetic-smoke", authorized=False, document_count=1, question_count=1)
    dataset["questions"][0].pop("relevantDocumentIds")
    with pytest.raises(rag_eval.EvalError, match="至少需要"):
        rag_eval.validate_dataset(dataset)


def _seed_preflight_db(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    conn.execute(
        "INSERT INTO asset (id, original_name, mime_type, size, object_key, sha256, status, created_at) "
        "VALUES ('asset-in', 'in.txt', 'text/plain', 1, 'in.txt', 'h', 'active', '2026-01-01T00:00:00+00:00')"
    )
    conn.execute(
        "INSERT INTO knowledge_document (id, asset_id, title, document_type, hash, index_state, created_at) "
        "VALUES ('doc-in', 'asset-in', 'in', 'txt', 'h', 'ready', '2026-01-01T00:00:00+00:00')"
    )
    conn.execute(
        "INSERT INTO chunk (id, document_id, seq, text, token_count, hash) "
        "VALUES ('chunk-in', 'doc-in', 0, 'text', 1, 'h')"
    )
    conn.execute(
        "INSERT INTO asset (id, original_name, mime_type, size, object_key, sha256, status, created_at) "
        "VALUES ('asset-out', 'out.txt', 'text/plain', 1, 'out.txt', 'h', 'active', '2026-01-01T00:00:00+00:00')"
    )
    conn.execute(
        "INSERT INTO knowledge_document (id, asset_id, title, document_type, hash, index_state, created_at) "
        "VALUES ('doc-out', 'asset-out', 'out', 'txt', 'h', 'ready', '2026-01-01T00:00:00+00:00')"
    )
    conn.execute(
        "INSERT INTO chunk (id, document_id, seq, text, token_count, hash) "
        "VALUES ('chunk-out', 'doc-out', 0, 'text', 1, 'h')"
    )


def test_preflight_requires_declared_ready_corpus_and_chunk_membership():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    try:
        _seed_preflight_db(conn)
        dataset = {
            "schemaVersion": 1,
            "datasetId": "chunk-contract",
            "kind": "synthetic-smoke",
            "corpus": {"id": "c", "version": "v1", "authorized": False, "documentIds": ["doc-in"]},
            "questions": [
                {
                    "id": "q",
                    "query": "text",
                    "relevantChunkIds": ["chunk-in"],
                }
            ],
        }
        validated = rag_eval.validate_dataset(dataset)
        rag_eval._preflight_corpus(conn, validated)

        dataset["questions"][0]["relevantChunkIds"] = ["chunk-out"]
        with pytest.raises(rag_eval.EvalError, match="corpus.documentIds"):
            rag_eval._preflight_corpus(conn, rag_eval.validate_dataset(dataset))
    finally:
        conn.close()


def test_eval_rejects_implicit_existing_and_data_root_output_paths(tmp_path: Path):
    with pytest.raises(rag_eval.EvalError, match="默认"):
        rag_eval._explicit_path(".", "output")
    existing = tmp_path / "existing.json"
    existing.write_text("already here", encoding="utf-8")
    with pytest.raises(rag_eval.EvalError, match="无法安全写入"):
        rag_eval._write_report(existing, {"status": "synthetic"})

    data_root = tmp_path / "data"
    data_root.mkdir()
    with pytest.raises(rag_eval.EvalError, match="无法安全写入"):
        rag_eval._write_report(data_root / "report.json", {"status": "synthetic"}, data_root=data_root)


def test_eval_report_rejects_distinct_host_root_and_reparse_before_evaluation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    data_root = tmp_path / "live-data-root"
    host_root = tmp_path / "live-host-root"
    data_root.mkdir()
    host_root.mkdir()
    monkeypatch.setenv("DATA_ROOT", str(data_root))
    monkeypatch.setenv("DATA_HOST_ROOT", str(host_root))
    with pytest.raises(rag_eval.EvalError, match="无法安全写入"):
        rag_eval._report_output_path(host_root / "PRIVATE_CANARY_REPORT.json", data_root=tmp_path / "other")

    alias = tmp_path / "report-parent-alias"
    try:
        alias.symlink_to(tmp_path, target_is_directory=True)
    except OSError:
        pytest.skip("当前 Windows 测试账户不允许创建符号链接")
    with pytest.raises(rag_eval.EvalError, match="无法安全写入"):
        rag_eval._report_output_path(alias / "report.json", data_root=tmp_path / "other")


def test_synthetic_report_has_fingerprints_profile_sources_and_never_claims_acceptance(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    dataset_path = Path(__file__).parent / "fixtures" / "rag_eval_synthetic.json"
    data_root = tmp_path / "explicit-data-root"
    data_root.mkdir()
    output = tmp_path / "synthetic-report.json"
    monkeypatch.setattr(
        rag_eval,
        "evaluate",
        lambda _root, _dataset: {
            "store": "sqlite-vec",
            "modelId": "hash-v1",
            "dim": 4,
            "profile": {"store": "sqlite-vec", "modelId": "hash-v1", "dim": 4},
            "retrievalSources": ["vector"],
            "metrics": {
                "hitRateAt20": 1.0,
                "recallAt20": 1.0,
                "mrrAt20": 1.0,
                "p50LatencyMs": 1.0,
                "p95LatencyMs": 2.0,
            },
        },
    )
    assert rag_eval.main(
        ["run", "--data-root", str(data_root), "--dataset", str(dataset_path), "--output", str(output)]
    ) == 0
    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["kind"] == "synthetic-smoke"
    assert report["status"] == "synthetic-smoke-evaluated"
    assert report["acceptanceStatus"] == "not-applicable"
    assert report["datasetFingerprint"] and report["corpusFingerprint"]
    assert report["profile"]["modelId"] == "hash-v1"
    assert report["retrievalSources"] == ["vector"]


def test_synthetic_run_uses_explicit_readonly_db_and_real_sqlite_vec(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr(settings, "llama_embed_base_url", "")
    monkeypatch.setattr(settings, "embedding_model_id", "")
    fixture = Path(__file__).parent / "fixtures" / "rag_eval_synthetic.json"
    dataset = json.loads(fixture.read_text(encoding="utf-8"))
    data_root = tmp_path / "data-root"
    db_path = data_root / "db" / "star-isle.db"
    db_path.parent.mkdir(parents=True)
    conn = sqlite3.connect(db_path)
    try:
        conn.executescript(SCHEMA)
        profile = rag_engine.embedding_profile()
        store = vector_store.VectorStore(conn)
        for sequence, document_id in enumerate(dataset["corpus"]["documentIds"]):
            asset_id = f"asset-{document_id}"
            chunk_id = f"chunk-{document_id}"
            text = f"{document_id} synthetic retrieval evidence"
            conn.execute(
                "INSERT INTO asset (id, original_name, mime_type, size, object_key, sha256, status, created_at) "
                "VALUES (?, ?, 'text/plain', 1, ?, 'h', 'active', '2026-01-01T00:00:00+00:00')",
                (asset_id, f"{document_id}.txt", f"{document_id}.txt"),
            )
            conn.execute(
                "INSERT INTO knowledge_document (id, asset_id, title, document_type, hash, index_state, created_at) "
                "VALUES (?, ?, ?, 'txt', 'h', 'ready', '2026-01-01T00:00:00+00:00')",
                (document_id, asset_id, document_id),
            )
            store.replace_document(
                document_id,
                [vector_store.VectorChunk(chunk_id, 0, text, 1, f"h-{sequence}")],
                [rag_engine.embed_query(text)],
                profile,
            )
        conn.commit()
    finally:
        conn.close()

    output = tmp_path / "synthetic-real-report.json"
    assert rag_eval.main(
        ["run", "--data-root", str(data_root), "--dataset", str(fixture), "--output", str(output)]
    ) == 0
    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["kind"] == "synthetic-smoke"
    assert report["status"] == "synthetic-smoke-evaluated"
    assert report["retrievalSources"] == ["vector"]
    assert report["metrics"]["hitRateAt20"] == 1.0


def test_acceptance_run_requires_explicit_authorized_flag(tmp_path: Path):
    dataset_path = tmp_path / "acceptance.json"
    dataset_path.write_text(json.dumps(_dataset()), encoding="utf-8")
    data_root = tmp_path / "explicit-data-root"
    data_root.mkdir()
    assert rag_eval.main(
        [
            "run",
            "--data-root",
            str(data_root),
            "--dataset",
            str(dataset_path),
            "--output",
            str(tmp_path / "out.json"),
        ]
    ) == 1


def test_cli_stdout_and_stderr_drop_document_query_answer_and_path_canaries(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    canaries = {
        "document": "PRIVATE_CANARY_DOCUMENT",
        "query": "PRIVATE_CANARY_ANSWER",
        "csrf": "PRIVATE_CANARY_CSRF",
    }
    dataset = {
        "schemaVersion": 1,
        "datasetId": "PRIVATE_CANARY_DATASET",
        "kind": "synthetic-smoke",
        "corpus": {
            "id": "PRIVATE_CANARY_CORPUS",
            "version": "v-private",
            "authorized": False,
            "documentIds": [canaries["document"]],
        },
        "questions": [
            {
                "id": "PRIVATE_CANARY_QUESTION",
                "query": canaries["query"],
                "relevantDocumentIds": [canaries["document"]],
            }
        ],
    }
    dataset_path = tmp_path / "PRIVATE_CANARY_PATH-dataset.json"
    dataset_path.write_text(json.dumps(dataset), encoding="utf-8")

    assert rag_eval.main(["validate", "--dataset", str(dataset_path)]) == 0
    validate_output = capsys.readouterr()
    assert validate_output.err == ""
    for value in (*canaries.values(), str(dataset_path), "PRIVATE_CANARY_DATASET", "PRIVATE_CANARY_CORPUS"):
        assert value not in validate_output.out
    assert json.loads(validate_output.out)["documentCount"] == 1
    assert "Fingerprint" not in validate_output.out

    monkeypatch.setattr(
        rag_eval,
        "evaluate",
        lambda _root, _dataset: {
            "store": "sqlite-vec",
            "modelId": "hash-v1",
            "dim": 4,
            "profile": {"store": "sqlite-vec", "modelId": "hash-v1", "dim": 4},
            "retrievalSources": ["vector"],
            "metrics": {
                "hitRateAt20": 1.0,
                "recallAt20": 1.0,
                "mrrAt20": 1.0,
                "p50LatencyMs": 1.0,
                "p95LatencyMs": 1.0,
            },
        },
    )
    data_root = tmp_path / "PRIVATE_CANARY_PATH-data-root"
    data_root.mkdir()
    output = tmp_path / "PRIVATE_CANARY_PATH-output.json"
    assert rag_eval.main(
        ["run", "--data-root", str(data_root), "--dataset", str(dataset_path), "--output", str(output)]
    ) == 0
    run_output = capsys.readouterr()
    assert run_output.err == ""
    for value in (*canaries.values(), str(dataset_path), str(data_root), str(output)):
        assert value not in run_output.out
    assert json.loads(run_output.out) == {"status": "synthetic-smoke-evaluated"}

    assert rag_eval.main(["validate", "--dataset", str(tmp_path / "PRIVATE_CANARY_PATH-missing.json")]) == 1
    failed = capsys.readouterr()
    assert failed.out == ""
    assert failed.err == "RAG evaluation failed\n"


def _evaluation_stub() -> dict[str, object]:
    return {
        "store": "sqlite-vec",
        "modelId": "hash-v1",
        "dim": 4,
        "profile": {"store": "sqlite-vec", "modelId": "hash-v1", "dim": 4},
        "retrievalSources": ["vector"],
        "metrics": {
            "hitRateAt20": 1.0,
            "recallAt20": 1.0,
            "mrrAt20": 1.0,
            "p50LatencyMs": 1.0,
            "p95LatencyMs": 2.0,
        },
    }


def test_synthetic_rag_rejects_any_embedding_endpoint_before_evaluate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    fixture = Path(__file__).parent / "fixtures" / "rag_eval_synthetic.json"
    data_root = tmp_path / "data-root"
    data_root.mkdir()
    output = tmp_path / "output.json"
    called = False

    def unexpected_evaluate(*_args, **_kwargs):
        nonlocal called
        called = True
        raise AssertionError("PRIVATE_CANARY_NETWORK")

    monkeypatch.setenv("LLAMA_EMBED_BASE_URL", "https://outside.invalid")
    monkeypatch.setattr(rag_eval, "evaluate", unexpected_evaluate)
    assert rag_eval.main(
        ["run", "--data-root", str(data_root), "--dataset", str(fixture), "--output", str(output)]
    ) == 1
    captured = capsys.readouterr()
    assert not called
    assert not output.exists()
    assert captured.out == ""
    assert captured.err == "RAG evaluation failed\n"
    assert "PRIVATE_CANARY_NETWORK" not in captured.out + captured.err


def test_remote_acceptance_embedding_requires_second_explicit_opt_in_without_network(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    dataset_path = tmp_path / "acceptance.json"
    dataset_path.write_text(json.dumps(_dataset()), encoding="utf-8")
    data_root = tmp_path / "data-root"
    data_root.mkdir()
    blocked_output = tmp_path / "blocked.json"
    allowed_output = tmp_path / "allowed.json"
    called = False

    def stub_evaluate(*_args, **_kwargs):
        nonlocal called
        called = True
        return _evaluation_stub()

    monkeypatch.setenv("LLAMA_EMBED_BASE_URL", "https://outside.invalid")
    monkeypatch.setattr(rag_eval, "evaluate", stub_evaluate)
    base_args = [
        "run",
        "--data-root",
        str(data_root),
        "--dataset",
        str(dataset_path),
        "--authorized",
    ]
    assert rag_eval.main([*base_args, "--output", str(blocked_output)]) == 1
    blocked = capsys.readouterr()
    assert not called
    assert not blocked_output.exists()
    assert blocked.out == ""
    assert blocked.err == "RAG evaluation failed\n"

    assert rag_eval.main(
        [*base_args, "--allow-remote-embedding", "--output", str(allowed_output)]
    ) == 0
    assert called
    assert allowed_output.is_file()


def test_rag_module_loader_forces_dotenv_disabled_before_app_import(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("PYTHON_DOTENV_DISABLED", raising=False)
    rag_eval._load_rag_modules()
    assert os.environ["PYTHON_DOTENV_DISABLED"] == "1"
