"""RAG 分块、严格 embedding 契约、检索 rerank seam 与 chat 流。"""

from __future__ import annotations

import asyncio
import hashlib
import json
import math
import re
import sqlite3
from dataclasses import dataclass
from typing import AsyncIterator, Sequence

import httpx

from ..config import settings
from . import vector_store


CHUNK_TOKENS = 600
CHUNK_OVERLAP = 100
TOP_K = 20
TOP_RESULTS = 6
HASH_PROFILE_ID = "hash-v1"


class RagError(RuntimeError):
    """所有可预期 RAG 错误都只暴露稳定 code，不回传远程细节。"""

    def __init__(self, code: str, *, retryable: bool = False) -> None:
        super().__init__(code)
        self.code = code
        self.retryable = retryable


class EmbeddingError(RagError):
    pass


class ChatStreamError(RagError):
    pass


@dataclass(frozen=True)
class EmbeddingBatch:
    profile: vector_store.VectorProfile
    vectors: list[list[float]]


# ---------- 分块 ----------

def split_text(text: str) -> list[str]:
    """按段落与长度切块（约 600 tokens / 100 overlap）。"""
    text = re.sub(r"\r\n", "\n", text)
    paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
    chunks: list[str] = []
    buf = ""
    for para in paragraphs:
        if len(buf) + len(para) > CHUNK_TOKENS * 3 and buf:
            chunks.extend(_split_long(buf))
            buf = ""
        buf = f"{buf}\n\n{para}" if buf else para
    if buf:
        chunks.extend(_split_long(buf))
    return [chunk for chunk in chunks if chunk.strip()]


def _split_long(text: str) -> list[str]:
    parts: list[str] = []
    while len(text) > CHUNK_TOKENS * 3:
        cut = text.rfind(" ", 0, CHUNK_TOKENS * 3)
        if cut < CHUNK_TOKENS * 2:
            cut = CHUNK_TOKENS * 3
        parts.append(text[:cut])
        text = text[cut - CHUNK_OVERLAP :]
    if text:
        parts.append(text)
    return parts


def token_count(text: str) -> int:
    return max(1, len(text) // 2)


# ---------- embedding profile 与严格远程契约 ----------

def embedding_profile() -> vector_store.VectorProfile:
    """URL 为空时且仅此时启用显式离线 hash-v1 profile。"""
    dim = settings.embedding_dim
    if not isinstance(dim, int) or isinstance(dim, bool) or dim <= 0:
        raise EmbeddingError("EMBED_DIM_INVALID")
    if not settings.llama_embed_base_url:
        return vector_store.VectorProfile(vector_store.STORE_NAME, HASH_PROFILE_ID, dim)
    if not settings.embedding_model_id:
        raise EmbeddingError("EMBED_MODEL_ID_REQUIRED")
    return vector_store.VectorProfile(
        vector_store.STORE_NAME,
        settings.embedding_model_id,
        dim,
    )


def _hash_embedding(text: str, dim: int) -> list[float]:
    """仅 hash-v1 profile 使用的确定性、非零离线 embedding。"""
    if dim <= 0:
        raise EmbeddingError("EMBED_DIM_INVALID")
    values = [0.0] * dim
    tokens = re.findall(r"[\w\u4e00-\u9fff]+", text.lower())
    # 纯标点/空白以原始文本的 digest 落点，保证非空输入仍得到非零可验证向量。
    material = tokens or [text]
    for ordinal, token in enumerate(material):
        digest = hashlib.blake2b(
            f"{ordinal}:{token}".encode("utf-8", errors="replace"), digest_size=16
        ).digest()
        first = int.from_bytes(digest[:8], "big") % dim
        second = int.from_bytes(digest[8:], "big") % dim
        values[first] += 1.0
        values[second] -= 0.25
    norm = math.sqrt(sum(value * value for value in values))
    if not math.isfinite(norm) or norm <= 0.0:
        raise EmbeddingError("EMBED_HASH_INVALID")
    return [value / norm for value in values]


def _validate_remote_vector(value: object, dim: int) -> list[float]:
    if not isinstance(value, list) or len(value) != dim:
        raise EmbeddingError("EMBED_RESPONSE_DIM")
    vector: list[float] = []
    norm_sq = 0.0
    for item in value:
        if isinstance(item, bool) or not isinstance(item, (int, float)):
            raise EmbeddingError("EMBED_RESPONSE_VALUE")
        number = float(item)
        if not math.isfinite(number):
            raise EmbeddingError("EMBED_RESPONSE_VALUE")
        vector.append(number)
        norm_sq += number * number
    if not math.isfinite(norm_sq) or norm_sq <= 0.0:
        raise EmbeddingError("EMBED_RESPONSE_ZERO")
    return vector


def _remote_embed(texts: Sequence[str], profile: vector_store.VectorProfile) -> list[list[float]]:
    """验证 OpenAI-compatible embedding 响应的全部索引、数量、模型与值。"""
    try:
        with httpx.Client(timeout=httpx.Timeout(60.0, connect=10.0)) as client:
            response = client.post(
                f"{settings.llama_embed_base_url.rstrip('/')}/v1/embeddings",
                json={"input": list(texts), "model": profile.model_id},
            )
    except httpx.TimeoutException as exc:
        raise EmbeddingError("EMBED_TIMEOUT", retryable=True) from exc
    except httpx.RequestError as exc:
        raise EmbeddingError("EMBED_NETWORK", retryable=True) from exc
    except Exception as exc:
        raise EmbeddingError("EMBED_CLIENT_FAILURE", retryable=True) from exc

    if response.status_code != 200:
        retryable = response.status_code == 429 or response.status_code >= 500
        code = "EMBED_HTTP_RETRYABLE" if retryable else "EMBED_HTTP_TERMINAL"
        raise EmbeddingError(code, retryable=retryable)
    try:
        payload = response.json()
    except Exception as exc:
        raise EmbeddingError("EMBED_RESPONSE_JSON") from exc
    if not isinstance(payload, dict):
        raise EmbeddingError("EMBED_RESPONSE_SCHEMA")
    if payload.get("model") != profile.model_id:
        raise EmbeddingError("EMBED_RESPONSE_MODEL")
    data = payload.get("data")
    if not isinstance(data, list) or len(data) != len(texts):
        raise EmbeddingError("EMBED_RESPONSE_COUNT")

    indexed: dict[int, list[float]] = {}
    for item in data:
        if not isinstance(item, dict):
            raise EmbeddingError("EMBED_RESPONSE_SCHEMA")
        index = item.get("index")
        if isinstance(index, bool) or not isinstance(index, int):
            raise EmbeddingError("EMBED_RESPONSE_INDEX")
        if index in indexed or index < 0 or index >= len(texts):
            raise EmbeddingError("EMBED_RESPONSE_INDEX")
        indexed[index] = _validate_remote_vector(item.get("embedding"), profile.dim)
    if set(indexed) != set(range(len(texts))):
        raise EmbeddingError("EMBED_RESPONSE_INDEX")
    return [indexed[index] for index in range(len(texts))]


def prepare_embeddings(texts: Sequence[str]) -> EmbeddingBatch:
    """先完整生成并验证整个 embedding batch；绝不在失败时改用 hash。"""
    profile = embedding_profile()
    normalized = list(texts)
    if any(not isinstance(text, str) or not text.strip() for text in normalized):
        raise EmbeddingError("EMBED_TEXT_INVALID")
    if profile.model_id == HASH_PROFILE_ID:
        vectors = [_hash_embedding(text, profile.dim) for text in normalized]
    else:
        vectors = _remote_embed(normalized, profile)
    if len(vectors) != len(normalized):
        raise EmbeddingError("EMBED_RESPONSE_COUNT")
    return EmbeddingBatch(profile=profile, vectors=vectors)


def embed_texts(texts: list[str]) -> list[list[float]]:
    """保留原调用接口，同时应用完整的 profile/响应契约。"""
    return prepare_embeddings(texts).vectors


def embed_query(text: str) -> list[float]:
    return prepare_embeddings([text]).vectors[0]


# ---------- VectorStore facade ----------

def _to_vector_chunks(chunks: Sequence[tuple[str, int, str, str]]) -> list[vector_store.VectorChunk]:
    records: list[vector_store.VectorChunk] = []
    for index in range(len(chunks)):
        chunk_id, seq, text, text_hash = chunks[index]
        records.append(
            vector_store.VectorChunk(
                id=chunk_id,
                seq=seq,
                text=text,
                token_count=token_count(text),
                text_hash=text_hash,
            )
        )
    return records


def upsert_chunk_vectors(
    conn: sqlite3.Connection,
    document_id: str,
    chunks: list[tuple[str, int, str, str]],
    model_id: str | None,
) -> None:
    """兼容旧调用方；实际写入统一委托给 VectorStore。"""
    batch = prepare_embeddings([chunk[2] for chunk in chunks])
    if model_id is not None and model_id != batch.profile.model_id:
        raise EmbeddingError("EMBED_PROFILE_MISMATCH")
    vector_store.VectorStore(conn).replace_document(
        document_id,
        _to_vector_chunks(chunks),
        batch.vectors,
        batch.profile,
    )


def delete_chunk_vectors(conn: sqlite3.Connection, document_id: str) -> None:
    vector_store.delete_document(conn, document_id)


# ---------- 检索：候选 top20，再确定性 rerank 到 top6 ----------

def _query_terms(query: str) -> list[str]:
    terms: list[str] = []
    for token in re.findall(r"[\w\u4e00-\u9fff]+", query.lower()):
        if re.fullmatch(r"[\u4e00-\u9fff]+", token):
            if len(token) > 1:
                terms.extend(token[index : index + 2] for index in range(len(token) - 1))
        elif len(token) > 1:
            terms.append(token)
    return list(dict.fromkeys(terms))


def _keyword_search(conn: sqlite3.Connection, query: str, candidate_limit: int) -> list[dict]:
    terms = _query_terms(query)
    if not terms or candidate_limit <= 0:
        return []
    clauses = " OR ".join(["c.text LIKE ?"] * len(terms))
    params = [f"%{term}%" for term in terms]
    score_expr = " + ".join(["(CASE WHEN c.text LIKE ? THEN 1 ELSE 0 END)"] * len(terms))
    rows = conn.execute(
        f"SELECT c.id AS chunk_id, c.document_id, c.seq, c.text, c.hash, "
        f"({score_expr}) AS score FROM chunk c "
        "JOIN knowledge_document d ON d.id=c.document_id "
        "JOIN asset a ON a.id=d.asset_id "
        f"WHERE ({clauses}) AND d.index_state='ready' AND d.deleted_at IS NULL "
        "AND a.status <> 'deleted' "
        "ORDER BY score DESC, c.document_id ASC, c.seq ASC, c.id ASC LIMIT ?",
        (*params, *params, candidate_limit),
    ).fetchall()
    return [dict(row) for row in rows]


def retrieve_candidates(
    conn: sqlite3.Connection,
    query: str,
    candidate_limit: int = TOP_K,
) -> tuple[list[dict], vector_store.VectorProfile, str]:
    """返回严格至多 top20 的同 profile 向量候选，或明确 legacy keyword 候选。"""
    limit = min(max(1, candidate_limit), TOP_K)
    profile = embedding_profile()
    store = vector_store.VectorStore(conn)
    if store.has_compatible_vectors(profile):
        # 远程 query embedding 失败必须上抛给 SSE，绝不能静默跨 profile / keyword。
        query_vector = prepare_embeddings([query]).vectors[0]
        return store.search(query_vector, profile, limit), profile, "vector"
    return _keyword_search(conn, query, limit), profile, "keyword"


def _lexical_overlap(query: str, text: str) -> int:
    return sum(1 for term in _query_terms(query) if term in text.lower())


def rerank_candidates(query: str, candidates: Sequence[dict], limit: int = TOP_RESULTS) -> list[dict]:
    """一个可替换的、稳定排序的基础 rerank seam。"""
    scored: list[tuple[tuple[float, float, str, int, str], dict]] = []
    for candidate in candidates:
        lexical = _lexical_overlap(query, str(candidate.get("text", "")))
        if "distance" in candidate:
            distance = float(candidate["distance"])
            relevance = (1.0 - distance) + lexical * 0.03
            tie_distance = distance
        else:
            relevance = float(candidate.get("score", 0.0)) + lexical * 0.001
            tie_distance = 0.0
        sort_key = (
            -relevance,
            tie_distance,
            str(candidate.get("document_id", "")),
            int(candidate.get("seq", 0)),
            str(candidate.get("chunk_id", "")),
        )
        scored.append((sort_key, candidate))
    scored.sort(key=lambda item: item[0])
    return [candidate for _, candidate in scored[: min(max(0, limit), TOP_RESULTS)]]


def search(conn: sqlite3.Connection, query: str, top_k: int = TOP_RESULTS) -> list[dict]:
    """公共检索 API：固定 top20 候选，最终最多返回 top6。"""
    candidates, _, _ = retrieve_candidates(conn, query, TOP_K)
    return rerank_candidates(query, candidates, min(max(0, top_k), TOP_RESULTS))


# ---------- chat 流：严格 SSE，不混合 partial 与模板 ----------

async def _template_chat(citations: list[dict]) -> AsyncIterator[dict]:
    snippet = " ".join(str(citation.get("snippet", "")) for citation in citations[:3])
    text = "模型服务当前不可用，以下为基于检索片段的降级回答（仅供参考）：\n\n"
    if snippet:
        text += f"根据你的文档，检索到相关内容：…{snippet[:600]}…"
    else:
        text += "本次未检索到可引用的来源内容。"
    for char in text:
        yield {"event": "token", "token": char}
        await asyncio.sleep(0)


def _chat_delta(payload: object) -> str:
    if not isinstance(payload, dict):
        raise ChatStreamError("CHAT_STREAM_INVALID")
    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
        raise ChatStreamError("CHAT_STREAM_INVALID")
    delta = choices[0].get("delta")
    if not isinstance(delta, dict):
        raise ChatStreamError("CHAT_STREAM_INVALID")
    content = delta.get("content", "")
    if content is None:
        return ""
    if not isinstance(content, str):
        raise ChatStreamError("CHAT_STREAM_INVALID")
    return content


async def stream_chat(
    messages: list[dict],
    citations: list[dict],
) -> AsyncIterator[dict]:
    """远程流必须是 200、合法 choices SSE 且以 [DONE] 收尾。"""
    if not settings.llama_chat_base_url:
        async for event in _template_chat(citations):
            yield event
        return

    emitted_token = False
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(300.0, connect=10.0)) as client:
            async with client.stream(
                "POST",
                f"{settings.llama_chat_base_url.rstrip('/')}/v1/chat/completions",
                json={
                    "model": settings.chat_model_id or "default",
                    "messages": messages,
                    "stream": True,
                },
            ) as response:
                if response.status_code != 200:
                    raise ChatStreamError("CHAT_HTTP_STATUS")
                saw_done = False
                async for line in response.aiter_lines():
                    if not line or line.startswith((":", "event:", "id:", "retry:")):
                        continue
                    if not line.startswith("data:"):
                        raise ChatStreamError("CHAT_STREAM_INVALID")
                    raw = line[5:].strip()
                    if raw == "[DONE]":
                        saw_done = True
                        break
                    try:
                        payload = json.loads(raw)
                    except json.JSONDecodeError as exc:
                        raise ChatStreamError("CHAT_STREAM_INVALID") from exc
                    content = _chat_delta(payload)
                    if content:
                        emitted_token = True
                        yield {"event": "token", "token": content}
                if not saw_done:
                    raise ChatStreamError("CHAT_STREAM_INCOMPLETE")
    except (httpx.TimeoutException, httpx.RequestError) as exc:
        error = ChatStreamError("CHAT_NETWORK", retryable=True)
        if emitted_token:
            raise error from exc
    except ChatStreamError:
        if emitted_token:
            raise
    except Exception as exc:
        error = ChatStreamError("CHAT_STREAM_FAILURE", retryable=True)
        if emitted_token:
            raise error from exc
    else:
        if emitted_token:
            return

    # 仅尚未发出任何 token 的失败/空流可降级；partial 永远由上方抛出。
    async for event in _template_chat(citations):
        yield event
