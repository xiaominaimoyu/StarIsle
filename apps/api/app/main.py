"""FastAPI 入口:CORS 同源默认关闭;所有 /api/v1 路由注册;统一错误结构。"""

from __future__ import annotations

import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from .config import API_ROOT, settings
from .db import audit, init_db
from .deps import get_request_id
from .observability import emit_api_request, route_template, safe_request_id
from .private_desk.routes import router as private_desk_router
from .private_runtime import client_is_loopback
from .request_body_limit import (
    PAYLOAD_TOO_LARGE_DETAIL,
    RequestBodyLimitMiddleware,
    build_request_body_limit_policy,
    is_request_body_limit_overflow,
    should_close_connection,
)
from .routers import auth, files, knowledge, mail, misc, rag
from .storage import StorageUnavailable


def expose_api_docs(env: str) -> tuple[str | None, str | None, str | None]:
    """Interactive docs stay available in development and are hidden in production."""

    if env == "production":
        return None, None, None
    return "/api/docs", "/api/openapi.json", "/redoc"


_docs_url, _openapi_url, _redoc_url = expose_api_docs(settings.star_isle_env)


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    settings.ensure_dirs()
    yield


app = FastAPI(
    title="星屿 StarIsle API",
    version="0.1.0",
    docs_url=_docs_url,
    openapi_url=_openapi_url,
    redoc_url=_redoc_url,
    lifespan=lifespan,
)


# Register before request_context so the latter remains the outer user middleware
# and logs/directly headers early 400/413 responses as well.
app.add_middleware(
    RequestBodyLimitMiddleware,
    router=app.router,
    policy=build_request_body_limit_policy(
        file_max_bytes=settings.file_max_bytes,
        image_max_bytes=settings.image_max_bytes,
        document_max_bytes=knowledge.DOC_MAX_BYTES,
        mail_max_bytes=settings.mail_max_bytes,
    ),
)


def apply_security_headers(response):
    """Baseline headers for the private API, including direct uvicorn without Caddy."""

    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("Referrer-Policy", "no-referrer")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
    if "cache-control" not in response.headers:
        response.headers["Cache-Control"] = "no-store"
    return response


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    return apply_security_headers(response)


@app.middleware("http")
async def private_desk_loopback_guard(request: Request, call_next):
    """启动脚本打开此开关后，非回环来源连健康检查也不放行。"""

    if os.environ.get("STAR_ISLE_PRIVATE_DESK_LOOPBACK_ONLY") == "1":
        host = request.client.host if request.client else None
        if not client_is_loopback(host):
            response = JSONResponse(
                status_code=403,
                content={
                    "error": {
                        "code": "BIND_REFUSED",
                        "message": "私人书桌只接受本机连接。",
                        "requestId": getattr(request.state, "request_id", ""),
                    }
                },
            )
            return apply_security_headers(response)
    return await call_next(request)


@app.middleware("http")
async def request_context(request: Request, call_next):
    # 只把已验证的标识放进 request.state；后续审计、响应和结构化事件共用该值。
    request.state.request_id = safe_request_id(request.headers.get("x-request-id"))
    response = await call_next(request)
    response.headers["X-Request-Id"] = request.state.request_id
    try:
        emit_api_request(
            request_id=request.state.request_id,
            method=request.method,
            route=route_template(request),
            status=response.status_code,
        )
    except Exception:
        # 可观测性是旁路，不能改变既有路由、鉴权或错误响应行为。
        pass
    return response


def _error_body(status: int, detail: object, request: Request) -> dict:
    """统一失败体:{"error": {code, message, requestId}}。"""
    if isinstance(detail, dict):
        code = detail.get("code", "INTERNAL")
        message = detail.get("message", "请求失败。")
    elif isinstance(detail, str):
        code = "BAD_REQUEST" if status < 500 else "INTERNAL"
        message = detail
    else:
        code = "BAD_REQUEST" if status < 500 else "INTERNAL"
        message = "请求失败。"
    return {
        "error": {
            "code": code,
            "message": message,
            "requestId": get_request_id(request),
        }
    }


@app.exception_handler(StarletteHTTPException)
async def http_exception_handler(request: Request, exc: StarletteHTTPException):
    is_body_limit = is_request_body_limit_overflow(exc)
    status_code = 413 if is_body_limit else exc.status_code
    detail = PAYLOAD_TOO_LARGE_DETAIL if is_body_limit else exc.detail
    headers = dict(exc.headers or {})
    if is_body_limit and should_close_connection(request.scope):
        headers["Connection"] = "close"
    return JSONResponse(
        status_code=status_code,
        content=_error_body(status_code, detail, request),
        headers=headers,
    )


@app.exception_handler(RequestValidationError)
async def request_validation_exception_handler(
    request: Request, exc: RequestValidationError
):
    """422 也使用共享 contracts 的错误外形，避免客户端分支猜测。"""
    return JSONResponse(
        status_code=422,
        content=_error_body(
            422, {"code": "UNPROCESSABLE", "message": "请求参数无效。"}, request
        ),
    )


@app.exception_handler(StorageUnavailable)
async def storage_unavailable_exception_handler(
    request: Request, _exc: StorageUnavailable
):
    """锁竞争超时是短暂基础设施故障，不应伪装成永久业务错误。"""

    return JSONResponse(
        status_code=503,
        content=_error_body(
            503,
            {"code": "INTERNAL", "message": "对象存储暂不可用,请稍后重试。"},
            request,
        ),
        headers={"Retry-After": "1"},
    )


@app.exception_handler(Exception)
async def unhandled_exception(request: Request, _exc: Exception):
    request_id = get_request_id(request)
    try:
        audit(
            "unhandled_error",
            resource_type="api",
            request_id=request_id,
            result="INTERNAL",
        )
    except Exception:
        # 审计后端暂时不可用时，固定 500 仍须返回，不能暴露异常内容。
        pass
    response = JSONResponse(
        status_code=500,
        content=_error_body(
            500, {"code": "INTERNAL", "message": "服务异常,请稍后重试。"}, request
        ),
    )
    response.headers["X-Request-Id"] = request_id
    try:
        emit_api_request(
            request_id=request_id,
            method=request.method,
            route=route_template(request),
            status=500,
        )
    except Exception:
        pass
    return response


def include_routers(prefix: str = API_ROOT) -> None:
    app.include_router(auth.router, prefix=prefix)
    app.include_router(files.router, prefix=prefix)
    app.include_router(mail.router, prefix=prefix)
    app.include_router(knowledge.router, prefix=prefix)
    app.include_router(misc.router, prefix=prefix)
    app.include_router(rag.router, prefix=prefix)
    app.include_router(private_desk_router)


include_routers()
