export function money(fen: number) {
  return new Intl.NumberFormat("zh-CN", { style: "currency", currency: "CNY" }).format(fen / 100);
}
export function dateTime(value?: string | null) {
  return value
    ? new Intl.DateTimeFormat("zh-CN", {
        timeZone: "Asia/Shanghai",
        dateStyle: "medium",
        timeStyle: "short",
        hour12: false,
      }).format(new Date(value))
    : "—";
}
export function shortId(value: string) {
  return value.slice(0, 8).toUpperCase();
}
export function attributes(value?: Record<string, string>) {
  return Object.entries(value ?? {})
    .map(([k, v]) => `${k} ${v}`)
    .join(" · ");
}
export function parseFen(value: string) {
  if (!/^\d+(\.\d{1,2})?$/.test(value)) throw new Error("金额需为非负数，最多两位小数。");
  const [whole, fraction = ""] = value.split(".");
  const result = Number(whole) * 100 + Number(fraction.padEnd(2, "0"));
  if (!Number.isSafeInteger(result) || result > 100000000) throw new Error("金额超出允许范围。");
  return result;
}
export function formText(data: FormData, name: string) {
  return String(data.get(name) ?? "").trim();
}
export function safeReturn(value: string | null, fallback = "/") {
  if (!value?.startsWith("/") || /[\u0000-\u0020\u007f\\]/.test(value)) return fallback;
  try {
    const target = new URL(value, "https://commerce.invalid");
    return target.origin === "https://commerce.invalid"
      ? `${target.pathname}${target.search}${target.hash}`
      : fallback;
  } catch {
    return fallback;
  }
}

const labels: Record<string, string> = {
  pending_payment: "待付款",
  closing: "关闭核对中",
  paid: "已付款",
  cancelled: "已取消",
  preparing: "支付入口准备中",
  pending: "处理中",
  unknown: "结果核对中",
  closed: "已关闭",
  refunded: "已全额退款",
  unfulfilled: "待拣货",
  fulfilling: "拣货中",
  shipped: "已发出",
  delivered: "已签收",
  requested: "待审核",
  approved: "待寄回",
  rejected: "未通过",
  received: "待验收",
  inspected: "已验收",
  succeeded: "已成功",
  done: "已完成",
  failed: "未完成",
  manual_review: "需人工处理",
  running: "处理中",
  open: "待处理",
  resolved: "已解决",
  provider: "核对提供方流水",
  local: "核对本地记录",
  in_transit: "运输中",
  exception: "配送异常",
};
export function statusLabel(value: string) {
  return labels[value] ?? `状态：${value}`;
}
export function eventLabel(kind: string) {
  const known: Record<string, string> = {
    "payment.pending": "支付入口已就绪",
    "payment.paid": "付款已确认",
    "payment.unknown": "支付结果核对中",
    "order.created": "订单已创建",
    "order.paid": "付款已确认",
    "order.cancel_requested": "已申请取消",
    "order.expired_closing": "订单到期，正在核对关闭结果",
    "order.closed": "订单已关闭",
    "order.picking": "开始拣货",
    "order.shipped": "订单已发货",
    "order.delivered": "订单已签收",
    "return.requested": "已提交退货申请",
    "return.approved": "退货申请已通过",
    "return.rejected": "退货申请未通过",
    "return.shipped": "退货已寄回",
    "return.received": "商家已收件",
    "return.inspected": "退货已验收",
    "refund.succeeded": "退款已确认",
    "refund.confirmed": "退款已确认",
    "shipping.delivered": "物流已签收",
    "shipping.in_transit": "物流运输中",
    "shipping.exception": "物流异常",
  };
  return known[kind] ?? "订单状态已更新";
}
