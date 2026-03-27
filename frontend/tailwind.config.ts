import type { Config } from "tailwindcss";

const config: Config = {
  content: [
    "./app/**/*.{ts,tsx}",
    "./components/**/*.{ts,tsx}",
    "./lib/**/*.{ts,tsx}",
  ],
  theme: {
    extend: {
      colors: {
        codey: {
          bg: "#0a0a0f",
          card: "#14141f",
          "card-hover": "#1a1a2e",
          border: "#2a2a3e",
          "border-light": "#3a3a4e",
          green: "#00ff88",
          "green-dim": "#00cc6a",
          "green-glow": "rgba(0, 255, 136, 0.15)",
          red: "#ff4444",
          "red-dim": "#cc3333",
          "red-glow": "rgba(255, 68, 68, 0.15)",
          yellow: "#ffcc00",
          "yellow-dim": "#ccaa00",
          "yellow-glow": "rgba(255, 204, 0, 0.15)",
          text: "#e0e0e8",
          "text-dim": "#8888a0",
          "text-muted": "#55556a",
        },
      },
      fontFamily: {
        sans: ["Inter", "system-ui", "sans-serif"],
        mono: ["JetBrains Mono", "Fira Code", "monospace"],
      },
      boxShadow: {
        "glow-green": "0 0 20px rgba(0, 255, 136, 0.2)",
        "glow-red": "0 0 20px rgba(255, 68, 68, 0.2)",
        "glow-yellow": "0 0 20px rgba(255, 204, 0, 0.2)",
      },
      animation: {
        "pulse-glow": "pulse-glow 2s ease-in-out infinite",
        "fade-in": "fade-in 0.3s ease-out",
        "slide-up": "slide-up 0.3s ease-out",
      },
      keyframes: {
        "pulse-glow": {
          "0%, 100%": { opacity: "0.6" },
          "50%": { opacity: "1" },
        },
        "fade-in": {
          from: { opacity: "0" },
          to: { opacity: "1" },
        },
        "slide-up": {
          from: { opacity: "0", transform: "translateY(8px)" },
          to: { opacity: "1", transform: "translateY(0)" },
        },
      },
    },
  },
  plugins: [],
};

export default config;
