import type { components } from "./api.generated";

type Schema = components["schemas"];
export type Product = Schema["ProductOut"];
export type ProductPage = Schema["ProductPage"];
export type Sku = Schema["SKUOut"];
export type Category = Schema["CategoryOut"];
export type Media = Schema["MediaOut"];
export type Profile = Schema["ProfileOut"];
export type Store = Schema["StoreOut"];
export type Address = Schema["AddressOut"];
export type CartItem = Schema["CartOut"];
export type Quote = Schema["QuoteOut"];
export type Order = Schema["OrderOut"];
export type OrderPage = Schema["OrderPage"];
export type Return = Schema["ReturnOut"];
export type ReturnOption = Schema["ReturnOption"];
export type Refund = Schema["RefundOut"];
export type Shipment = Schema["ShipmentDetail"];
export type History = Schema["HistoryOut"];
export type Session = Schema["SessionOut"];
export type Notification = Schema["NotificationOut"];
export type Preferences = Schema["PreferenceIn"];
export type Inventory = Schema["InventoryOut"];
export type InventoryPage = Schema["InventoryPage"];
export type Movement = Schema["MovementOut"];
export type Change = Schema["ChangeOut"];
export type Staff = Schema["StaffOut"];
export type ShippingRule = Schema["ShippingRuleOut"];
export type ShippingRulePage = Schema["ShippingRulePage"];
export type DailyReport = Schema["DailyOut"];
export type Ranking = Schema["RankingOut"];
export type ExportJob = Schema["ExportOut"];
export type Audit = Schema["AuditOut"];
export type Operation = Schema["OperationOut"];
export type Failure = Schema["FailureOut"];
export type Reconciliation = Schema["RunOut"];
export type ReconciliationCase = Schema["CaseOut"];
export type ImportResult = Schema["ImportResult"];
export type Snapshot = Schema["SnapshotOut"];
export type SnapshotPage = Schema["SnapshotPage"];
export type Scope = "customer" | "admin";

export const permissions = {
  catalog: "商品与库存",
  fulfillment: "订单履约",
  aftersales: "售后与退款",
  audit: "报表与审计",
  identity: "员工管理",
} as const;
