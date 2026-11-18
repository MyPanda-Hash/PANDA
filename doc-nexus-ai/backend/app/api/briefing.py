"""主动汇报的读取入口：语音悬浮窗、MES 通知都能从这里拿。

只读，按当前登录账号的 MES 权限取数；不做写操作。
"""

from __future__ import annotations

from datetime import date
from urllib.parse import quote

from fastapi import APIRouter, Cookie, Depends, Header, HTTPException, Query, Response
from sqlalchemy import select

from app.api.deps import current_user_from_bearer_or_cookie
from app.core.database import transaction
from app.models.auth import User
from app.models.inapp_notification import InappNotification
from app.services import brief_board as brief_board_service
from app.services import dashboard_analysis as dashboard_analysis_service
from app.services import floor_board as floor_board_service
from app.services import period_reports, report_deck
from app.services import view_as as view_as_service
from app.services import my_board as my_board_service
from app.services import production_briefing

router = APIRouter(prefix="/assistant/briefing", tags=["assistant-briefing"])


@router.get("")
def latest_briefing(user: User = Depends(current_user_from_bearer_or_cookie)) -> dict:
    """当前账号的生产概况（按他自己的 MES 权限）。"""
    briefing = production_briefing.for_user(user.id)
    return {
        "data": {
            "text": briefing.speak(),
            "ok": briefing.ok,
            "note": briefing.note,
            "lines": briefing.lines,
            "items": briefing.items,
            "skipped": briefing.skipped,
            "generatedAt": date.today().isoformat(),
        }
    }


@router.get("/board")
def brief_board(
    user: User = Depends(current_user_from_bearer_or_cookie),
    role: str = Query(default="manager", max_length=16),
    period: str = Query(default="", max_length=7),
    view_as: str = Query(default="", max_length=128),
    x_view_as: str = Header(default="", alias="X-View-As"),
    x_factory_code: str = Header(default="", alias="X-Factory-Code"),
    factory_cookie: str = Cookie(default="", alias="doc_nexus_factory"),
) -> dict:
    """简报看板：结论 + 四个数字 + 三条待拍板。

    角色决定展示口径；view_as 只给管理员，用另一个账号的身份取数，
    这样「切换视角」才真的换了数据，而不只是换标签。
    """
    factory = (x_factory_code or factory_cookie or "").strip().upper()
    with transaction() as db:
        try:
            viewer = view_as_service.resolve(db, user, view_as or x_view_as)
        except view_as_service.ViewAsDenied as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        return {
            "data": brief_board_service.for_user(
                db, user_id=viewer.id, factory_code=factory, role=role, period=period
            )
        }


@router.get("/floor")
def floor_board(user: User = Depends(current_user_from_bearer_or_cookie)) -> dict:
    """车间现场看板：在制工单进度、今日报工、停滞与逾期。"""
    return {"data": floor_board_service.for_user(user.id)}


@router.get("/mine")
def my_board(user: User = Depends(current_user_from_bearer_or_cookie)) -> dict:
    """一线员工看板：待报工工序 + 我的产出。"""
    # AI 侧用户只有 display_name（中文名），没有 real_name；
    # 这里用它去匹配报工单上的「工人名称」，匹配不到会明确标注「未登记工人」。
    return {"data": my_board_service.for_user(user.id, real_name=user.display_name or user.username)}


@router.get("/reports/{kind}/pptx")
def report_pptx(
    kind: str,
    user: User = Depends(current_user_from_bearer_or_cookie),
    session_id: str = Query(default="", max_length=128),
) -> Response:
    """把报表导成 PPTX：企业里日报/周报是拿去过会的，纯文本不够用。

    session_id 传了就读「对话里那份」正文，把模型追加的「分析」「建议」也带上；
    不传就只用系统口径的数字（render_report）。
    """
    if kind not in ("daily", "weekly", "monthly"):
        raise HTTPException(status_code=422, detail="不支持的报表类型：" + kind)

    with transaction() as db:
        try:
            data, title = report_deck.build_pptx(db, kind=kind, session_id=session_id)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    filename = report_deck.filename_of(kind, title)
    # 中文文件名要按 RFC 5987 编码，否则浏览器拿到的是一串乱码
    quoted = quote(filename)
    return Response(
        content=data,
        media_type=("application/vnd.openxmlformats-officedocument"
                    ".presentationml.presentation"),
        headers={
            "Content-Disposition": "attachment; filename*=UTF-8''" + quoted,
            "Cache-Control": "no-store",
        },
    )

@router.post("/reports/{kind}")
async def generate_period_report(
    kind: str,
    user: User = Depends(current_user_from_bearer_or_cookie),
    anchor: str = Query(default="", max_length=10),
) -> dict:
    """生成一份周期报表（daily / weekly / monthly），并投递到当前用户的对话。

    与对话里叫 AI「生成周报」走的是同一条服务路径，避免两套口径。
    """
    parsed = None
    if anchor:
        try:
            parsed = date.fromisoformat(anchor)
        except ValueError:
            raise HTTPException(status_code=422, detail="anchor 需要 yyyy-MM-dd") from None
    with transaction() as db:
        try:
            result = await period_reports.generate(db, user=user, kind=kind, anchor=parsed)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from None
    return {"data": result}


@router.post("/analysis")
async def dashboard_analysis(payload: dict, user: User = Depends(current_user_from_bearer_or_cookie)) -> dict:
    """数据看板的 AI 分析：入参是看板上的指标快照，出参是增长点与建议。"""
    with transaction() as db:
        result = await dashboard_analysis_service.analyze(db, payload.get("metrics") or {})
    return {"data": result}


@router.get("/notifications")
def briefing_notifications(
    limit: int = 5,
    user: User = Depends(current_user_from_bearer_or_cookie),
) -> dict:
    """最近的主动汇报（早间推送写进通知表的那几条），用于「昨天/今早跟我说了什么」。"""
    with transaction() as db:
        rows = list(
            db.scalars(
                select(InappNotification)
                .where(InappNotification.user_id == user.id)
                .where(InappNotification.kind == "notice")
                .order_by(InappNotification.created_at.desc())
                .limit(max(1, min(limit, 20)))
            )
        )
        items = [
            {
                "id": row.id,
                "title": row.title,
                "body": row.body,
                "createdAt": row.created_at.isoformat() if row.created_at else "",
                "read": bool(row.read_at),
            }
            for row in rows
        ]
    return {"data": {"items": items}}
