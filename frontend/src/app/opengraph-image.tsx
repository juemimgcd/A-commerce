import { ImageResponse } from "next/og";
export const alt = "商品选购 · 规格清楚，选择简单";
export const size = { width: 1200, height: 630 };
export const contentType = "image/png";
export default function OpenGraphImage() {
  return new ImageResponse(
    <div
      style={{
        display: "flex",
        flexDirection: "column",
        justifyContent: "space-between",
        width: "100%",
        height: "100%",
        padding: 80,
        background: "#f7f8fa",
        color: "#172033",
        fontFamily: "sans-serif",
      }}
    >
      <div style={{ fontSize: 32, display: "flex", alignItems: "center", gap: 20 }}>商品选购</div>
      <div style={{ fontSize: 80, lineHeight: 1.2, display: "flex", flexDirection: "column" }}>
        <span>规格清楚。</span>
        <span style={{ color: "#ff5000" }}>选择简单。</span>
      </div>
      <div style={{ fontSize: 24, color: "#526079" }}>护肤 · 家电 · 日常用品</div>
    </div>,
    size,
  );
}
