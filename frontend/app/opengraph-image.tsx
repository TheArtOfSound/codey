import { ImageResponse } from "next/og";

export const alt = "Codey — The autonomous repo operator";
export const size = { width: 1200, height: 630 };
export const contentType = "image/png";

export default function Image() {
  return new ImageResponse(
    (
      <div
        style={{
          height: "100%",
          width: "100%",
          display: "flex",
          flexDirection: "column",
          justifyContent: "center",
          padding: "80px",
          backgroundColor: "#050a12",
          color: "#f1f5f9",
          fontFamily: "sans-serif",
        }}
      >
        <div style={{ fontSize: 44, fontWeight: 700, color: "#22c55e", letterSpacing: 4 }}>
          CODEY
        </div>
        <div style={{ fontSize: 72, fontWeight: 800, lineHeight: 1.05, marginTop: 24, maxWidth: 1000 }}>
          The autonomous repo operator
        </div>
        <div style={{ fontSize: 32, color: "#94a3b8", marginTop: 32, maxWidth: 1000 }}>
          Writes patches, verifies the diff, and proves what actually changed.
        </div>
        <div style={{ fontSize: 26, color: "#475569", marginTop: 48 }}>
          codey.imagineqira.com
        </div>
      </div>
    ),
    size,
  );
}
