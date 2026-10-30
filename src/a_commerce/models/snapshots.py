"""报价在确认前固定规格；数据库与输出共享明确的快照类型。"""

from typing import NotRequired, TypedDict


class QuoteItem(TypedDict):
    sku_id: str
    sku_version: int
    product_version: int
    quantity: int
    price_fen: int
    title: str
    # 已持久化的历史报价可能没有该字段，不能从当前商品回填成交事实。
    attributes: NotRequired[dict[str, str]]
    flash_sale_id: NotRequired[str]
    flash_sale_version: NotRequired[int]
