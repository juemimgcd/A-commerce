import { ImageResponse } from "next/og";
export const alt = "A-commerce · 规格清楚，选择简单";
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
      <div style={{ fontSize: 32, display: "flex", alignItems: "center", gap: 20 }}>
        <span
          style={{
            display: "flex",
            background: "#172033",
            color: "white",
            padding: "12px 20px",
            borderRadius: 12,
          }}
        >
          A·
        </span>
        A-commerce
      </div>
      <div style={{ fontSize: 80, lineHeight: 1.2, display: "flex", flexDirection: "column" }}>
        <span>规格清楚。</span>
        <span style={{ color: "#1d4ed8" }}>选择简单。</span>
      </div>
      <div style={{ fontSize: 24, color: "#526079" }}>数码配件 · 按需选择</div>
    </div>,
    size,
  );
}
