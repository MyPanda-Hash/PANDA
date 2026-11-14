"""Enterprise WeChat self-built application and intelligent bot callbacks."""

from __future__ import annotations

import logging

from fastapi import APIRouter, BackgroundTasks, HTTPException, Query, Request
from fastapi.responses import JSONResponse, PlainTextResponse

from app.services.wecom import (
    WECOM_MAX_CALLBACK_BYTES,
    WeComConfigurationError,
    WeComError,
    aibot_callback_response,
    decrypt_aibot_echo,
    decrypt_aibot_message,
    decrypt_callback_message,
    decrypt_echo,
    encrypt_aibot_response,
    load_wecom_aibot_config,
    load_wecom_config,
    process_incoming_aibot_message,
    process_incoming_message,
)

logger = logging.getLogger(__name__)



router = APIRouter(prefix="/integrations/wecom", tags=["wecom"])


async def _read_callback_body(request: Request) -> bytes:
    """Read callback data incrementally and stop before an unbounded body is buffered."""
    content_length = request.headers.get("content-length")
    if content_length:
        try:
            if int(content_length) > WECOM_MAX_CALLBACK_BYTES:
                raise HTTPException(status_code=413, detail="企业微信消息体过大")
        except ValueError:
            raise HTTPException(status_code=400, detail="Content-Length 无效") from None

    body = bytearray()
    async for chunk in request.stream():
        if len(body) + len(chunk) > WECOM_MAX_CALLBACK_BYTES:
            raise HTTPException(status_code=413, detail="企业微信消息体过大")
        body.extend(chunk)
    return bytes(body)


def _callback_error(error: WeComError) -> HTTPException:
    logger.warning("wecom callback rejected: %s", error)
    if isinstance(error, WeComConfigurationError):
        return HTTPException(status_code=503, detail="企业微信回调未配置")
    return HTTPException(status_code=403, detail="企业微信回调校验失败")


@router.get("/callback", response_class=PlainTextResponse)
def verify_callback(
    msg_signature: str = Query(..., alias="msg_signature"),
    timestamp: str = Query(...),
    nonce: str = Query(...),
    echostr: str = Query(...),
) -> PlainTextResponse:
    """Validate the callback URL and return the decrypted echostr."""
    config = load_wecom_config()
    try:
        plaintext = decrypt_echo(
            config=config,
            msg_signature=msg_signature,
            timestamp=timestamp,
            nonce=nonce,
            echostr=echostr,
        )
    except WeComError as exc:
        raise _callback_error(exc) from exc
    return PlainTextResponse(plaintext)


@router.post("/callback", response_class=PlainTextResponse)
async def receive_callback(
    request: Request,
    background_tasks: BackgroundTasks,
    msg_signature: str = Query(..., alias="msg_signature"),
    timestamp: str = Query(...),
    nonce: str = Query(...),
) -> PlainTextResponse:
    """Verify/decrypt a callback, acknowledge quickly, and process it in background."""
    config = load_wecom_config()
    if not config.configured:
        raise HTTPException(status_code=503, detail="企业微信回调未配置")
    body = await _read_callback_body(request)
    try:
        message = decrypt_callback_message(
            body,
            config=config,
            msg_signature=msg_signature,
            timestamp=timestamp,
            nonce=nonce,
        )
    except WeComError as exc:
        raise _callback_error(exc) from exc
    background_tasks.add_task(process_incoming_message, message, config=config)
    return PlainTextResponse("success")


@router.get("/aibot/callback", response_class=PlainTextResponse)
def verify_aibot_callback(
    msg_signature: str = Query(..., alias="msg_signature"),
    timestamp: str = Query(...),
    nonce: str = Query(...),
    echostr: str = Query(...),
) -> PlainTextResponse:
    """Validate the intelligent bot callback URL."""
    config = load_wecom_aibot_config()
    try:
        plaintext = decrypt_aibot_echo(
            config=config,
            msg_signature=msg_signature,
            timestamp=timestamp,
            nonce=nonce,
            echostr=echostr,
        )
    except WeComError as exc:
        raise _callback_error(exc) from exc
    return PlainTextResponse(plaintext)


@router.post("/aibot/callback")
async def receive_aibot_callback(
    request: Request,
    background_tasks: BackgroundTasks,
    msg_signature: str = Query(..., alias="msg_signature"),
    timestamp: str = Query(...),
    nonce: str = Query(...),
) -> JSONResponse:
    """Decrypt an intelligent bot callback and return its passive response."""
    config = load_wecom_aibot_config()
    if not config.configured:
        raise HTTPException(status_code=503, detail="企业微信智能机器人回调未配置")
    body = await _read_callback_body(request)
    try:
        message = decrypt_aibot_message(
            body,
            config=config,
            msg_signature=msg_signature,
            timestamp=timestamp,
            nonce=nonce,
        )
        payload, should_process = aibot_callback_response(message)
    except WeComError as exc:
        raise _callback_error(exc) from exc
    if should_process:
        background_tasks.add_task(
            process_incoming_aibot_message, message, config=config
        )
    return JSONResponse(
        encrypt_aibot_response(
            payload,
            config=config,
            timestamp=timestamp,
            nonce=nonce,
        )
    )
