"""问屿：只索引这台电脑上的手记、日记、来信和照片说明，只访问回环地址上的模型。"""

from __future__ import annotations

import hashlib
import json
import math
import struct
from dataclasses import dataclass
from typing import AsyncIterator
from urllib.parse import urlsplit

import httpx

from ..config import settings
from ..db import get_conn
from ..security import iso, now_utc, random_id
from .rag_engine import split_text

_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})
_SCORE_FLOOR = 0.35
_INDEX_BATCH = 32
EMBED_OFFLINE = "嵌入服务没开。BGE-M3 需要在 127.0.0.1:8001。已经做好的索引还留着，恢复后点「继续索引」。"
CHAT_OFFLINE = "生成服务没开。Qwen 需要在 127.0.0.1:8000。如果为了 ComfyUI 用过 Stop-Qwen，重新启动后再问。手记和相册不受影响。"
URL_REFUSED = "模型地址不是这台电脑的回环地址，已拒绝连接。"
DIM_MISMATCH = "嵌入结果和设置的维度不一致。BGE-M3 一般是 1024，请在 runtime.env 里把 EMBEDDING_DIM 改成 1024。"
UNKNOWN = "我在手记、日记和来信里没有找到可以依据的内容。"


class DeskAskError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class _Source:
    kind: str
    source_id: str
    title: str
    updated_at: str
    content_hash: str
    text: str
    href: str


def loopback_base(value: str) -> bool:
    try:
        parsed = urlsplit(value.strip())
        _ = parsed.port
    except (TypeError, ValueError):
        return False
    return bool(
        parsed.scheme in {"http", "https"}
        and parsed.hostname in _LOOPBACK_HOSTS
        and parsed.username is None
        and parsed.password is None
        and not parsed.query
        and not parsed.fragment
    )


def embed_base() -> str:
    configured = settings.llama_embed_base_url.strip()
    return configured or "http://127.0.0.1:8001"


def chat_base() -> str:
    configured = settings.llama_chat_base_url.strip()
    return configured or "http://127.0.0.1:8000"


def _model_id(kind: str) -> str:
    if kind == "embed":
        return settings.embedding_model_id or "bge-m3"
    return settings.chat_model_id or "qwen"


def _digest(*parts: str) -> str:
    return hashlib.sha256("\n".join(parts).encode()).hexdigest()


def _pack(vector: list[float]) -> bytes:
    return struct.pack(f"<{len(vector)}f", *vector)


def _unpack(blob: bytes, dim: int) -> list[float]:
    if len(blob) != dim * 4:
        raise DeskAskError("EMBED_DIM", DIM_MISMATCH)
    return list(struct.unpack(f"<{dim}f", blob))


def _normalize(vector: list[float]) -> list[float]:
    norm = math.sqrt(sum(value * value for value in vector))
    if not math.isfinite(norm) or norm <= 0:
        raise DeskAskError("EMBED_DIM", DIM_MISMATCH)
    return [value / norm for value in vector]


def _probe(base: str) -> str:
    if not loopback_base(base):
        return "refused"
    try:
        with httpx.Client(timeout=httpx.Timeout(1.0, connect=0.4), follow_redirects=False) as client:
            response = client.get(f"{base.rstrip('/')}/v1/models")
    except httpx.HTTPError:
        return "down"
    if response.status_code >= 500:
        return "down"
    return "up"


def _where(base: str) -> str:
    parsed = urlsplit(base)
    host = parsed.hostname or "127.0.0.1"
    if parsed.port:
        return f"{host}:{parsed.port}"
    return host


def _message_for(kind: str, state: str) -> str:
    if state == "up":
        return "嵌入服务在听。" if kind == "embed" else "生成服务在听。"
    if state == "refused":
        return URL_REFUSED
    if kind == "embed":
        return f"嵌入服务没开。BGE-M3 需要在 {_where(embed_base())}。已经做好的索引还留着，恢复后点「继续索引」。"
    return (
        f"生成服务没开。Qwen 需要在 {_where(chat_base())}。"
        "如果为了 ComfyUI 用过 Stop-Qwen，重新启动后再问。手记和相册不受影响。"
    )


def service_status() -> dict:
    embed_state = _probe(embed_base())
    chat_state = _probe(chat_base())
    with get_conn() as conn:
        ready = conn.execute("SELECT COUNT(*) FROM desk_index_item WHERE status='ready'").fetchone()[0]
        pending = conn.execute("SELECT COUNT(*) FROM desk_index_item WHERE status='pending'").fetchone()[0]
        chunks = conn.execute("SELECT COUNT(*) FROM desk_index_chunk").fetchone()[0]
    return {
        "embed": {"state": embed_state, "message": _message_for("embed", embed_state)},
        "chat": {"state": chat_state, "message": _message_for("chat", chat_state)},
        "ready": int(ready),
        "pending": int(pending),
        "chunks": int(chunks),
    }


def _photo_context(conn, photo_id: str) -> str:
    rows = conn.execute(
        "SELECT title, body_md FROM desk_note WHERE deleted_at IS NULL AND instr(body_md, ?) > 0",
        (f"photo:{photo_id}",),
    ).fetchall()
    snippets: list[str] = []
    needle = f"photo:{photo_id}"
    for row in rows:
        body = row["body_md"]
        at = body.find(needle)
        if at < 0:
            continue
        start = max(0, at - 80)
        snippets.append(f"{row['title']}：{body[start:at + len(needle) + 80]}")
    return "\n".join(snippets)


def _desired(conn) -> dict[tuple[str, str], _Source]:
    found: dict[tuple[str, str], _Source] = {}
    notes = conn.execute(
        """
        SELECT n.id, n.title, n.body_md, n.content_hash, n.updated_at, n.diary_on
        FROM desk_note n WHERE n.deleted_at IS NULL
        """
    ).fetchall()
    for row in notes:
        tags = conn.execute(
            "SELECT tag FROM desk_note_tag WHERE note_id=? ORDER BY tag",
            (row["id"],),
        ).fetchall()
        tag_text = " ".join(item["tag"] for item in tags)
        text = f"{row['title']}\n{tag_text}\n{row['body_md']}".strip()
        found[("note", row["id"])] = _Source(
            "note",
            row["id"],
            row["title"] or "手记",
            row["updated_at"],
            row["content_hash"],
            text,
            f"/private/notes?note={row['id']}",
        )
    letters = conn.execute(
        """
        SELECT id, name, contact, body, imported_at FROM visitor_letter
        WHERE deleted_at IS NULL
        """
    ).fetchall()
    for row in letters:
        text = f"{row['name']}\n{row['contact']}\n{row['body']}".strip()
        found[("letter", row["id"])] = _Source(
            "letter",
            row["id"],
            row["name"] or "来信",
            row["imported_at"],
            _digest(row["name"], row["contact"], row["body"]),
            text,
            f"/private/messages?letter={row['id']}",
        )
    photos = conn.execute(
        """
        SELECT p.id, p.capture_at, p.created_at, a.original_name
        FROM photo p JOIN asset a ON a.id = p.asset_id
        """
    ).fetchall()
    for row in photos:
        context = _photo_context(conn, row["id"])
        name = row["original_name"] or "照片"
        capture = row["capture_at"] or ""
        text = f"照片 {name}\n拍摄时间 {capture}\n{context}".strip()
        found[("photo", row["id"])] = _Source(
            "photo",
            row["id"],
            name,
            row["capture_at"] or row["created_at"],
            _digest(name, capture, context),
            text,
            "/private/album",
        )
    return found


def _chunks_for(source: _Source) -> list[str]:
    pieces = [piece.strip() for piece in split_text(source.text) if piece.strip()]
    if not pieces:
        pieces = [source.title.strip() or source.kind]
    return pieces[:24]


def _embed(texts: list[str]) -> list[list[float]]:
    base = embed_base()
    if not loopback_base(base):
        raise DeskAskError("URL_REFUSED", URL_REFUSED)
    if _probe(base) != "up":
        raise DeskAskError("EMBED_OFFLINE", EMBED_OFFLINE)
    model = _model_id("embed")
    dim = settings.embedding_dim
    try:
        with httpx.Client(timeout=httpx.Timeout(30.0, connect=1.0), follow_redirects=False) as client:
            response = client.post(
                f"{base.rstrip('/')}/v1/embeddings",
                json={"input": texts, "model": model},
            )
    except httpx.HTTPError as exc:
        raise DeskAskError("EMBED_OFFLINE", EMBED_OFFLINE) from exc
    if response.status_code != 200:
        raise DeskAskError("EMBED_OFFLINE", EMBED_OFFLINE)
    payload = response.json()
    data = payload.get("data") if isinstance(payload, dict) else None
    if payload.get("model") != model or not isinstance(data, list) or len(data) != len(texts):
        raise DeskAskError("EMBED_DIM", DIM_MISMATCH)
    vectors: list[list[float]] = []
    for item in sorted(data, key=lambda row: row.get("index", 0)):
        raw = item.get("embedding") if isinstance(item, dict) else None
        if not isinstance(raw, list) or len(raw) != dim:
            raise DeskAskError("EMBED_DIM", DIM_MISMATCH)
        vectors.append(_normalize([float(value) for value in raw]))
    return vectors


def _replace_chunks(conn, source: _Source, chunks: list[str], vectors: list[list[float]]) -> None:
    conn.execute(
        "DELETE FROM desk_index_chunk WHERE source_kind=? AND source_id=?",
        (source.kind, source.source_id),
    )
    for seq, (text, vector) in enumerate(zip(chunks, vectors, strict=True)):
        conn.execute(
            """
            INSERT INTO desk_index_chunk (id, source_kind, source_id, seq, text, embedding)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (random_id("dch_"), source.kind, source.source_id, seq, text, _pack(vector)),
        )
    conn.execute(
        """
        INSERT INTO desk_index_item
          (source_kind, source_id, content_hash, source_updated_at, indexed_at, status, title)
        VALUES (?, ?, ?, ?, ?, 'ready', ?)
        ON CONFLICT(source_kind, source_id) DO UPDATE SET
          content_hash=excluded.content_hash,
          source_updated_at=excluded.source_updated_at,
          indexed_at=excluded.indexed_at,
          status='ready',
          title=excluded.title
        """,
        (source.kind, source.source_id, source.content_hash, source.updated_at, iso(now_utc()), source.title),
    )


def _mark_pending(conn, source: _Source) -> None:
    conn.execute(
        """
        INSERT INTO desk_index_item
          (source_kind, source_id, content_hash, source_updated_at, indexed_at, status, title)
        VALUES (?, ?, ?, ?, NULL, 'pending', ?)
        ON CONFLICT(source_kind, source_id) DO UPDATE SET
          content_hash=excluded.content_hash,
          source_updated_at=excluded.source_updated_at,
          status='pending',
          title=excluded.title
        """,
        (source.kind, source.source_id, source.content_hash, source.updated_at, source.title),
    )


def _drop_source(conn, kind: str, source_id: str) -> None:
    conn.execute(
        "DELETE FROM desk_index_chunk WHERE source_kind=? AND source_id=?",
        (kind, source_id),
    )
    conn.execute(
        "DELETE FROM desk_index_item WHERE source_kind=? AND source_id=?",
        (kind, source_id),
    )


def sync_sources(*, force: bool = False) -> int:
    with get_conn() as conn:
        desired = _desired(conn)
        rows = conn.execute("SELECT source_kind, source_id, content_hash, source_updated_at, status FROM desk_index_item").fetchall()
        current = {(row["source_kind"], row["source_id"]): row for row in rows}
        pending = 0
        for key, source in desired.items():
            row = current.get(key)
            unchanged = (
                row is not None
                and row["content_hash"] == source.content_hash
                and row["source_updated_at"] == source.updated_at
                and row["status"] == "ready"
            )
            if unchanged and not force:
                continue
            _mark_pending(conn, source)
            pending += 1
        for key in current:
            if key not in desired:
                _drop_source(conn, key[0], key[1])
    return pending


def index_pending(*, force: bool = False) -> dict:
    marked = sync_sources(force=force)
    embed_state = _probe(embed_base())
    if embed_state != "up":
        status = service_status()
        status["code"] = "URL_REFUSED" if embed_state == "refused" else "EMBED_OFFLINE"
        status["indexed"] = 0
        status["marked"] = marked
        return status
    indexed = 0
    with get_conn() as conn:
        waiting = conn.execute(
            """
            SELECT source_kind, source_id FROM desk_index_item
            WHERE status='pending' ORDER BY source_kind, source_id LIMIT ?
            """,
            (_INDEX_BATCH,),
        ).fetchall()
        wanted = {(row["source_kind"], row["source_id"]) for row in waiting}
        sources = {key: source for key, source in _desired(conn).items() if key in wanted}
    for key in wanted:
        source = sources.get(key)
        if source is None:
            with get_conn() as conn:
                _drop_source(conn, key[0], key[1])
            continue
        chunks = _chunks_for(source)
        try:
            vectors = _embed(chunks)
        except DeskAskError as exc:
            status = service_status()
            status["code"] = exc.code
            status["indexed"] = indexed
            status["message"] = exc.message
            return status
        with get_conn() as conn:
            _replace_chunks(conn, source, chunks, vectors)
        indexed += 1
    status = service_status()
    status["code"] = "INDEX_OK"
    status["indexed"] = indexed
    return status


def _search(query_vector: list[float]) -> list[dict]:
    dim = len(query_vector)
    with get_conn() as conn:
        rows = conn.execute(
            """
            SELECT c.id, c.source_kind, c.source_id, c.seq, c.text, c.embedding, i.title
            FROM desk_index_chunk c
            JOIN desk_index_item i
              ON i.source_kind=c.source_kind AND i.source_id=c.source_id AND i.status='ready'
            """
        ).fetchall()
        hrefs = {key: source.href for key, source in _desired(conn).items()}
    scored: list[dict] = []
    for row in rows:
        vector = _unpack(row["embedding"], dim)
        score = sum(left * right for left, right in zip(query_vector, vector, strict=False))
        scored.append({
            "chunkId": row["id"],
            "kind": row["source_kind"],
            "sourceId": row["source_id"],
            "title": row["title"],
            "snippet": row["text"][:240],
            "href": hrefs.get((row["source_kind"], row["source_id"]), "/private/ask"),
            "score": score,
        })
    scored.sort(key=lambda item: (-item["score"], item["kind"], item["sourceId"], item["chunkId"]))
    return scored[:6]


class _ThinkFilter:
    def __init__(self) -> None:
        self._buffer = ""
        self._inside = False

    def feed(self, text: str) -> str:
        self._buffer += text
        output: list[str] = []
        while self._buffer:
            if self._inside:
                end = self._buffer.find("</think>")
                if end < 0:
                    self._buffer = self._buffer[-16:]
                    break
                self._buffer = self._buffer[end + 8 :]
                self._inside = False
                continue
            start = self._buffer.find("<think>")
            if start < 0:
                hold = 0
                for size in range(1, min(7, len(self._buffer)) + 1):
                    if "<think>".startswith(self._buffer[-size:]):
                        hold = size
                output.append(self._buffer[:-hold] if hold else self._buffer)
                self._buffer = self._buffer[-hold:] if hold else ""
                break
            output.append(self._buffer[:start])
            self._buffer = self._buffer[start + 7 :]
            self._inside = True
        return "".join(output)


def _visible_delta(delta: dict, think: _ThinkFilter) -> str:
    if not isinstance(delta, dict):
        return ""
    if delta.get("reasoning_content") or delta.get("reasoning"):
        delta = {key: value for key, value in delta.items() if key not in {"reasoning_content", "reasoning"}}
    content = delta.get("content")
    if not isinstance(content, str) or not content:
        return ""
    return think.feed(content)


async def _stream_model(messages: list[dict]) -> AsyncIterator[str]:
    base = chat_base()
    if not loopback_base(base):
        raise DeskAskError("URL_REFUSED", URL_REFUSED)
    think = _ThinkFilter()
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(120.0, connect=1.0), follow_redirects=False) as client:
            async with client.stream(
                "POST",
                f"{base.rstrip('/')}/v1/chat/completions",
                json={"model": _model_id("chat"), "messages": messages, "stream": True},
            ) as response:
                if response.status_code != 200:
                    raise DeskAskError("CHAT_OFFLINE", CHAT_OFFLINE)
                async for line in response.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    raw = line[5:].strip()
                    if raw == "[DONE]":
                        break
                    payload = json.loads(raw)
                    choices = payload.get("choices") if isinstance(payload, dict) else None
                    if not isinstance(choices, list) or not choices:
                        continue
                    delta = choices[0].get("delta") if isinstance(choices[0], dict) else None
                    visible = _visible_delta(delta, think)
                    if visible:
                        yield visible
    except DeskAskError:
        raise
    except (httpx.HTTPError, ValueError) as exc:
        raise DeskAskError("CHAT_OFFLINE", CHAT_OFFLINE) from exc


async def answer(query: str) -> AsyncIterator[dict]:
    text = query.strip()
    if not text:
        yield {"event": "error", "code": "EMPTY", "message": "问题不能为空。"}
        return
    if len(text) > 8000:
        yield {"event": "error", "code": "EMPTY", "message": "问题过长，请缩短后重试。"}
        return
    embed_state = _probe(embed_base())
    if embed_state != "up":
        yield {"event": "error", "code": embed_state.upper(), "message": _message_for("embed", embed_state)}
        return
    try:
        query_vector = _embed([text])[0]
    except DeskAskError as exc:
        yield {"event": "error", "code": exc.code, "message": exc.message}
        return
    hits = [hit for hit in _search(query_vector) if hit["score"] >= _SCORE_FLOOR]
    if not hits:
        yield {"event": "token", "token": UNKNOWN}
        yield {"event": "done", "citations": []}
        return
    citations = [{key: hit[key] for key in ("kind", "sourceId", "title", "snippet", "href")} for hit in hits]
    for citation in citations:
        yield {"event": "citation", "citation": citation}
    chat_state = _probe(chat_base())
    if chat_state != "up":
        yield {"event": "token", "token": _message_for("chat", chat_state)}
        yield {"event": "done", "citations": citations}
        return
    context = "\n\n".join(f"[来源{index + 1}] {hit['title']}\n{hit['snippet']}" for index, hit in enumerate(hits))
    messages = [
        {
            "role": "system",
            "content": "你是星屿的书桌。只根据给出的原文回答。原文没有的，就说「我不知道」。不要编造，不要输出思考过程。",
        },
        {"role": "user", "content": f"原文：\n{context}\n\n问题：{text}"},
    ]
    collected = ""
    try:
        async for token in _stream_model(messages):
            collected += token
            yield {"event": "token", "token": token}
    except DeskAskError as exc:
        if collected:
            yield {"event": "error", "code": exc.code, "message": exc.message}
            return
        yield {"event": "token", "token": exc.message}
    if not collected.strip():
        yield {"event": "token", "token": UNKNOWN}
    yield {"event": "done", "citations": citations}
