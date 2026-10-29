"""客服、记忆管理与画像统计入口，模型能力 token 和用户会话分开验证。"""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Body, Query, Response

from a_commerce.dependencies import Admin, Credentials, Customer, R
from a_commerce.errors import CommerceError
from a_commerce.schemas.support import (
    AudiencePage,
    ConversationIn,
    ConversationOut,
    FeedbackIn,
    InsightsOut,
    MemoryDecision,
    MemoryIn,
    MemoryOut,
    MemoryProfile,
    MessageIn,
    SupportRunOut,
    SupportSettingsIn,
    SupportTool,
)
from a_commerce.services import customer_memory, support

router = APIRouter(prefix="/api/v1", tags=["客服与用户记忆"])


@router.get("/customer/support/conversations", response_model=list[ConversationOut])
async def conversations(r: R, p: Customer) -> list[ConversationOut]:
    return await support.conversations(r, p)


@router.post("/customer/support/conversations", response_model=ConversationOut, status_code=201)
async def create_conversation(data: ConversationIn, r: R, p: Customer) -> ConversationOut:
    return await support.create_conversation(r, p, data)


@router.get(
    "/customer/support/conversations/{conversation_id}/messages", response_model=list[SupportRunOut]
)
async def messages(conversation_id: UUID, r: R, p: Customer) -> list[SupportRunOut]:
    return await support.history(r, p, conversation_id)


@router.post(
    "/customer/support/conversations/{conversation_id}/messages",
    response_model=SupportRunOut,
    status_code=202,
)
async def send_message(
    conversation_id: UUID, data: MessageIn, background: BackgroundTasks, r: R, p: Customer
) -> SupportRunOut:
    result, token = await support.start_run(r, p, conversation_id, data)
    if token:
        background.add_task(support.execute_run, r, p, result.id, token)
    return result


@router.put("/customer/support/runs/{run_id}/feedback", status_code=204)
async def feedback(run_id: UUID, data: FeedbackIn, r: R, p: Customer) -> Response:
    await support.feedback(r, p, run_id, data)
    return Response(status_code=204)


@router.get("/customer/memory", response_model=MemoryProfile)
async def memory(r: R, p: Customer) -> MemoryProfile:
    return await customer_memory.get_profile(r, p)


@router.put("/customer/memory/settings", response_model=SupportSettingsIn)
async def settings(data: SupportSettingsIn, r: R, p: Customer) -> SupportSettingsIn:
    return await customer_memory.save_settings(r, p, data)


@router.post("/customer/memory/items", response_model=MemoryOut, status_code=201)
async def add_memory(data: MemoryIn, r: R, p: Customer) -> MemoryOut:
    return await customer_memory.save_memory(r, p, data)


@router.put("/customer/memory/items/{memory_id}", response_model=MemoryOut)
async def edit_memory(memory_id: UUID, data: MemoryIn, r: R, p: Customer) -> MemoryOut:
    return await customer_memory.save_memory(r, p, data, memory_id)


@router.put("/customer/memory/items/{memory_id}/decision", response_model=MemoryOut)
async def decide_memory(memory_id: UUID, data: MemoryDecision, r: R, p: Customer) -> MemoryOut:
    return await customer_memory.decide_memory(r, p, memory_id, data)


@router.delete("/customer/memory", status_code=204)
async def clear_memory(r: R, p: Customer) -> Response:
    await customer_memory.clear_memory(r, p)
    return Response(status_code=204)


@router.get("/admin/customer-insights", response_model=InsightsOut)
async def insights(r: R, p: Admin) -> InsightsOut:
    return await customer_memory.insights(r, p)


@router.get("/admin/customer-audience", response_model=AudiencePage)
async def audience(
    r: R,
    p: Admin,
    cursor: UUID | None = None,
    limit: Annotated[int, Query(ge=1, le=30)] = 20,
    marketing_only: bool = False,
) -> AudiencePage:
    return await customer_memory.audience(r, p, cursor, limit, marketing_only)


@router.post("/agent/support/runs/{run_id}/tools")
async def tool(
    run_id: UUID, data: Annotated[SupportTool, Body()], r: R, credentials: Credentials
) -> dict[str, object]:
    if credentials is None:
        raise CommerceError("UNAUTHENTICATED", "需要本轮客服能力凭证。", 401)
    return await support.call_tool(r, run_id, credentials.credentials, data)
