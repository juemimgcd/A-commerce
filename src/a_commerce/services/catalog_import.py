"""受限 JSON 商品导入；每行独立事务，错误只影响该行，不留下半个商品。"""

from uuid import UUID

from pydantic import Field, ValidationError
from sqlalchemy.exc import IntegrityError

from a_commerce.conf.resources import Resources
from a_commerce.errors import CommerceError, missing
from a_commerce.models.entities import SKU, Audit, Category, Product, Stock
from a_commerce.schemas.api import ProductIn, Schema, SKUIn
from a_commerce.services.catalog import record_change
from a_commerce.services.identity import Principal, authorize


class ImportLine(Schema):
    product: ProductIn
    skus: list[SKUIn] = Field(min_length=1, max_length=20)


class ImportIn(Schema):
    rows: list[dict[str, object]] = Field(min_length=1, max_length=100)


class ImportResult(Schema):
    row: int
    product_id: UUID | None = None
    error: str | None = None


async def import_products(r: Resources, p: Principal, data: ImportIn) -> list[ImportResult]:
    async with r.sessions() as session:
        await authorize(session, p, "catalog")
    results: list[ImportResult] = []
    for index, raw in enumerate(data.rows, start=1):
        try:
            line = ImportLine.model_validate(raw)
            async with r.sessions.begin() as session:
                await authorize(session, p, "catalog", lock=True)
                if await session.get(Category, line.product.category_id) is None:
                    raise missing()
                product = Product(**line.product.model_dump())
                session.add(product)
                await session.flush()
                for sku_data in line.skus:
                    sku = SKU(product_id=product.id, **sku_data.model_dump())
                    session.add(sku)
                    await session.flush()
                    session.add(Stock(sku_id=sku.id))
                await session.flush()
                await record_change(session, product)
                session.add(
                    Audit(
                        actor_id=p.id,
                        action="catalog.import",
                        resource_id=product.id,
                        detail=f"商品导入第 {index} 行",
                    )
                )
                product_id = product.id
            results.append(ImportResult(row=index, product_id=product_id))
        except ValidationError:
            results.append(ImportResult(row=index, error="INVALID_ROW"))
        except IntegrityError:
            results.append(ImportResult(row=index, error="DUPLICATE_SKU_OR_CONSTRAINT"))
        except CommerceError as exc:
            if exc.status == 401:
                raise
            results.append(ImportResult(row=index, error=exc.code))
    return results
