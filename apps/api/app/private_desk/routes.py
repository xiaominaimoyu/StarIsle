"""私人书桌页面。登录页匿名可开，其余页面要有有效会话。"""

from __future__ import annotations

from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, RedirectResponse

from ..deps import AuthError, require_auth
from ..security import ACCESS_COOKIE

router = APIRouter(tags=["private-desk"])

_STATIC = Path(__file__).resolve().parent / "static"
_PAGES = {
    "login.html": "text/html; charset=utf-8",
    "home.html": "text/html; charset=utf-8",
    "album.html": "text/html; charset=utf-8",
    "messages.html": "text/html; charset=utf-8",
}
_ASSETS = {
    "desk.css": "text/css; charset=utf-8",
    "desk.js": "text/javascript; charset=utf-8",
    "login.js": "text/javascript; charset=utf-8",
    "home.js": "text/javascript; charset=utf-8",
    "album.js": "text/javascript; charset=utf-8",
    "messages.js": "text/javascript; charset=utf-8",
    "private-state.mjs": "text/javascript; charset=utf-8",
}
_ALLOWED_NEXT = {"/private", "/private/", "/private/album", "/private/messages"}
_PAGE_HEADERS = {
    "Content-Security-Policy": (
        "default-src 'self'; img-src 'self'; style-src 'self'; script-src 'self'; "
        "connect-src 'self'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'"
    ),
    "X-Robots-Tag": "noindex, nofollow",
}


def _file(name: str) -> Path:
    path = (_STATIC / name).resolve()
    if path.parent != _STATIC or not path.is_file():
        raise HTTPException(status_code=404, detail={"code": "NOT_FOUND", "message": "页面不存在"})
    return path


def _page(name: str) -> FileResponse:
    return FileResponse(_file(name), media_type=_PAGES[name], headers=_PAGE_HEADERS)


def _redirect(next_path: str) -> RedirectResponse:
    safe = next_path if next_path in _ALLOWED_NEXT else "/private/album"
    return RedirectResponse(url=f"/private/login?next={quote(safe, safe='/')}", status_code=302)


def _signed_in(request: Request) -> bool:
    try:
        require_auth(request.cookies.get(ACCESS_COOKIE))
    except AuthError:
        return False
    return True


@router.get("/private/login")
def private_login() -> FileResponse:
    return _page("login.html")


@router.get("/private")
@router.get("/private/")
def private_home(request: Request):
    if not _signed_in(request):
        return _redirect("/private/")
    return _page("home.html")


@router.get("/private/messages")
def private_messages(request: Request):
    if not _signed_in(request):
        return _redirect("/private/messages")
    return _page("messages.html")


@router.get("/private/album")
def private_album(request: Request):
    if not _signed_in(request):
        return _redirect("/private/album")
    return _page("album.html")


@router.get("/private/assets/{name}")
def private_asset(name: str) -> FileResponse:
    media = _ASSETS.get(name)
    if media is None:
        raise HTTPException(status_code=404, detail={"code": "NOT_FOUND", "message": "页面不存在"})
    return FileResponse(_file(name), media_type=media, headers={"X-Robots-Tag": "noindex, nofollow"})
