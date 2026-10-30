"""HTTP 依赖只负责解析身份；写事务内仍需重新核验会话与权限。"""

from typing import Annotated

from fastapi import Depends, Header, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from a_commerce.conf.resources import Resources
from a_commerce.errors import CommerceError
from a_commerce.services.identity import Principal, resolve
from a_commerce.traffic import Limit, enforce

bearer = HTTPBearer(auto_error=False)


async def get_resources(request: Request) -> Resources:
    # 仅访问进程内对象；同步依赖会让每个请求无谓地往返线程池。
    result: Resources = request.app.state.resources
    return result


R = Annotated[Resources, Depends(get_resources)]
Credentials = Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)]


async def customer(request: Request, r: R, credentials: Credentials) -> Principal:
    if credentials is None:
        raise CommerceError("UNAUTHENTICATED", "需要 Bearer 会话。", 401)
    principal = await resolve(r, credentials.credentials, "customer")
    await principal_limit(request, r, principal)
    return principal


async def staff(request: Request, r: R, credentials: Credentials) -> Principal:
    if credentials is None:
        raise CommerceError("UNAUTHENTICATED", "需要管理员会话。", 401)
    principal = await resolve(r, credentials.credentials, "staff")
    await principal_limit(request, r, principal)
    return principal


async def principal_limit(request: Request, r: R, principal: Principal) -> None:
    # 主体来自服务端会话，不使用客户端自报的 user_id 或随意切换的 token 作为额度键。
    # 所有普通读/写分别共用用户预算；报价和下单再有独立额度，查询订单不被写额度挤占。
    subject = f"{principal.kind}:{principal.id}"
    read = request.method in {"GET", "HEAD", "OPTIONS"}
    policy = "user_read" if read else "user_write"
    limits = [Limit(policy, subject, getattr(r.settings.traffic, policy))]
    route = getattr(request.scope.get("route"), "path", "")
    if request.method == "POST" and route == "/api/v1/customer/quotes":
        limits.append(Limit("quote_user", subject, r.settings.traffic.quote_user))
    if request.method == "POST" and route == "/api/v1/customer/orders":
        limits.append(Limit("order_user", subject, r.settings.traffic.order_user))
    await enforce(r, limits, fail_open=read)


Customer = Annotated[Principal, Depends(customer)]
Admin = Annotated[Principal, Depends(staff)]
Key = Annotated[
    str, Header(alias="Idempotency-Key", min_length=8, max_length=96, pattern=r"^[A-Za-z0-9._:-]+$")
]


async def sensitive_limit(request: Request, r: R) -> None:
    # 不信任任意 X-Forwarded-For；部署时由受信网关/Uvicorn 配置真实客户端地址。
    host = request.client.host if request.client else "unknown"
    await enforce(r, [Limit("auth_ip", host, r.settings.traffic.auth_ip)])


async def entry_limit(request: Request, r: R) -> None:
    if getattr(request.state, "catalog_entry_limited", False):
        return
    # 网关先做本地粗限流，这里补上跨 API 实例共享的 IP 额度，并在鉴权查库之前执行。
    host = request.client.host if request.client else "unknown"
    await enforce(
        r,
        [Limit("entry_ip", host, r.settings.traffic.entry_ip)],
        fail_open=request.method in {"GET", "HEAD", "OPTIONS"},
    )
