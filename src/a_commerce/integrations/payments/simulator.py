"""独立模拟支付服务：SQLite 保存提供方事实，经 HTTP/签名回调与商户交互。

这是开发提供方，不是生产支付通道。关闭操作会建立墓碑，迟到创建无法重新开放付款。
"""

import asyncio
import hashlib
import hmac
import json
import sqlite3
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, closing
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Literal
from uuid import UUID, uuid4

import httpx
import structlog
from fastapi import Depends, FastAPI, Header, HTTPException, Query, Response
from pydantic import BaseModel, Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

from a_commerce.integrations.payments.client import ProviderResult
from a_commerce.integrations.payments.ledger import LedgerEntry, LedgerPage


class ProviderSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="SIMULATOR_")
    database_path: str = "var/provider.sqlite3"
    api_key: SecretStr = Field(min_length=32)
    callback_secret: SecretStr = Field(min_length=32)
    callback_url: str = "http://api:8000/api/v1/callbacks/payments/simulator"


class IntentIn(BaseModel):
    intent_id: UUID
    amount_fen: int = Field(gt=0)
    merchant_id: str
    currency: Literal["CNY"]


def initialize(path: str) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(path, timeout=3)) as db, db:
        db.execute("PRAGMA journal_mode=WAL")
        db.execute(
            "CREATE TABLE IF NOT EXISTS payments (id TEXT PRIMARY KEY, amount INTEGER NOT NULL, "
            "merchant TEXT NOT NULL, state TEXT NOT NULL)"
        )
        db.execute(
            "CREATE TABLE IF NOT EXISTS refunds (id TEXT PRIMARY KEY, intent TEXT NOT NULL, "
            "amount INTEGER NOT NULL, merchant TEXT NOT NULL)"
        )
        db.execute(
            "CREATE TABLE IF NOT EXISTS money_ledger (sequence INTEGER PRIMARY KEY AUTOINCREMENT, "
            "kind TEXT NOT NULL, resource_id TEXT NOT NULL, intent_id TEXT NOT NULL, "
            "amount INTEGER NOT NULL, merchant TEXT NOT NULL, observed_at TEXT NOT NULL, "
            "time_basis TEXT NOT NULL, UNIQUE(kind,resource_id))"
        )
        # 旧模拟器没有付款时间，只能记录迁移时观察到的事实；不伪造历史清算时间。
        observed = datetime.now(UTC).isoformat()
        db.execute(
            "INSERT OR IGNORE INTO money_ledger "
            "(kind,resource_id,intent_id,amount,merchant,observed_at,time_basis) "
            "SELECT 'payment',id,id,amount,merchant,?,'migration_observed' "
            "FROM payments WHERE state='paid'",
            (observed,),
        )
        db.execute(
            "INSERT OR IGNORE INTO money_ledger "
            "(kind,resource_id,intent_id,amount,merchant,observed_at,time_basis) "
            "SELECT 'refund',id,intent,amount,merchant,?,'migration_observed' FROM refunds",
            (observed,),
        )


def transact(
    path: str, kind: str, ident: UUID, data: IntentIn | None = None
) -> ProviderResult | None:
    with closing(sqlite3.connect(path, timeout=3)) as db, db:
        # 支付/关闭/退款串行检查后提交，避免模拟器自身制造不合法的竞态结果。
        db.execute("BEGIN IMMEDIATE")
        if kind in {"get_refund", "refund"}:
            row = db.execute(
                "SELECT intent, amount, merchant FROM refunds WHERE id=?", (str(ident),)
            ).fetchone()
            if row:
                if data and (row[0], row[1], row[2]) != (
                    str(data.intent_id),
                    data.amount_fen,
                    data.merchant_id,
                ):
                    raise HTTPException(409, "退款幂等参数冲突")
                return ProviderResult(
                    id=ident,
                    intent_id=UUID(row[0]),
                    amount_fen=row[1],
                    merchant_id=row[2],
                    currency="CNY",
                    state="succeeded",
                )
            if kind == "get_refund":
                return None
            if data is None:
                raise HTTPException(422, "缺少退款参数")
            payment = db.execute(
                "SELECT amount, merchant, state FROM payments WHERE id=?", (str(data.intent_id),)
            ).fetchone()
            used = db.execute(
                "SELECT COALESCE(SUM(amount),0) FROM refunds WHERE intent=?", (str(data.intent_id),)
            ).fetchone()[0]
            if (
                not payment
                or payment[2] != "paid"
                or payment[1] != data.merchant_id
                or used + data.amount_fen > payment[0]
            ):
                raise HTTPException(409, "退款不满足实付余额")
            db.execute(
                "INSERT INTO refunds VALUES (?,?,?,?)",
                (str(ident), str(data.intent_id), data.amount_fen, data.merchant_id),
            )
            db.execute(
                "INSERT INTO money_ledger "
                "(kind,resource_id,intent_id,amount,merchant,observed_at,time_basis) "
                "VALUES ('refund',?,?,?,?,?,'provider_observed')",
                (
                    str(ident),
                    str(data.intent_id),
                    data.amount_fen,
                    data.merchant_id,
                    datetime.now(UTC).isoformat(),
                ),
            )
            return ProviderResult(
                id=ident,
                intent_id=data.intent_id,
                amount_fen=data.amount_fen,
                merchant_id=data.merchant_id,
                currency="CNY",
                state="succeeded",
            )
        row = db.execute(
            "SELECT amount, merchant, state FROM payments WHERE id=?", (str(ident),)
        ).fetchone()
        if row is None:
            if kind in {"get", "pay"}:
                return None
            if data is None or data.intent_id != ident:
                raise HTTPException(422, "缺少支付参数")
            state = "closed" if kind == "close" else "pending"
            db.execute(
                "INSERT INTO payments VALUES (?,?,?,?)",
                (str(ident), data.amount_fen, data.merchant_id, state),
            )
            row = (data.amount_fen, data.merchant_id, state)
        elif data and (
            data.intent_id != ident or data.amount_fen != row[0] or data.merchant_id != row[1]
        ):
            raise HTTPException(409, "支付幂等参数冲突")
        amount, merchant, state = row
        if kind == "pay":
            if state == "closed":
                raise HTTPException(409, "支付会话已经关闭")
            state = "paid"
        elif kind == "close" and state != "paid":
            state = "closed"
        db.execute("UPDATE payments SET state=? WHERE id=?", (state, str(ident)))
        if state == "paid":
            db.execute(
                "INSERT OR IGNORE INTO money_ledger "
                "(kind,resource_id,intent_id,amount,merchant,observed_at,time_basis) "
                "VALUES ('payment',?,?,?,?,?,'provider_observed')",
                (str(ident), str(ident), amount, merchant, datetime.now(UTC).isoformat()),
            )
        return ProviderResult.model_validate(
            {
                "id": ident,
                "intent_id": ident,
                "amount_fen": amount,
                "merchant_id": merchant,
                "currency": "CNY",
                "state": state,
            }
        )


def ledger_page(path: str, after: int, through: int | None, limit: int) -> LedgerPage:
    with closing(sqlite3.connect(path, timeout=3)) as db, db:
        db.execute("BEGIN")
        maximum = int(
            db.execute("SELECT COALESCE(MAX(sequence),0) FROM money_ledger").fetchone()[0]
        )
        upper = maximum if through is None else through
        if upper > maximum or after > upper:
            raise HTTPException(409, "资金流水快照上界无效，请重新发起核对")
        rows = db.execute(
            "SELECT sequence,kind,resource_id,intent_id,amount,merchant,observed_at,time_basis "
            "FROM money_ledger WHERE sequence>? AND sequence<=? ORDER BY sequence LIMIT ?",
            (after, upper, limit + 1),
        ).fetchall()
        items = [
            LedgerEntry(
                sequence=row[0],
                kind=row[1],
                id=row[2],
                intent_id=row[3],
                amount_fen=row[4],
                merchant_id=row[5],
                observed_at=row[6],
                time_basis=row[7],
                currency="CNY",
                state="paid" if row[1] == "payment" else "succeeded",
            )
            for row in rows[:limit]
        ]
        complete = len(rows) <= limit
        return LedgerPage(
            through_sequence=upper,
            next_after=upper if complete else items[-1].sequence,
            complete=complete,
            items=items,
        )


def create_app() -> FastAPI:
    settings = ProviderSettings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        await asyncio.to_thread(initialize, settings.database_path)
        yield

    async def authenticate(authorization: Annotated[str, Header()] = "") -> None:
        if not hmac.compare_digest(authorization, f"Bearer {settings.api_key.get_secret_value()}"):
            raise HTTPException(401, "无效提供方凭证")

    app = FastAPI(
        title="A-commerce 独立模拟支付提供方",
        lifespan=lifespan,
        dependencies=[Depends(authenticate)],
    )

    @app.get("/ledger", response_model=LedgerPage)
    async def ledger(
        after: Annotated[int, Query(ge=0)] = 0,
        through: Annotated[int | None, Query(ge=0)] = None,
        limit: Annotated[int, Query(ge=1, le=100)] = 100,
    ) -> LedgerPage:
        return await asyncio.to_thread(ledger_page, settings.database_path, after, through, limit)

    @app.get("/payments/{ident}", response_model=ProviderResult)
    async def get_payment(ident: UUID) -> ProviderResult:
        result = await asyncio.to_thread(transact, settings.database_path, "get", ident)
        if result is None:
            raise HTTPException(404)
        return result

    @app.put("/payments/{ident}", response_model=ProviderResult)
    async def create_payment(ident: UUID, data: IntentIn) -> ProviderResult | None:
        return await asyncio.to_thread(transact, settings.database_path, "create", ident, data)

    @app.post("/payments/{ident}/close", response_model=ProviderResult)
    async def close_payment(ident: UUID, data: IntentIn) -> ProviderResult | None:
        return await asyncio.to_thread(transact, settings.database_path, "close", ident, data)

    @app.post("/payments/{ident}/pay", response_model=ProviderResult)
    async def pay(ident: UUID, response: Response) -> ProviderResult:
        result = await asyncio.to_thread(transact, settings.database_path, "pay", ident)
        if result is None:
            raise HTTPException(409, "支付会话尚未创建")
        payload = {
            "event_id": str(uuid4()),
            "intent_id": str(ident),
            "merchant_id": result.merchant_id,
            "amount_fen": result.amount_fen,
            "currency": "CNY",
            "state": "paid",
        }
        body = json.dumps(payload, separators=(",", ":")).encode()
        timestamp = str(int(time.time()))
        signature = hmac.new(
            settings.callback_secret.get_secret_value().encode(),
            timestamp.encode() + b"." + body,
            hashlib.sha256,
        ).hexdigest()
        try:
            async with httpx.AsyncClient(timeout=5) as client:
                callback = await client.post(
                    settings.callback_url,
                    content=body,
                    headers={
                        "Content-Type": "application/json",
                        "X-Timestamp": timestamp,
                        "X-Signature": signature,
                    },
                )
                callback.raise_for_status()
        except httpx.HTTPError:
            # 已付款事实不能因商户回调失败而回滚；商户 worker 后续主动查询即可恢复。
            structlog.get_logger().warning("simulator_callback_failed", intent_id=str(ident))
            response.headers["X-Callback-Delivered"] = "false"
        return result

    @app.get("/refunds/{ident}", response_model=ProviderResult)
    async def get_refund(ident: UUID) -> ProviderResult:
        result = await asyncio.to_thread(transact, settings.database_path, "get_refund", ident)
        if result is None:
            raise HTTPException(404)
        return result

    @app.put("/refunds/{ident}", response_model=ProviderResult)
    async def refund(ident: UUID, data: IntentIn) -> ProviderResult | None:
        return await asyncio.to_thread(transact, settings.database_path, "refund", ident, data)

    return app
