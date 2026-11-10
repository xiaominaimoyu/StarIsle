"""私人书桌总览：待办、近况和一眼能看完的数量。"""

from __future__ import annotations

from datetime import date

from ..db import get_conn
from ..security import iso, now_utc, random_id

TEXT_MAX = 200
ACTIVITY_LIMIT = 20


class DeskBoardError(Exception):
    def __init__(self, code: str, message: str, status: int = 422) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status


def parse_text(value: object) -> str:
    if not isinstance(value, str):
        raise DeskBoardError("UNPROCESSABLE", "待办内容无效。")
    text = value.replace("\x00", "").strip()
    if not text or len(text) > TEXT_MAX:
        raise DeskBoardError("UNPROCESSABLE", "待办内容无效。")
    return text


def parse_due(value: object) -> str | None:
    if value is None or value == "":
        return None
    if not isinstance(value, str):
        raise DeskBoardError("UNPROCESSABLE", "日期无效。")
    try:
        date.fromisoformat(value)
    except ValueError as exc:
        raise DeskBoardError("UNPROCESSABLE", "日期无效。") from exc
    return value


def _todo(row) -> dict:
    return {
        "id": row["id"],
        "text": row["text"],
        "dueOn": row["due_on"],
        "done": row["done_at"] is not None,
        "doneAt": row["done_at"],
        "createdAt": row["created_at"],
        "updatedAt": row["updated_at"],
    }


def list_todos() -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute(
            """
            SELECT * FROM desk_todo
            ORDER BY (done_at IS NOT NULL),
                     CASE WHEN due_on IS NULL THEN 1 ELSE 0 END,
                     due_on,
                     created_at,
                     id
            """
        ).fetchall()
    return [_todo(row) for row in rows]


def create_todo(*, text: str, due_on: str | None) -> dict:
    moment = iso(now_utc())
    todo_id = random_id("todo_")
    with get_conn() as conn:
        conn.execute(
            """
            INSERT INTO desk_todo (id, text, due_on, done_at, created_at, updated_at)
            VALUES (?, ?, ?, NULL, ?, ?)
            """,
            (todo_id, text, due_on, moment, moment),
        )
        row = conn.execute("SELECT * FROM desk_todo WHERE id=?", (todo_id,)).fetchone()
    return _todo(row)


def update_todo(todo_id: str, *, text: str | None, due_on: str | None, due_set: bool, done: bool | None) -> dict:
    moment = iso(now_utc())
    with get_conn() as conn:
        current = conn.execute("SELECT * FROM desk_todo WHERE id=?", (todo_id,)).fetchone()
        if current is None:
            raise DeskBoardError("NOT_FOUND", "待办不存在。", 404)
        next_text = text if text is not None else current["text"]
        next_due = due_on if due_set else current["due_on"]
        if done is True:
            next_done = current["done_at"] or moment
        elif done is False:
            next_done = None
        else:
            next_done = current["done_at"]
        conn.execute(
            "UPDATE desk_todo SET text=?, due_on=?, done_at=?, updated_at=? WHERE id=?",
            (next_text, next_due, next_done, moment, todo_id),
        )
        row = conn.execute("SELECT * FROM desk_todo WHERE id=?", (todo_id,)).fetchone()
    return _todo(row)


def delete_todo(todo_id: str) -> None:
    with get_conn() as conn:
        deleted = conn.execute("DELETE FROM desk_todo WHERE id=?", (todo_id,))
        if deleted.rowcount != 1:
            raise DeskBoardError("NOT_FOUND", "待办不存在。", 404)


def _activity() -> list[dict]:
    items: list[dict] = []
    with get_conn() as conn:
        notes = conn.execute(
            """
            SELECT id, title, diary_on, created_at, updated_at
            FROM desk_note WHERE deleted_at IS NULL
            """
        ).fetchall()
        letters = conn.execute(
            """
            SELECT id, name, imported_at FROM visitor_letter
            WHERE deleted_at IS NULL
            """
        ).fetchall()
        photos = conn.execute(
            """
            SELECT p.id, p.created_at, a.original_name
            FROM photo p JOIN asset a ON a.id = p.asset_id
            """
        ).fetchall()
    for row in notes:
        kind = "diary" if row["diary_on"] else "note"
        created = row["updated_at"] == row["created_at"]
        if kind == "diary":
            label = "写了日记" if created else "改了日记"
        else:
            label = "写了手记" if created else "改了手记"
        items.append({
            "kind": kind,
            "id": row["id"],
            "title": row["title"] or "手记",
            "action": label,
            "at": row["updated_at"],
            "href": f"/private/notes?note={row['id']}",
        })
    for row in letters:
        items.append({
            "kind": "letter",
            "id": row["id"],
            "title": row["name"] or "来信",
            "action": "收到来信",
            "at": row["imported_at"],
            "href": f"/private/messages?letter={row['id']}",
        })
    for row in photos:
        items.append({
            "kind": "photo",
            "id": row["id"],
            "title": row["original_name"] or "照片",
            "action": "上传了照片",
            "at": row["created_at"],
            "href": "/private/album",
        })
    items.sort(key=lambda item: (item["at"], item["kind"], item["id"]), reverse=True)
    return items[:ACTIVITY_LIMIT]


def home_summary() -> dict:
    with get_conn() as conn:
        photos = conn.execute("SELECT COUNT(*) FROM photo").fetchone()[0]
        unread = conn.execute(
            """
            SELECT COUNT(*) FROM visitor_letter
            WHERE deleted_at IS NULL AND archived_at IS NULL AND read_at IS NULL
            """
        ).fetchone()[0]
        notes = conn.execute("SELECT COUNT(*) FROM desk_note WHERE deleted_at IS NULL").fetchone()[0]
        open_todos = conn.execute("SELECT COUNT(*) FROM desk_todo WHERE done_at IS NULL").fetchone()[0]
        ready = conn.execute("SELECT COUNT(*) FROM desk_index_item WHERE status='ready'").fetchone()[0]
        pending = conn.execute("SELECT COUNT(*) FROM desk_index_item WHERE status='pending'").fetchone()[0]
    return {
        "counts": {
            "photos": int(photos),
            "unreadMessages": int(unread),
            "notes": int(notes),
            "openTodos": int(open_todos),
        },
        "index": {"ready": int(ready), "pending": int(pending)},
        "todos": list_todos(),
        "activity": _activity(),
    }
