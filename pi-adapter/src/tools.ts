import type { ToolDefinition } from "@earendil-works/pi-coding-agent";
import { Type, type TSchema } from "typebox";
import { Check } from "typebox/value";
import { uuid } from "./contracts.ts";
import type { BackendClient } from "./backend-client.ts";

export function commerceTools(client: BackendClient): ToolDefinition[] {
  function tool(
    name: string,
    description: string,
    parameters: TSchema,
  ): ToolDefinition {
    return {
      name: `commerce_${name}`,
      label: description,
      description,
      parameters,
      executionMode: "sequential",
      async execute(id, params, signal) {
        if (
          !Check(parameters, params) ||
          !params ||
          typeof params !== "object" ||
          Array.isArray(params)
        )
          throw new Error("无效工具参数。");
        const result = await client.call(name, params, id, signal);
        return {
          content: [{ type: "text", text: JSON.stringify(result) }],
          details: {},
        };
      },
    };
  }
  const empty = Type.Object({}, { additionalProperties: false });
  if (client.request.shop_id) {
    const period = Type.Object({
      start: Type.String({ format: "date" }),
      end: Type.String({ format: "date" }),
      product_id: Type.Optional(uuid),
    }, { additionalProperties: false });
    const page = Type.Object({
      cursor: Type.Optional(Type.String({ maxLength: 200 })),
      limit: Type.Optional(Type.Integer({ minimum: 1, maximum: 20 })),
    }, { additionalProperties: false });
    return [
      tool("get_shop_sales_summary", "查询当前店铺期间收款与退款，以及该期间支付订单截至当前的经营表现。结束日期不包含在区间内，最多 366 天。", period),
      tool("get_product_sales", "按 product_id 查询本店商品销量、退款、成本覆盖与可计算毛利，金额为分。", period),
      tool("get_shop_customer_statistics", "查询本店聚合购买人数、新老客、收货地区和消费分布，不提供客户联系方式。", period),
      tool("list_shop_orders", "分页读取本店脱敏订单摘要，不含姓名、电话、地址或买家标识。", page),
      tool("list_shop_products", "分页读取本店商品、售价、当前成本、成本版本和库存。", page),
    ];
  }
  const search = Type.Object(
    {
      q: Type.Optional(Type.String({ maxLength: 80 })),
      category: Type.Optional(uuid),
      brand: Type.Optional(Type.String({ maxLength: 100 })),
      cursor: Type.Optional(uuid),
      min_price: Type.Optional(
        Type.Integer({ minimum: 0, maximum: 100000000 }),
      ),
      max_price: Type.Optional(
        Type.Integer({ minimum: 0, maximum: 100000000 }),
      ),
    },
    { additionalProperties: false },
  );
  return [
    tool(
      "search_products",
      "按品类、关键词、品牌和价格范围查有货商品，价格单位为分。每页最多六件，使用返回的 next_cursor 翻页。",
      search,
    ),
    tool(
      "recommend_products",
      "在当前页符合条件的有货商品中结合已确认偏好排序，返回实际命中的偏好。使用 next_cursor 翻页查看更多候选。",
      search,
    ),
    tool(
      "get_product",
      "查询某件商品的规格、当前价格和库存。",
      Type.Object({ product_id: uuid }, { additionalProperties: false }),
    ),
    ...[
      ["get_order", "查询当前用户订单详情和付款状态。"],
      ["get_shipment", "查询当前用户订单物流。"],
      [
        "get_return_options",
        "核对当前用户订单的退货期限和可退数量，生成可信申请入口，不提交退货。",
      ],
    ].map(([name, description]) =>
      tool(
        name,
        description,
        Type.Object({ order_id: uuid }, { additionalProperties: false }),
      ),
    ),
    tool(
      "get_return",
      "查询当前用户售后申请与退款进度。",
      Type.Object({ return_id: uuid }, { additionalProperties: false }),
    ),
    ...[
      ["get_categories", "查询商品分类及分类 ID。"],
      ["get_account", "查询当前用户邮箱和脱敏地址摘要。"],
      ["get_cart", "查询当前用户购物车。"],
      ["get_orders", "查询当前用户最近十个订单。"],
      ["get_returns", "查询当前用户售后申请。"],
      ["get_policy", "查询商家售后政策和已配置的联系方式。"],
      ["get_memory", "查询已确认偏好与记忆开关。推荐或总结偏好前先调用。"],
    ].map(([name, description]) => tool(name, description, empty)),
    tool(
      "propose_memory",
      "根据本轮用户原文提出一条长期偏好候选；不会自动确认。",
      Type.Object(
        {
          kind: Type.Union(
            ["category", "brand", "budget", "attribute", "service"].map(
              (value) => Type.Literal(value),
            ),
          ),
          value: Type.String({ minLength: 1, maxLength: 120 }),
          evidence: Type.String({ minLength: 2, maxLength: 240 }),
        },
        { additionalProperties: false },
      ),
    ),
  ];
}
