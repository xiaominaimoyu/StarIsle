#!/usr/bin/env python3
"""StarIsle RAG 只读评测：严格数据集契约、corpus 预检与 top20 指标。"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import sqlite3
import sys
import time
from pathlib import Path
from typing import Any


_SCRIPT_DIRECTORY = Path(__file__).resolve().parent
if str(_SCRIPT_DIRECTORY) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIRECTORY))

from harness_safety import HarnessSafetyError, is_loopback_http_url, new_output_file_path  # noqa: E402


class EvalError(RuntimeError):
    """对 CLI 仅暴露稳定、脱敏的评测失败原因。"""


DATASET_FIELDS = {"schemaVersion", "datasetId", "kind", "corpus", "questions"}
CORPUS_FIELDS = {"id", "version", "authorized", "documentIds"}
QUESTION_FIELDS = {"id", "query", "relevantDocumentIds", "relevantChunkIds"}


def _script_root() -> Path:
    return Path(__file__).resolve().parent.parent


def _explicit_path(value: str, label: str) -> Path:
    if not value or value.strip().lower() in {".", "default", "none"}:
        raise EvalError(f"{label} 必须显式指定，不能使用默认路径")
    return Path(value).expanduser().resolve()


def _load_dataset(path: Path) -> dict[str, Any]:
    if not path.is_file() or path.is_symlink():
        raise EvalError("dataset 必须是普通 JSON 文件")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise EvalError("dataset 不是有效 JSON") from exc
    if not isinstance(value, dict):
        raise EvalError("dataset 顶层必须是对象")
    return value


def _nonempty_id(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise EvalError(f"{label} 必须是非空字符串")
    return value


def _id_list(value: object, label: str, *, required: bool) -> list[str]:
    if value is None and not required:
        return []
    if not isinstance(value, list) or (required and not value):
        raise EvalError(f"{label} 必须是{'非空' if required else ''}数组")
    if any(not isinstance(item, str) or not item for item in value):
        raise EvalError(f"{label} 只能包含非空字符串")
    if len(set(value)) != len(value):
        raise EvalError(f"{label} 不能重复")
    return list(value)


def validate_dataset(dataset: dict[str, Any]) -> dict[str, Any]:
    if set(dataset) != DATASET_FIELDS:
        raise EvalError("dataset 字段必须严格匹配 schema")
    schema_version = dataset.get("schemaVersion")
    if isinstance(schema_version, bool) or schema_version != 1:
        raise EvalError("schemaVersion 必须为 1")
    dataset_id = _nonempty_id(dataset.get("datasetId"), "datasetId")
    kind = dataset.get("kind")
    if kind not in {"synthetic-smoke", "acceptance"}:
        raise EvalError("kind 必须为 synthetic-smoke 或 acceptance")

    corpus = dataset.get("corpus")
    if not isinstance(corpus, dict) or set(corpus) != CORPUS_FIELDS:
        raise EvalError("corpus 字段必须严格匹配 schema")
    corpus_id = _nonempty_id(corpus.get("id"), "corpus.id")
    corpus_version = _nonempty_id(corpus.get("version"), "corpus.version")
    authorized = corpus.get("authorized")
    if not isinstance(authorized, bool):
        raise EvalError("corpus.authorized 必须是布尔值")
    corpus_document_ids = _id_list(corpus.get("documentIds"), "corpus.documentIds", required=True)
    corpus_documents = set(corpus_document_ids)

    questions = dataset.get("questions")
    if not isinstance(questions, list) or not questions:
        raise EvalError("questions 必须是非空数组")
    seen_questions: set[str] = set()
    normalized_questions: list[dict[str, Any]] = []
    for question in questions:
        if not isinstance(question, dict) or not set(question).issubset(QUESTION_FIELDS):
            raise EvalError("question 包含未定义字段")
        question_id = _nonempty_id(question.get("id"), "question.id")
        if question_id in seen_questions:
            raise EvalError("question id 必须唯一")
        query = _nonempty_id(question.get("query"), "question.query")
        relevant_documents = _id_list(
            question.get("relevantDocumentIds"), "question.relevantDocumentIds", required=False
        )
        relevant_chunks = _id_list(
            question.get("relevantChunkIds"), "question.relevantChunkIds", required=False
        )
        if not relevant_documents and not relevant_chunks:
            raise EvalError("question 至少需要 relevantDocumentIds 或 relevantChunkIds")
        if not set(relevant_documents).issubset(corpus_documents):
            raise EvalError("relevantDocumentIds 必须属于 corpus.documentIds")
        seen_questions.add(question_id)
        normalized_questions.append(
            {
                "id": question_id,
                "query": query,
                "relevantDocumentIds": relevant_documents,
                "relevantChunkIds": relevant_chunks,
            }
        )

    if kind == "synthetic-smoke" and authorized:
        raise EvalError("synthetic-smoke corpus 必须标记为 unauthorized")
    if kind == "acceptance":
        if not authorized:
            raise EvalError("acceptance corpus 必须明确标记为 authorized")
        if len(corpus_document_ids) < 20:
            raise EvalError("acceptance corpus 至少需要 20 个不同文档")
        if len(normalized_questions) < 30:
            raise EvalError("acceptance 至少需要 30 个不同问题")

    return {
        "schemaVersion": 1,
        "datasetId": dataset_id,
        "kind": kind,
        "corpus": {
            "id": corpus_id,
            "version": corpus_version,
            "authorized": authorized,
            "documentIds": corpus_document_ids,
            "documentCount": len(corpus_document_ids),
        },
        "questions": normalized_questions,
        "questionCount": len(normalized_questions),
    }


def _fingerprint(value: object) -> str:
    canonical = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _percentile(values: list[float], fraction: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    position = max(0, min(len(ordered) - 1, math.ceil(len(ordered) * fraction) - 1))
    return round(ordered[position], 3)


def _readonly_connection(data_root: Path) -> sqlite3.Connection:
    db_path = data_root / "db" / "star-isle.db"
    if not db_path.is_file() or db_path.is_symlink():
        raise EvalError("显式 DATA_ROOT 中未找到 SQLite 数据库")
    try:
        conn = sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA query_only=ON")
        return conn
    except sqlite3.Error as exc:
        raise EvalError("无法以只读模式打开评测数据库") from exc


def _load_rag_modules():
    # This must precede every application import so an ambient .env is never
    # loaded while evaluating a caller-selected dataset.
    os.environ["PYTHON_DOTENV_DISABLED"] = "1"
    api_root = _script_root() / "apps" / "api"
    if str(api_root) not in sys.path:
        sys.path.insert(0, str(api_root))
    from app.services import rag_engine, vector_store

    return rag_engine, vector_store


def _placeholders(values: list[str]) -> str:
    return ", ".join("?" for _ in values)


def _preflight_corpus(conn: sqlite3.Connection, validated: dict[str, Any]) -> None:
    document_ids = validated["corpus"]["documentIds"]
    rows = conn.execute(
        "SELECT d.id, COUNT(c.id) AS chunk_count FROM knowledge_document d "
        "JOIN asset a ON a.id=d.asset_id "
        "LEFT JOIN chunk c ON c.document_id=d.id "
        f"WHERE d.id IN ({_placeholders(document_ids)}) AND d.deleted_at IS NULL "
        "AND d.index_state='ready' AND a.status='active' GROUP BY d.id",
        tuple(document_ids),
    ).fetchall()
    valid_documents = {str(row["id"]): int(row["chunk_count"]) for row in rows}
    if set(document_ids) != set(valid_documents) or any(count <= 0 for count in valid_documents.values()):
        raise EvalError("corpus 声明文档必须在只读 DB 中存在、active、ready 且含 chunks")

    declared_chunks = sorted(
        {
            chunk_id
            for question in validated["questions"]
            for chunk_id in question["relevantChunkIds"]
        }
    )
    if not declared_chunks:
        return
    rows = conn.execute(
        "SELECT c.id, c.document_id FROM chunk c "
        f"WHERE c.id IN ({_placeholders(declared_chunks)})",
        tuple(declared_chunks),
    ).fetchall()
    chunk_documents = {str(row["id"]): str(row["document_id"]) for row in rows}
    if set(declared_chunks) != set(chunk_documents):
        raise EvalError("relevantChunkIds 必须在只读 DB 中存在")
    if not set(chunk_documents.values()).issubset(set(document_ids)):
        raise EvalError("relevantChunkIds 必须属于 corpus.documentIds")


def evaluate(data_root: Path, validated: dict[str, Any]) -> dict[str, Any]:
    rag_engine, vector_store = _load_rag_modules()
    try:
        conn = _readonly_connection(data_root)
        try:
            _preflight_corpus(conn, validated)
            latencies: list[float] = []
            hit_count = 0
            recall_sum = 0.0
            reciprocal_rank_sum = 0.0
            source_kinds: set[str] = set()
            active_profile: dict[str, object] | None = None
            for question in validated["questions"]:
                started = time.perf_counter()
                candidates, profile, source_kind = rag_engine.retrieve_candidates(
                    conn, question["query"], candidate_limit=20
                )
                latencies.append((time.perf_counter() - started) * 1000.0)
                active_profile = profile.as_dict()
                source_kinds.add(source_kind)

                relevant_units = {
                    *(f"document:{document_id}" for document_id in question["relevantDocumentIds"]),
                    *(f"chunk:{chunk_id}" for chunk_id in question["relevantChunkIds"]),
                }
                matched_units: set[str] = set()
                first_rank: int | None = None
                for rank, candidate in enumerate(candidates[:20], start=1):
                    candidate_units = {
                        f"document:{candidate['document_id']}",
                        f"chunk:{candidate['chunk_id']}",
                    }
                    overlap = candidate_units.intersection(relevant_units)
                    if overlap and first_rank is None:
                        first_rank = rank
                    matched_units.update(overlap)
                if matched_units:
                    hit_count += 1
                recall_sum += len(matched_units) / len(relevant_units)
                if first_rank is not None:
                    reciprocal_rank_sum += 1.0 / first_rank
        finally:
            conn.close()
    except rag_engine.RagError as exc:
        raise EvalError(f"RAG 检索失败：{exc.code}") from exc
    except vector_store.VectorStoreError as exc:
        raise EvalError(f"向量存储不可用：{exc.code}") from exc
    except sqlite3.Error as exc:
        raise EvalError("评测数据库 schema 或只读查询失败") from exc

    question_count = validated["questionCount"]
    if active_profile is None:  # validate_dataset 已保证问题非空，仅作防御。
        raise EvalError("评测没有生成检索 profile")
    return {
        "store": active_profile["store"],
        "modelId": active_profile["modelId"],
        "dim": active_profile["dim"],
        "profile": active_profile,
        "retrievalSources": sorted(source_kinds),
        "metrics": {
            "hitRateAt20": round(hit_count / question_count, 6),
            "recallAt20": round(recall_sum / question_count, 6),
            "mrrAt20": round(reciprocal_rank_sum / question_count, 6),
            "p50LatencyMs": _percentile(latencies, 0.50),
            "p95LatencyMs": _percentile(latencies, 0.95),
        },
    }


def _report_output_path(output: Path, *, data_root: Path | None = None) -> Path:
    """Validate an exclusive report target before any evaluator/module work."""

    protected_roots: list[Path] = []
    if data_root is not None:
        protected_roots.append(data_root)
    protected_roots.extend(
        Path(value)
        for value in (os.environ.get("DATA_ROOT"), os.environ.get("DATA_HOST_ROOT"))
        if value and value.strip()
    )
    try:
        return new_output_file_path(
            output,
            repository_root=_script_root(),
            data_roots=tuple(protected_roots),
        )
    except HarnessSafetyError:
        raise EvalError("output 无法安全写入") from None


def _write_report(output: Path, report: dict[str, Any], *, data_root: Path | None = None) -> None:
    output = _report_output_path(output, data_root=data_root)
    try:
        with output.open("x", encoding="utf-8") as handle:
            json.dump(report, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
    except (FileExistsError, OSError):
        raise EvalError("output 无法安全写入") from None


def _embedding_endpoint() -> str:
    return os.environ.get("LLAMA_EMBED_BASE_URL", "").strip()


def _enforce_embedding_gate(validated: dict[str, Any], *, allow_remote_embedding: bool) -> None:
    """Keep synthetic smoke offline; require a second opt-in for remote RAG."""

    endpoint = _embedding_endpoint()
    if validated["kind"] == "synthetic-smoke":
        if endpoint:
            raise EvalError("synthetic embedding endpoint is not permitted")
        return
    if endpoint and not is_loopback_http_url(endpoint) and not allow_remote_embedding:
        raise EvalError("remote embedding requires explicit authorization")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="StarIsle RAG read-only evaluation")
    commands = parser.add_subparsers(dest="command", required=True)
    validate_parser = commands.add_parser("validate", help="验证 RAG 评测数据集和 acceptance 门禁")
    validate_parser.add_argument("--dataset", required=True)
    run_parser = commands.add_parser("run", help="在明确 DATA_ROOT 上只读执行评测")
    run_parser.add_argument("--data-root", required=True)
    run_parser.add_argument("--dataset", required=True)
    run_parser.add_argument("--output", required=True)
    run_parser.add_argument("--authorized", action="store_true", help="明确授权本次 acceptance 评测")
    run_parser.add_argument(
        "--allow-remote-embedding",
        action="store_true",
        help="与 --authorized 配合，明确授权 non-loopback embedding endpoint。",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        dataset_path = _explicit_path(args.dataset, "dataset")
        dataset = _load_dataset(dataset_path)
        validated = validate_dataset(dataset)
        dataset_fingerprint = _fingerprint(dataset)
        corpus_fingerprint = _fingerprint(dataset["corpus"])
        if args.command == "validate":
            print(
                json.dumps(
                    {
                        "status": "valid",
                        "schemaVersion": validated["schemaVersion"],
                        "kind": validated["kind"],
                        "questionCount": validated["questionCount"],
                        "documentCount": validated["corpus"]["documentCount"],
                    },
                    ensure_ascii=False,
                )
            )
            return 0

        data_root = _explicit_path(args.data_root, "data-root")
        output = _explicit_path(args.output, "output")
        if validated["kind"] == "acceptance" and not args.authorized:
            raise EvalError("acceptance run 需要显式 --authorized")
        output = _report_output_path(output, data_root=data_root)
        _enforce_embedding_gate(validated, allow_remote_embedding=args.allow_remote_embedding)
        evaluation = evaluate(data_root, validated)
        synthetic = validated["kind"] == "synthetic-smoke"
        report = {
            "schemaVersion": validated["schemaVersion"],
            "datasetId": validated["datasetId"],
            "kind": validated["kind"],
            "status": "synthetic-smoke-evaluated" if synthetic else "authorized-acceptance-evaluated",
            "acceptanceStatus": "not-applicable" if synthetic else "evaluated",
            "datasetFingerprint": dataset_fingerprint,
            "corpusFingerprint": corpus_fingerprint,
            "corpus": {
                "id": validated["corpus"]["id"],
                "version": validated["corpus"]["version"],
                "documentCount": validated["corpus"]["documentCount"],
            },
            **evaluation,
        }
        _write_report(output, report, data_root=data_root)
        print(json.dumps({"status": report["status"]}, ensure_ascii=False))
        return 0
    except EvalError:
        print("RAG evaluation failed", file=sys.stderr)
        return 1
    except Exception:
        print("RAG evaluation failed", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
