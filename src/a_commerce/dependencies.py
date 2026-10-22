"""HTTP 依赖只负责解析身份；写事务内仍需重新核验会话与权限。"""

import hashlib
from collections.abc import Awaitable
from typing import Annotated

from fastapi import Depends, Header, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from redis.exceptions import RedisError

from a_commerce.conf.resources import Resources
from a_commerce.errors import CommerceError
from a_commerce.services.identity import Principal, resolve

bearer = HTTPBearer(auto_error=False)


def get_resources(request: Request) -> Resources:
    result: Resources = request.app.state.resources
    return result


R = Annotated[Resources, Depends(get_resources)]
Credentials = Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)]


async def customer(r: R, credentials: Credentials) -> Principal:
    if credentials is None:
        raise CommerceError("UNAUTHENTICATED", "需要 Bearer 会话。", 401)
    return await resolve(r, credentials.credentials, "customer")


async def staff(r: R, credentials: Credentials) -> Principal:
    if credentials is None:
        raise CommerceError("UNAUTHENTICATED", "需要管理员会话。", 401)
    return await resolve(r, credentials.credentials, "staff")


Customer = Annotated[Principal, Depends(customer)]
Admin = Annotated[Principal, Depends(staff)]
Key = Annotated[
    str, Header(alias="Idempotency-Key", min_length=8, max_length=96, pattern=r"^[A-Za-z0-9._:-]+$")
]


async def sensitive_limit(request: Request, r: R) -> None:
    # 不信任任意 X-Forwarded-For；部署时由受信网关/Uvicorn 配置真实客户端地址。
    host = request.client.host if request.client else "unknown"
    digest = hashlib.sha256(host.encode()).hexdigest()
    script = (
        "local n=redis.call('INCR',KEYS[1]); "
        "if n==1 then redis.call('EXPIRE',KEYS[1],60) end; return n"
    )
    try:
        result = r.redis.eval(script, 1, f"rate:auth:{digest}")
        count = await result if isinstance(result, Awaitable) else result
    except RedisError as exc:
        raise CommerceError("RATE_LIMIT_UNAVAILABLE", "登录保护暂时不可用。", 503, True) from exc
    if int(count) > 20:
        raise CommerceError("RATE_LIMITED", "操作过于频繁，请稍后重试。", 429, True)
