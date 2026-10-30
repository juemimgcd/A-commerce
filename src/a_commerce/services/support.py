"""客服运行协调：先保存请求，再调用独立 pi 进程；交易写入不暴露给模型。"""

import asyncio
import hashlib
import json
import secrets
from datetime import timedelta
from uuid import UUID

import httpx
import structlog
from sqlalchemy import func, select, update

from a_commerce.conf.resources import Resources
from a_commerce.conf.support import support_config
from a_commerce.errors import CommerceError, missing
from a_commerce.models.base import now
from a_commerce.models.support import SupportConversation, SupportPreference, SupportRun
from a_commerce.schemas.support import (
    AdapterReply,
    ConversationIn,
    ConversationOut,
    FeedbackIn,
    MessageIn,
    OrderTool,
    ProductTool,
    ProposeMemoryTool,
    ReturnTool,
    SearchTool,
    SupportEventIn,
    SupportRunOut,
    SupportTool,
)
from a_commerce.services import (
    aftersales,
    cart,
    catalog,
    customer_memory,
    identity,
    management,
    orders,
    shipping,
    storefront,
)
from a_commerce.services.identity import Principal, authorize

log = structlog.get_logger()
# 与 pi-adapter 的请求体上限一致，按实际发送的 UTF-8 JSON 字节数裁剪。
ADAPTER_REQUEST_MAX_BYTES = 64 * 1024


async def conversations(r: Resources, p: Principal) -> list[ConversationOut]:
    async with r.sessions() as session:
        await authorize(session, p)
        rows = await session.scalars(
            select(SupportConversation)
            .where(SupportConversation.user_id == p.id)
            .order_by(SupportConversation.created_at.desc(), SupportConversation.id)
            .limit(50)
        )
        return [ConversationOut.model_validate(row) for row in rows]


async def create_conversation(r: Resources, p: Principal, data: ConversationIn) -> ConversationOut:
    async with r.sessions.begin() as session:
        await customer_memory.lock_preferences(session, p)
        count = await session.scalar(
            select(func.count())
            .select_from(SupportConversation)
            .where(SupportConversation.user_id == p.id)
        )
        if count is not None and count >= 50:
            raise CommerceError(
                "CONVERSATION_LIMIT", "最多保留 50 个客服会话，请继续已有会话。", 409
            )
        row = SupportConversation(user_id=p.id, title=data.title)
        session.add(row)
        await session.flush()
        return ConversationOut.model_validate(row)


async def history(r: Resources, p: Principal, conversation_id: UUID) -> list[SupportRunOut]:
    async with r.sessions.begin() as session:
        await authorize(session, p)
        conversation = await session.get(SupportConversation, conversation_id)
        if conversation is None or conversation.user_id != p.id:
            raise missing()
        # 进程重启可能没有机会写失败状态；持久化截止时间保证页面不会无限等待。
        await session.execute(
            update(SupportRun)
            .where(
                SupportRun.conversation_id == conversation_id,
                SupportRun.status == "running",
                SupportRun.expires_at <= now(),
            )
            .values(status="failed", error_code="SUPPORT_INTERRUPTED", token_hash="")
        )
        rows = list(
            await session.scalars(
                select(SupportRun)
                .where(SupportRun.conversation_id == conversation_id)
                .order_by(SupportRun.created_at.desc(), SupportRun.id.desc())
                .limit(100)
            )
        )
        return [SupportRunOut.model_validate(row) for row in reversed(rows)]


async def start_run(
    r: Resources, p: Principal, conversation_id: UUID, data: MessageIn
) -> tuple[SupportRunOut, str | None]:
    config = support_config()
    async with r.sessions.begin() as session:
        preference = await customer_memory.lock_preferences(session, p)
        conversation = await session.get(SupportConversation, conversation_id)
        if conversation is None or conversation.user_id != p.id:
            raise missing()
        previous = await session.scalar(
            select(SupportRun).where(
                SupportRun.user_id == p.id,
                SupportRun.request_id == data.request_id,
            )
        )
        if previous:
            if previous.conversation_id != conversation_id or previous.user_text != data.text:
                raise CommerceError("IDEMPOTENCY_CONFLICT", "同一请求编号不能用于不同消息。", 409)
            return SupportRunOut.model_validate(previous), None
        if config.bridge_secret is None:
            raise CommerceError(
                "SUPPORT_NOT_CONFIGURED", "智能客服尚未启用，请使用购物帮助或联系商家。", 503
            )
        running = await session.scalar(
            select(SupportRun.id)
            .where(
                SupportRun.user_id == p.id,
                SupportRun.status == "running",
                SupportRun.expires_at > now(),
            )
            .limit(1)
        )
        if running:
            raise CommerceError("SUPPORT_BUSY", "上一条咨询仍在处理中，请等待回复。", 409)
        recent = await session.scalar(
            select(func.count())
            .select_from(SupportRun)
            .where(
                SupportRun.user_id == p.id,
                SupportRun.created_at >= now() - timedelta(hours=1),
            )
        )
        if recent is not None and recent >= config.hourly_messages:
            raise CommerceError("SUPPORT_RATE_LIMIT", "本小时咨询次数已达上限，请稍后再试。", 429)
        raw = secrets.token_urlsafe(32)
        run = SupportRun(
            user_id=p.id,
            session_id=p.session_id,
            conversation_id=conversation_id,
            request_id=data.request_id,
            user_text=data.text,
            token_hash=hashlib.sha256(raw.encode()).hexdigest(),
            expires_at=now() + timedelta(seconds=config.timeout_seconds + 10),
            memory_generation=preference.generation,
        )
        session.add(run)
        if conversation.title == "新的客服咨询":
            conversation.title = data.text[:60]
        await session.flush()
        return SupportRunOut.model_validate(run), raw


async def execute_run(r: Resources, p: Principal, run_id: UUID, token: str) -> None:
    """外部模型请求不占数据库事务。不自动重放已提交请求，读操作失败可由用户重试。"""
    config = support_config()
    error_code = "SUPPORT_UNAVAILABLE"
    try:
        async with asyncio.timeout(config.timeout_seconds):
            async with r.sessions() as session:
                await authorize(session, p)
                run = await session.get(SupportRun, run_id)
                if run is None or run.status != "running":
                    return
                previous = list(
                    await session.scalars(
                        select(SupportRun)
                        .where(
                            SupportRun.conversation_id == run.conversation_id,
                            SupportRun.id != run_id,
                            SupportRun.status == "completed",
                            SupportRun.memory_generation == run.memory_generation,
                        )
                        .order_by(SupportRun.created_at.desc(), SupportRun.id.desc())
                        .limit(6)
                    )
                )
                context = [
                    {"user": row.user_text, "assistant": row.assistant_text[:4000]}
                    for row in reversed(previous)
                ]
                payload = {
                    "run_id": str(run.id),
                    "user_id": str(run.user_id),
                    "conversation_id": str(run.conversation_id),
                    "memory_generation": run.memory_generation,
                    "previous_run_id": str(previous[0].id) if previous else None,
                    "token": token,
                    "message": run.user_text,
                    "history": context,
                }
            while True:
                body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode(
                    "utf-8"
                )
                if len(body) <= ADAPTER_REQUEST_MAX_BYTES:
                    break
                if not context:
                    raise CommerceError(
                        "SUPPORT_MESSAGE_TOO_LARGE", "本轮消息过大，请缩短后重试。", 422
                    )
                # 保留当前问题及最近的完整问答，不截断原文或拆开问答对。
                context.pop(0)
            if config.bridge_secret is None:
                raise CommerceError("SUPPORT_NOT_CONFIGURED", "客服尚未配置。", 503)
            # 此客户端不读取系统代理，也不跟随重定向，避免将能力 token 发往意外地址。
            async with httpx.AsyncClient(trust_env=False, follow_redirects=False) as client:
                response = await client.post(
                    f"{config.adapter_url.rstrip('/')}/run",
                    content=body,
                    headers={
                        "Authorization": f"Bearer {config.bridge_secret.get_secret_value()}",
                        "Content-Type": "application/json",
                    },
                    timeout=config.timeout_seconds,
                )
                if response.is_error:
                    # The bridge returns fixed codes only; never persist raw model errors.
                    failure = response.json()
                    code = failure.get("error") if isinstance(failure, dict) else None
                    if code in {
                        "SUPPORT_BUSY",
                        "SUPPORT_INTERRUPTED",
                        "MEMORY_CHANGED",
                        "SUPPORT_BUDGET_EXHAUSTED",
                        "SUPPORT_INCOMPLETE",
                    }:
                        raise CommerceError(code, "本轮咨询未完成，请重新提问。", 503)
                    response.raise_for_status()
                reply = AdapterReply.model_validate_json(response.content)
            async with r.sessions.begin() as session:
                preference = await customer_memory.lock_preferences(session, p)
                run = await session.scalar(
                    select(SupportRun).where(SupportRun.id == run_id).with_for_update()
                )
                if run is None or run.status != "running":
                    return
                if run.memory_generation != preference.generation:
                    raise CommerceError("MEMORY_CHANGED", "偏好设置已变化，请重新提问。", 409)
                if run.expires_at <= now():
                    raise CommerceError("SUPPORT_INTERRUPTED", "本轮咨询已超时。", 409)
                # 标题和入口只来自本轮成功查询；模型不能自造 URL 或选择历史商品。
                available = {link["href"]: link for link in run.links}
                selected = list(dict.fromkeys(f"/products/{pid}" for pid in reply.product_ids))
                if any(href not in available for href in selected):
                    raise CommerceError("SUPPORT_INVALID_PRODUCTS", "商品入口未通过核验。", 502)
                run.links = [
                    link for link in run.links if not link["href"].startswith("/products/")
                ] + [available[href] for href in selected]
                run.assistant_text = reply.text
                run.runtime = {
                    **run.runtime,
                    **reply.model_dump(mode="json", exclude={"text"}),
                    "version": "commerce-harness-v1",
                }
                run.status = "completed"
                run.token_hash = ""
            return
    except CommerceError as exc:
        error_code = exc.code
    except (Exception, asyncio.CancelledError) as exc:
        # 不记录模型请求/响应、原始异常信息或用户文本，避免泄漏凭证和个人资料。
        log.warning("support_run_failed", run_id=str(run_id), error_type=type(exc).__name__)
    async with r.sessions.begin() as session:
        generation = await session.scalar(
            select(SupportPreference.generation).where(SupportPreference.user_id == p.id)
        )
        interrupted = await session.get(SupportRun, run_id)
        if interrupted is not None and generation != interrupted.memory_generation:
            error_code = "MEMORY_CHANGED"
        await session.execute(
            update(SupportRun)
            .where(SupportRun.id == run_id, SupportRun.status == "running")
            .values(status="failed", error_code=error_code, token_hash="")
        )


async def feedback(r: Resources, p: Principal, run_id: UUID, data: FeedbackIn) -> None:
    async with r.sessions.begin() as session:
        await authorize(session, p, lock=True)
        run = await session.scalar(
            select(SupportRun)
            .where(
                SupportRun.id == run_id,
                SupportRun.user_id == p.id,
            )
            .with_for_update()
        )
        if run is None or run.status != "completed":
            raise missing()
        run.feedback = data.feedback


async def tool_principal(
    r: Resources, run_id: UUID, token: str, *, count_tool: bool = True
) -> Principal:
    if len(token) > 128:
        raise missing()
    async with r.sessions.begin() as session:
        # 先只读定位，再按账号 → 会话 → 运行的锁顺序核验。
        run = await session.get(SupportRun, run_id)
        if run is None or not secrets.compare_digest(
            run.token_hash, hashlib.sha256(token.encode()).hexdigest()
        ):
            raise CommerceError("UNAUTHENTICATED", "无效客服能力凭证。", 401)
        p = Principal(run.user_id, run.session_id, "customer")
        await authorize(session, p, lock=True)
        await session.refresh(run, with_for_update=True)
        if run.status != "running" or run.expires_at <= now():
            raise CommerceError("SUPPORT_EXPIRED", "本轮客服权限已结束。", 401)
        preference = await session.scalar(
            select(SupportPreference).where(SupportPreference.user_id == p.id)
        )
        if preference is None or preference.generation != run.memory_generation:
            raise CommerceError("MEMORY_CHANGED", "偏好设置已变化，请重新提问。", 409)
        if count_tool and run.tool_calls >= 12:
            raise CommerceError("SUPPORT_TOOL_LIMIT", "本轮查询已达上限，请根据已有结果回复。", 429)
        if count_tool:
            run.tool_calls += 1
        return p


async def run_state(r: Resources, run_id: UUID, token: str) -> dict[str, object]:
    p = await tool_principal(r, run_id, token, count_tool=False)
    async with r.sessions() as session:
        run = await session.get(SupportRun, run_id)
        if run is None:
            raise missing()
        return {
            "status": run.status,
            "memory_generation": run.memory_generation,
            "user_id": str(p.id),
            "conversation_id": str(run.conversation_id),
        }


async def record_event(r: Resources, run_id: UUID, token: str, data: SupportEventIn) -> None:
    p = await tool_principal(r, run_id, token, count_tool=False)
    async with r.sessions.begin() as session:
        await authorize(session, p, lock=True)
        run = await session.scalar(
            select(SupportRun).where(SupportRun.id == run_id).with_for_update()
        )
        if run is None or run.status != "running" or run.expires_at <= now():
            return
        events = run.runtime.get("events", [])
        if isinstance(events, list) and len(events) < 80:
            run.runtime = {**run.runtime, "events": [*events, data.model_dump(mode="json")]}


async def cancel_run(r: Resources, p: Principal, run_id: UUID) -> None:
    async with r.sessions.begin() as session:
        await customer_memory.lock_preferences(session, p)
        run = await session.scalar(
            select(SupportRun)
            .where(SupportRun.id == run_id, SupportRun.user_id == p.id)
            .with_for_update()
        )
        if run is None:
            raise missing()
        if run.status != "running":
            return
        run.status = "failed"
        run.error_code = "SUPPORT_CANCELLED"
        run.token_hash = ""
    config = support_config()
    if config.bridge_secret:
        try:
            async with httpx.AsyncClient(trust_env=False, follow_redirects=False) as client:
                await client.post(
                    f"{config.adapter_url.rstrip('/')}/runs/{run_id}/cancel",
                    headers={"Authorization": f"Bearer {config.bridge_secret.get_secret_value()}"},
                    timeout=3,
                )
        except httpx.HTTPError:
            # Backend revocation is already committed, even if the bridge is down.
            log.warning("support_cancel_bridge_unavailable", run_id=str(run_id))


async def call_tool(r: Resources, run_id: UUID, token: str, data: SupportTool) -> dict[str, object]:
    p = await tool_principal(r, run_id, token)
    result: dict[str, object]
    links: list[dict[str, str]] = []
    feature = ""
    if isinstance(data, SearchTool):
        page = await catalog.list_products(
            r,
            data.q,
            data.category,
            data.cursor,
            6,
            data.min_price,
            data.max_price,
            data.brand,
            in_stock=True,
        )
        profile = await customer_memory.get_profile(r, p)
        preferences = (
            [
                m
                for m in profile.memories
                if m.status == "confirmed" and profile.settings.memory_enabled
            ]
            if data.tool == "recommend_products"
            else []
        )
        candidates: list[dict[str, object]] = []
        scores: dict[str, int] = {}
        for product in page.items:
            skus = [
                s
                for s in product.skus
                if s.active and s.available > 0 and data.min_price <= s.price_fen <= data.max_price
            ]
            if not skus:
                continue
            reasons = [
                m.value
                for m in preferences
                if (m.kind == "brand" and m.value.casefold() == product.brand.casefold())
                or (
                    m.kind in {"category", "attribute"}
                    and m.value.casefold()
                    in (
                        product.title
                        + " "
                        + " ".join(" ".join(s.attributes.values()) for s in skus)
                    ).casefold()
                )
            ]
            scores[str(product.id)] = len(reasons)
            candidates.append(
                {
                    "id": str(product.id),
                    "title": product.title,
                    "brand": product.brand,
                    "skus": [s.model_dump(mode="json") for s in skus[:6]],
                    "matched_preferences": reasons,
                }
            )
        candidates.sort(key=lambda item: -scores[str(item["id"])])
        items = candidates[:6]
        links = [{"title": str(item["title"]), "href": f"/products/{item['id']}"} for item in items]
        result = {
            "items": items,
            "candidate_count": len(candidates),
            "more_available": page.next_cursor is not None,
            "next_cursor": str(page.next_cursor) if page.next_cursor else None,
            "note": "在本次筛选候选中排序；当前需求优先，未命中偏好时不虚构个性化理由。",
        }
        feature = "recommendations" if data.tool == "recommend_products" else "product_search"
    elif isinstance(data, ProductTool):
        product = await catalog.get_product(r, data.product_id)
        result = product.model_dump(mode="json")
        links = [{"title": product.title, "href": f"/products/{product.id}"}]
        feature = "product_details"
    elif isinstance(data, OrderTool):
        order = await orders.get_order(r, p, data.order_id)
        links = [{"title": "查看订单", "href": f"/orders/{order.id}"}]
        if data.tool == "get_shipment":
            result = (await shipping.get_shipment(r, p, order.id)).model_dump(mode="json")
            feature = "shipping"
        elif data.tool == "get_return_options":
            options = await aftersales.return_options(r, p, order.id)
            result = {
                "order_id": str(order.id),
                "return_deadline": str(order.return_deadline),
                "options": [option.model_dump(mode="json") for option in options],
                "policy": order.policy,
                "submitted": False,
                "next_action": "请用户打开售后申请页面，选择商品数量并确认提交。",
            }
            if any(option.quantity_available > 0 for option in options):
                links.append({"title": "核对并申请退货", "href": f"/orders/{order.id}/return"})
            feature = "aftersales"
        else:
            result = order.model_dump(mode="json", exclude={"address_snapshot"})
            feature = "orders"
    elif isinstance(data, ReturnTool):
        record = await aftersales.get_return(r, p, data.return_id)
        result = record.model_dump(mode="json", exclude={"return_address"})
        links = [{"title": "查看售后进度", "href": f"/returns/{record.id}"}]
        feature = "aftersales"
    elif isinstance(data, ProposeMemoryTool):
        result = await customer_memory.propose(r, p, run_id, data)
        links = [{"title": "确认或纠正偏好", "href": "/account/memory"}]
    elif data.tool == "get_categories":
        result = {"items": [row.model_dump(mode="json") for row in await catalog.categories(r)]}
    elif data.tool == "get_account":
        profile_info = await storefront.profile(r, p)
        addresses = await identity.addresses(r, p)
        result = {
            "email": profile_info.email,
            "addresses": [{"region": a.region, "phone_suffix": a.phone[-4:]} for a in addresses],
            "next_action": "完整地址与资料请用户在账户页面查看或修改。",
        }
        links = [
            {"title": "管理个人资料", "href": "/account"},
            {"title": "管理收货地址", "href": "/account/addresses"},
        ]
        feature = "account"
    elif data.tool == "get_cart":
        result = {"items": [row.model_dump(mode="json") for row in await cart.get_cart(r, p)]}
        links = [{"title": "查看购物车", "href": "/cart"}]
        feature = "cart"
    elif data.tool == "get_orders":
        page_orders = await orders.list_orders(r, p, None, 10)
        result = {
            "items": [
                row.model_dump(mode="json", exclude={"address_snapshot"})
                for row in page_orders.items
            ],
            "has_more": page_orders.next_cursor is not None,
        }
        links = [{"title": "全部订单", "href": "/orders"}]
        feature = "orders"
    elif data.tool == "get_returns":
        rows_returns = await management.returns(r, p, None, None, 10, customer=True)
        result = {
            "items": [
                row.model_dump(mode="json", exclude={"return_address"}) for row in rows_returns
            ]
        }
        links = [{"title": "全部售后申请", "href": "/returns"}]
        feature = "aftersales"
    elif data.tool == "get_memory":
        profile_memory = await customer_memory.get_profile(r, p)
        result = {
            "enabled": profile_memory.settings.memory_enabled,
            "preferences": [
                m.model_dump(mode="json", exclude={"evidence"})
                for m in profile_memory.memories
                if m.status == "confirmed"
            ]
            if profile_memory.settings.memory_enabled
            else [],
            "usage": [u.model_dump(mode="json") for u in profile_memory.usage]
            if profile_memory.settings.memory_enabled
            else [],
        }
        links = [{"title": "管理记忆与共享", "href": "/account/memory"}]
    else:
        store = storefront.store(r)
        result = {
            "return_days": store.return_days,
            "support_email": store.support_email,
            "support_phone": store.support_phone,
            "payment_mode": store.payment_mode,
            "note": "具体订单按成交时的政策快照与可退数量核验；未配置联系方式时不要编造。",
        }
        links = [{"title": "购物帮助与联系商家", "href": "/help"}]
    if feature:
        await customer_memory.record_usage(r, p, run_id, feature)
    async with r.sessions.begin() as session:
        preference = await customer_memory.lock_preferences(session, p)
        run = await session.scalar(
            select(SupportRun).where(SupportRun.id == run_id).with_for_update()
        )
        if run is None or run.status != "running" or run.expires_at <= now():
            raise CommerceError("SUPPORT_EXPIRED", "本轮客服权限已结束。", 401)
        if run.memory_generation != preference.generation:
            raise CommerceError("MEMORY_CHANGED", "偏好设置已变化，请重新提问。", 409)
        merged = {link["href"]: link for link in run.links}
        merged.update({link["href"]: link for link in links})
        # 保留整轮查询结果，最终完成时按选择核验；十二次业务工具限制总量。
        run.links = list(merged.values())
    return {"data": result, "links": links}
