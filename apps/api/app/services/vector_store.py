"""sqlite-vec-backed BLOB vector store.

`chunk_embedding` 的读写、sqlite-vec 扩展加载与 float32 序列化只保留在本模块，
避免其他业务路径绕开 profile 和完整性约束。
"""

from __future__ import annotations

import math
import sqlite3
from dataclasses import dataclass
from typing import Sequence

import sqlite_vec


STORE_NAME = "sqlite-vec"


class VectorStoreError(RuntimeError):
    """脱敏的、可稳定处理的向量存储错误。"""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class VectorProfile:
    store: str
    model_id: str
    dim: int

    def as_dict(self) -> dict[str, object]:
        return {"store": self.store, "modelId": self.model_id, "dim": self.dim}


@dataclass(frozen=True)
class VectorChunk:
    id: str
    seq: int
    text: str
    token_count: int
    text_hash: str


def sqlite_vec_version() -> str:
    """返回已安装包版本，不加载 SQLite 扩展。"""
    return str(getattr(sqlite_vec, "__version__", "unknown"))


def _validate_profile(profile: VectorProfile) -> None:
    if profile.store != STORE_NAME or not profile.model_id or profile.dim <= 0:
        raise VectorStoreError("VECTOR_PROFILE_INVALID")


def _load_extension(conn: sqlite3.Connection) -> None:
    """按最小权限窗口加载 sqlite-vec，始终重新禁用扩展加载。"""
    try:
        conn.enable_load_extension(True)
    except (AttributeError, sqlite3.Error) as exc:
        raise VectorStoreError("VECTOR_EXTENSION_UNAVAILABLE") from exc
    try:
        sqlite_vec.load(conn)
    except Exception as exc:  # sqlite-vec 的平台异常不应外泄到 API / job 状态
        raise VectorStoreError("VECTOR_EXTENSION_UNAVAILABLE") from exc
    finally:
        try:
            conn.enable_load_extension(False)
        except (AttributeError, sqlite3.Error):
            # 加载开关无法复位时，宁可让后续连接关闭；调用路径会终止当前操作。
            raise VectorStoreError("VECTOR_EXTENSION_UNAVAILABLE")


def serialize_float32(vector: Sequence[float], dim: int) -> bytes:
    """严格验证有限、非零、精确维度的向量，并序列化为 sqlite-vec BLOB。"""
    if dim <= 0 or len(vector) != dim:
        raise VectorStoreError("VECTOR_DIM_INVALID")
    values: list[float] = []
    norm_sq = 0.0
    for value in vector:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise VectorStoreError("VECTOR_VALUE_INVALID")
        number = float(value)
        if not math.isfinite(number):
            raise VectorStoreError("VECTOR_VALUE_INVALID")
        values.append(number)
        norm_sq += number * number
    if not math.isfinite(norm_sq) or norm_sq <= 0.0:
        raise VectorStoreError("VECTOR_ZERO")
    try:
        blob = sqlite_vec.serialize_float32(values)
    except Exception as exc:
        raise VectorStoreError("VECTOR_SERIALIZE_FAILED") from exc
    if not blob or len(blob) != dim * 4:
        raise VectorStoreError("VECTOR_SERIALIZE_FAILED")
    return blob


class VectorStore:
    """单连接的 profile 隔离 BLOB store。"""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    def ensure_available(self) -> None:
        _load_extension(self.conn)

    def replace_document(
        self,
        document_id: str,
        chunks: Sequence[VectorChunk],
        vectors: Sequence[Sequence[float]],
        profile: VectorProfile,
    ) -> None:
        """在调用方的事务/savepoint 内替换一个文档的三类索引行。"""
        _validate_profile(profile)
        if not chunks or len(chunks) != len(vectors):
            raise VectorStoreError("VECTOR_BATCH_COUNT_MISMATCH")

        # 任何 DML 前完成所有编码，杜绝 zip 截断或半批次写入。
        encoded: list[bytes] = []
        seen_ids: set[str] = set()
        seen_seq: set[int] = set()
        for index in range(len(chunks)):
            chunk = chunks[index]
            if (
                not chunk.id
                or chunk.id in seen_ids
                or chunk.seq in seen_seq
                or not chunk.text.strip()
                or chunk.token_count <= 0
                or not chunk.text_hash
            ):
                raise VectorStoreError("VECTOR_CHUNK_INVALID")
            seen_ids.add(chunk.id)
            seen_seq.add(chunk.seq)
            encoded.append(serialize_float32(vectors[index], profile.dim))

        self.ensure_available()
        self.conn.execute(
            "DELETE FROM chunk_embedding WHERE chunk_id IN (SELECT id FROM chunk WHERE document_id=?)",
            (document_id,),
        )
        self.conn.execute(
            "DELETE FROM vector_ref WHERE chunk_id IN (SELECT id FROM chunk WHERE document_id=?)",
            (document_id,),
        )
        self.conn.execute("DELETE FROM chunk WHERE document_id=?", (document_id,))

        for index in range(len(chunks)):
            chunk = chunks[index]
            self.conn.execute(
                "INSERT INTO chunk (id, document_id, seq, text, token_count, hash) VALUES (?, ?, ?, ?, ?, ?)",
                (chunk.id, document_id, chunk.seq, chunk.text, chunk.token_count, chunk.text_hash),
            )
            self.conn.execute(
                "INSERT INTO vector_ref (chunk_id, store, vector_id, model_id, dim) VALUES (?, ?, ?, ?, ?)",
                (chunk.id, profile.store, chunk.id, profile.model_id, profile.dim),
            )
            self.conn.execute(
                "INSERT INTO chunk_embedding (chunk_id, embedding) VALUES (?, ?)",
                (chunk.id, encoded[index]),
            )

        self._assert_document_counts(document_id, len(chunks), profile)

    def delete_document(self, document_id: str) -> None:
        """按 BLOB → ref → chunk 顺序移除索引，避免三类孤儿。"""
        self.conn.execute(
            "DELETE FROM chunk_embedding WHERE chunk_id IN (SELECT id FROM chunk WHERE document_id=?)",
            (document_id,),
        )
        self.conn.execute(
            "DELETE FROM vector_ref WHERE chunk_id IN (SELECT id FROM chunk WHERE document_id=?)",
            (document_id,),
        )
        self.conn.execute("DELETE FROM chunk WHERE document_id=?", (document_id,))

    def _assert_document_counts(self, document_id: str, expected: int, profile: VectorProfile) -> None:
        chunk_count = int(
            self.conn.execute("SELECT COUNT(*) FROM chunk WHERE document_id=?", (document_id,)).fetchone()[0]
        )
        ref_count = int(
            self.conn.execute(
                "SELECT COUNT(*) FROM vector_ref v JOIN chunk c ON c.id=v.chunk_id "
                "WHERE c.document_id=? AND v.store=? AND v.model_id=? AND v.dim=?",
                (document_id, profile.store, profile.model_id, profile.dim),
            ).fetchone()[0]
        )
        blob_count = int(
            self.conn.execute(
                "SELECT COUNT(*) FROM chunk_embedding e JOIN chunk c ON c.id=e.chunk_id "
                "WHERE c.document_id=? AND length(e.embedding)=?",
                (document_id, profile.dim * 4),
            ).fetchone()[0]
        )
        if chunk_count != expected or ref_count != expected or blob_count != expected:
            raise VectorStoreError("VECTOR_ATOMIC_COUNT_MISMATCH")

    def has_compatible_vectors(self, profile: VectorProfile) -> bool:
        _validate_profile(profile)
        row = self.conn.execute(
            "SELECT 1 FROM vector_ref v "
            "JOIN chunk_embedding e ON e.chunk_id=v.chunk_id "
            "JOIN chunk c ON c.id=v.chunk_id "
            "JOIN knowledge_document d ON d.id=c.document_id "
            "JOIN asset a ON a.id=d.asset_id "
            "WHERE v.store=? AND v.model_id=? AND v.dim=? "
            "AND length(e.embedding)=? AND d.index_state='ready' AND d.deleted_at IS NULL "
            "AND a.status <> 'deleted' LIMIT 1",
            (profile.store, profile.model_id, profile.dim, profile.dim * 4),
        ).fetchone()
        return row is not None

    def search(
        self,
        query_vector: Sequence[float],
        profile: VectorProfile,
        candidate_limit: int,
    ) -> list[dict]:
        """只检索完全相同 profile 的 ready、未删除文档。"""
        _validate_profile(profile)
        if candidate_limit <= 0:
            return []
        query_blob = serialize_float32(query_vector, profile.dim)
        self.ensure_available()
        rows = self.conn.execute(
            "SELECT c.id AS chunk_id, c.document_id, c.seq, c.text, c.hash, "
            "vec_distance_cosine(e.embedding, ?) AS distance "
            "FROM chunk_embedding e "
            "JOIN vector_ref v ON v.chunk_id=e.chunk_id "
            "JOIN chunk c ON c.id=e.chunk_id "
            "JOIN knowledge_document d ON d.id=c.document_id "
            "JOIN asset a ON a.id=d.asset_id "
            "WHERE v.store=? AND v.model_id=? AND v.dim=? "
            "AND length(e.embedding)=? AND d.index_state='ready' AND d.deleted_at IS NULL "
            "AND a.status <> 'deleted' "
            "ORDER BY distance ASC, c.document_id ASC, c.seq ASC, c.id ASC LIMIT ?",
            (query_blob, profile.store, profile.model_id, profile.dim, profile.dim * 4, candidate_limit),
        ).fetchall()
        return [dict(row) for row in rows]


def delete_document(conn: sqlite3.Connection, document_id: str) -> None:
    VectorStore(conn).delete_document(document_id)
