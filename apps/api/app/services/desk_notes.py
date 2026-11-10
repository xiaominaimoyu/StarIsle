"""手记与日记。Markdown 只生成一小部分标签，再交给 nh3 去掉其余 HTML。"""

from __future__ import annotations

import hashlib
import re
from datetime import date
from typing import Any

import nh3

from ..db import get_conn
from ..security import iso, now_utc, random_id

TITLE_MAX = 80
BODY_MAX = 20_000
TAG_MAX = 24
TAG_COUNT = 12
QUERY_MAX = 40
_PHOTO_ID = re.compile(r"^[A-Za-z0-9_-]{1,80}$")
_PHOTO_MARK = re.compile(r"!\[([^\]\n]{0,120})\]\(photo:([A-Za-z0-9_-]{1,80})\)")
_LINK_MARK = re.compile(r"\[([^\]\n]{1,200})\]\((https?://[^\s)]{1,500})\)")
_PHOTO_SRC = re.compile(r"^/api/v1/photos/[A-Za-z0-9_-]{1,80}/original$")
_NOTE_HTML_TAGS = {"p", "br", "h1", "h2", "h3", "strong", "em", "code", "pre", "ul", "ol", "li", "blockquote", "a", "img"}
_NOTE_HTML_ATTRS = {"a": {"href"}, "img": {"src", "alt"}}


class DeskNoteError(Exception):
    def __init__(self, code: str, message: str, status: int = 422, note_id: str | None = None):
        self.code = code
        self.message = message
        self.status = status
        self.note_id = note_id


def content_hash(title: str, body: str, tags: list[str]) -> str:
    payload = title + "\n" + body + "\n" + "\n".join(tags)
    return hashlib.sha256(payload.encode()).hexdigest()


def search_grams(text: str) -> str:
    compact = "".join(ch for ch in text if ch.isalnum())
    grams: list[str] = []
    for size in (2, 3):
        grams.extend(compact[i : i + size] for i in range(max(0, len(compact) - size + 1)))
    grams.extend(re.findall(r"[A-Za-z0-9]{2,}", text))
    seen: set[str] = set()
    unique: list[str] = []
    for gram in grams:
        if gram in seen:
            continue
        seen.add(gram)
        unique.append(gram)
    return " ".join(unique)


def fts_query(raw: str) -> str | None:
    text = raw.strip()[:QUERY_MAX]
    compact = "".join(ch for ch in text if ch.isalnum())
    tokens = [compact[i : i + 2] for i in range(len(compact) - 1)]
    tokens.extend(re.findall(r"[A-Za-z0-9]{2,}", text))
    quoted: list[str] = []
    seen: set[str] = set()
    for token in tokens:
        if token in seen:
            continue
        seen.add(token)
        quoted.append(f'"{token}"')
        if len(quoted) == 24:
            break
    if not quoted:
        return None
    return " AND ".join(quoted)


def photo_ids_in(body: str) -> list[str]:
    seen: set[str] = set()
    found: list[str] = []
    for match in _PHOTO_MARK.finditer(body):
        photo_id = match.group(2)
        if photo_id in seen or not _PHOTO_ID.fullmatch(photo_id):
            continue
        seen.add(photo_id)
        found.append(photo_id)
    return found


def _escape(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


def _inline(text: str) -> str:
    out: list[str] = []
    index = 0
    while index < len(text):
        if text.startswith("**", index):
            end = text.find("**", index + 2)
            if end != -1:
                out.append("<strong>" + _escape(text[index + 2 : end]) + "</strong>")
                index = end + 2
                continue
        if text.startswith("`", index):
            end = text.find("`", index + 1)
            if end != -1:
                out.append("<code>" + _escape(text[index + 1 : end]) + "</code>")
                index = end + 1
                continue
        if text.startswith("![", index):
            mark = _PHOTO_MARK.match(text, index)
            if mark and mark.start() == index:
                src = f"/api/v1/photos/{mark.group(2)}/original"
                out.append(f'<img src="{src}" alt="{_escape(mark.group(1))}">')
                index = mark.end()
                continue
            foreign = re.match(r"!\[[^\]\n]{0,120}\]\([^)\n]{0,500}\)", text[index:])
            if foreign:
                out.append(_escape(foreign.group(0)))
                index += foreign.end()
                continue
        if text.startswith("[", index):
            mark = _LINK_MARK.match(text, index)
            if mark and mark.start() == index:
                out.append(f'<a href="{_escape(mark.group(2))}">{_escape(mark.group(1))}</a>')
                index = mark.end()
                continue
        if text.startswith("*", index):
            end = text.find("*", index + 1)
            if end != -1:
                out.append("<em>" + _escape(text[index + 1 : end]) + "</em>")
                index = end + 1
                continue
        out.append(_escape(text[index]))
        index += 1
    return "".join(out)


def _block_start(line: str) -> bool:
    return (
        line.startswith("#")
        or line.startswith("- ")
        or line.startswith("> ")
        or line.startswith("```")
        or bool(re.match(r"\d+\. ", line))
    )


def _keep_note_attribute(_tag: str, attr: str, value: str) -> str | None:
    if attr == "src" and _PHOTO_SRC.fullmatch(value) is None:
        return None
    if attr == "href" and not (value.startswith("https://") or value.startswith("http://")):
        return None
    return value


def sanitize_note_html(html: str) -> str:
    return nh3.clean(
        html,
        tags=_NOTE_HTML_TAGS,
        attributes=_NOTE_HTML_ATTRS,
        attribute_filter=_keep_note_attribute,
        url_schemes={"http", "https"},
        link_rel="noopener noreferrer",
    )


def render_markdown(source: str) -> str:
    lines = source.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    html: list[str] = []
    index = 0
    while index < len(lines):
        line = lines[index]
        if line.startswith("```"):
            index += 1
            block: list[str] = []
            while index < len(lines) and not lines[index].startswith("```"):
                block.append(lines[index])
                index += 1
            if index < len(lines):
                index += 1
            html.append("<pre><code>" + _escape("\n".join(block)) + "</code></pre>")
            continue
        if not line.strip():
            index += 1
            continue
        if line.startswith("### "):
            html.append("<h3>" + _inline(line[4:]) + "</h3>")
            index += 1
            continue
        if line.startswith("## "):
            html.append("<h2>" + _inline(line[3:]) + "</h2>")
            index += 1
            continue
        if line.startswith("# "):
            html.append("<h1>" + _inline(line[2:]) + "</h1>")
            index += 1
            continue
        if line.startswith("- "):
            items: list[str] = []
            while index < len(lines) and lines[index].startswith("- "):
                items.append("<li>" + _inline(lines[index][2:]) + "</li>")
                index += 1
            html.append("<ul>" + "".join(items) + "</ul>")
            continue
        numbered = re.match(r"\d+\. (.*)", line)
        if numbered:
            items = []
            while index < len(lines):
                item = re.match(r"\d+\. (.*)", lines[index])
                if not item:
                    break
                items.append("<li>" + _inline(item.group(1)) + "</li>")
                index += 1
            html.append("<ol>" + "".join(items) + "</ol>")
            continue
        if line.startswith("> "):
            quotes: list[str] = []
            while index < len(lines) and lines[index].startswith("> "):
                quotes.append(_inline(lines[index][2:]))
                index += 1
            html.append("<blockquote><p>" + " ".join(quotes) + "</p></blockquote>")
            continue
        paragraph = [line]
        index += 1
        while index < len(lines) and lines[index].strip() and not _block_start(lines[index]):
            paragraph.append(lines[index])
            index += 1
        html.append("<p>" + _inline(" ".join(paragraph)) + "</p>")
    return sanitize_note_html("".join(html))


def parse_title(value: Any, *, default: str = "") -> str:
    if value is None:
        text = default
    elif not isinstance(value, str):
        raise DeskNoteError("UNPROCESSABLE", "标题无效。")
    else:
        text = value.strip() or default
    if len(text) > TITLE_MAX:
        raise DeskNoteError("UNPROCESSABLE", "标题太长。")
    if not text:
        raise DeskNoteError("UNPROCESSABLE", "请写一个标题。")
    return text


def parse_body(value: Any) -> str:
    if value is None:
        return ""
    if not isinstance(value, str) or len(value) > BODY_MAX:
        raise DeskNoteError("UNPROCESSABLE", "正文太长或无效。")
    return value.replace("\x00", "")


def parse_tags(value: Any) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise DeskNoteError("UNPROCESSABLE", "标签无效。")
    tags: list[str] = []
    seen: set[str] = set()
    for item in value:
        if not isinstance(item, str):
            raise DeskNoteError("UNPROCESSABLE", "标签无效。")
        tag = item.strip().casefold()
        if not tag:
            continue
        if len(tag) > TAG_MAX or any(ch.isspace() for ch in tag):
            raise DeskNoteError("UNPROCESSABLE", "标签无效。")
        if tag in seen:
            continue
        seen.add(tag)
        tags.append(tag)
        if len(tags) > TAG_COUNT:
            raise DeskNoteError("UNPROCESSABLE", "标签太多。")
    return tags


def parse_day(value: str) -> str:
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        raise DeskNoteError("UNPROCESSABLE", "日期无效。")
    try:
        date.fromisoformat(value)
    except ValueError as exc:
        raise DeskNoteError("UNPROCESSABLE", "日期无效。") from exc
    return value


def parse_month(value: str) -> str:
    if not re.fullmatch(r"\d{4}-\d{2}", value):
        raise DeskNoteError("UNPROCESSABLE", "月份无效。")
    try:
        date.fromisoformat(value + "-01")
    except ValueError as exc:
        raise DeskNoteError("UNPROCESSABLE", "月份无效。") from exc
    return value


def _tags_for(conn, note_id: str) -> list[str]:
    rows = conn.execute(
        "SELECT tag FROM desk_note_tag WHERE note_id=? ORDER BY tag",
        (note_id,),
    ).fetchall()
    return [row["tag"] for row in rows]


def _photos_for(conn, note_id: str) -> list[str]:
    rows = conn.execute(
        "SELECT photo_id FROM desk_note_photo WHERE note_id=? ORDER BY photo_id",
        (note_id,),
    ).fetchall()
    return [row["photo_id"] for row in rows]


def _public(row, tags: list[str], photos: list[str]) -> dict:
    return {
        "id": row["id"],
        "title": row["title"],
        "body": row["body_md"],
        "html": render_markdown(row["body_md"]),
        "tags": tags,
        "photoIds": photos,
        "diaryOn": row["diary_on"],
        "contentHash": row["content_hash"],
        "createdAt": row["created_at"],
        "updatedAt": row["updated_at"],
    }


def _summary(row, tags: list[str]) -> dict:
    return {
        "id": row["id"],
        "title": row["title"],
        "tags": tags,
        "diaryOn": row["diary_on"],
        "contentHash": row["content_hash"],
        "updatedAt": row["updated_at"],
    }


def _load(conn, note_id: str):
    return conn.execute(
        "SELECT * FROM desk_note WHERE id=? AND deleted_at IS NULL",
        (note_id,),
    ).fetchone()


def _require_photos(conn, photo_ids: list[str]) -> None:
    for photo_id in photo_ids:
        row = conn.execute("SELECT id FROM photo WHERE id=?", (photo_id,)).fetchone()
        if row is None:
            raise DeskNoteError("PHOTO_NOT_FOUND", "手记引用了相册里没有的照片。")


def _write_links(conn, note_id: str, tags: list[str], photo_ids: list[str]) -> None:
    conn.execute("DELETE FROM desk_note_tag WHERE note_id=?", (note_id,))
    conn.execute("DELETE FROM desk_note_photo WHERE note_id=?", (note_id,))
    conn.executemany(
        "INSERT INTO desk_note_tag (note_id, tag) VALUES (?, ?)",
        [(note_id, tag) for tag in tags],
    )
    conn.executemany(
        "INSERT INTO desk_note_photo (note_id, photo_id) VALUES (?, ?)",
        [(note_id, photo_id) for photo_id in photo_ids],
    )


def _replace_fts(conn, note_id: str, title: str, body: str) -> None:
    rows = conn.execute("SELECT rowid FROM desk_note_fts WHERE note_id=?", (note_id,)).fetchall()
    for row in rows:
        conn.execute("DELETE FROM desk_note_fts WHERE rowid=?", (row["rowid"],))
    conn.execute(
        "INSERT INTO desk_note_fts (note_id, grams) VALUES (?, ?)",
        (note_id, search_grams(f"{title}\n{body}")),
    )


def _drop_fts(conn, note_id: str) -> None:
    rows = conn.execute("SELECT rowid FROM desk_note_fts WHERE note_id=?", (note_id,)).fetchall()
    for row in rows:
        conn.execute("DELETE FROM desk_note_fts WHERE rowid=?", (row["rowid"],))


def read_note(note_id: str) -> dict:
    with get_conn() as conn:
        row = _load(conn, note_id)
        if row is None:
            raise DeskNoteError("NOT_FOUND", "手记不存在。", 404)
        return _public(row, _tags_for(conn, note_id), _photos_for(conn, note_id))


def create_note(*, title: str, body: str, tags: list[str], diary_on: str | None) -> dict:
    photo_ids = photo_ids_in(body)
    moment = iso(now_utc())
    note_id = random_id("note_")
    digest = content_hash(title, body, tags)
    with get_conn() as conn:
        _require_photos(conn, photo_ids)
        if diary_on is not None:
            existing = conn.execute(
                "SELECT id FROM desk_note WHERE diary_on=? AND deleted_at IS NULL",
                (diary_on,),
            ).fetchone()
            if existing is not None:
                raise DeskNoteError("DIARY_EXISTS", "这一天已经有一篇日记。", 409, existing["id"])
        conn.execute(
            """
            INSERT INTO desk_note
              (id, title, body_md, content_hash, diary_on, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (note_id, title, body, digest, diary_on, moment, moment),
        )
        _write_links(conn, note_id, tags, photo_ids)
        _replace_fts(conn, note_id, title, body)
        row = _load(conn, note_id)
    return _public(row, tags, photo_ids)


def open_diary(day: str) -> dict:
    day = parse_day(day)
    with get_conn() as conn:
        existing = conn.execute(
            "SELECT * FROM desk_note WHERE diary_on=? AND deleted_at IS NULL",
            (day,),
        ).fetchone()
        if existing is not None:
            return _public(existing, _tags_for(conn, existing["id"]), _photos_for(conn, existing["id"]))
    return create_note(title=day, body="", tags=["日记"], diary_on=day)


def update_note(note_id: str, *, title: str, body: str, tags: list[str], base_hash: str) -> dict:
    if len(base_hash) != 64 or any(ch not in "0123456789abcdef" for ch in base_hash):
        raise DeskNoteError("UNPROCESSABLE", "缺少内容版本。")
    photo_ids = photo_ids_in(body)
    moment = iso(now_utc())
    digest = content_hash(title, body, tags)
    with get_conn() as conn:
        current = _load(conn, note_id)
        if current is None:
            raise DeskNoteError("NOT_FOUND", "手记不存在。", 404)
        _require_photos(conn, photo_ids)
        updated = conn.execute(
            """
            UPDATE desk_note
            SET title=?, body_md=?, content_hash=?, updated_at=?
            WHERE id=? AND deleted_at IS NULL AND content_hash=?
            """,
            (title, body, digest, moment, note_id, base_hash),
        )
        if updated.rowcount != 1:
            if _load(conn, note_id) is None:
                raise DeskNoteError("NOT_FOUND", "手记不存在。", 404)
            raise DeskNoteError("STALE_NOTE", "这篇手记刚刚更新过，没有覆盖。", 409)
        _write_links(conn, note_id, tags, photo_ids)
        _replace_fts(conn, note_id, title, body)
        row = _load(conn, note_id)
    return _public(row, tags, photo_ids)


def delete_note(note_id: str) -> None:
    moment = iso(now_utc())
    with get_conn() as conn:
        current = _load(conn, note_id)
        if current is None:
            raise DeskNoteError("NOT_FOUND", "手记不存在。", 404)
        conn.execute(
            "UPDATE desk_note SET deleted_at=?, updated_at=? WHERE id=?",
            (moment, moment, note_id),
        )
        _drop_fts(conn, note_id)


def list_notes(*, query: str, tag: str, kind: str) -> list[dict]:
    if kind not in {"", "note", "diary"}:
        raise DeskNoteError("UNPROCESSABLE", "请求参数无效。")
    if tag and (len(tag) > TAG_MAX or any(ch.isspace() for ch in tag)):
        raise DeskNoteError("UNPROCESSABLE", "标签无效。")
    match = fts_query(query) if query.strip() else None
    if query.strip() and match is None:
        return []
    sql = "SELECT * FROM desk_note WHERE deleted_at IS NULL"
    params: list[str] = []
    if kind == "diary":
        sql += " AND diary_on IS NOT NULL"
    elif kind == "note":
        sql += " AND diary_on IS NULL"
    if tag:
        sql += " AND id IN (SELECT note_id FROM desk_note_tag WHERE tag=?)"
        params.append(tag.casefold())
    if match is not None:
        sql += " AND id IN (SELECT note_id FROM desk_note_fts WHERE desk_note_fts MATCH ?)"
        params.append(match)
    sql += " ORDER BY updated_at DESC, id DESC LIMIT 50"
    with get_conn() as conn:
        rows = conn.execute(sql, params).fetchall()
        return [_summary(row, _tags_for(conn, row["id"])) for row in rows]


def diary_month(month: str) -> dict:
    month = parse_month(month)
    with get_conn() as conn:
        rows = conn.execute(
            """
            SELECT id, title, diary_on FROM desk_note
            WHERE deleted_at IS NULL AND diary_on LIKE ?
            ORDER BY diary_on
            """,
            (month + "-%",),
        ).fetchall()
    return {
        "month": month,
        "days": [{"date": row["diary_on"], "id": row["id"], "title": row["title"]} for row in rows],
    }
