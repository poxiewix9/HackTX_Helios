import type { Config } from "tailwindcss";

export default {
  content: ["./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        ops: {
          bg: "#07090d",
          panel: "#0d1117",
          edge: "#1b2230",
          text: "#d6dde8",
          dim: "#7b879a",
          accent: "#36c4ff",
          warn: "#ffb020",
          danger: "#ff3b30",
          ok: "#2fd27f",
        },
      },
      fontFamily: {
        mono: ["ui-monospace", "SFMono-Regular", "Menlo", "monospace"],
      },
      keyframes: {
        pulseRed: {
          "0%, 100%": { opacity: "1" },
          "50%": { opacity: "0.35" },
        },
      },
      animation: { pulseRed: "pulseRed 0.7s ease-in-out infinite" },
    },
  },
  plugins: [],
} satisfies Config;
