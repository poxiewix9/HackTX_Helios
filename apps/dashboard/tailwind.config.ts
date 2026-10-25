import type { Config } from "tailwindcss";

export default {
  content: ["./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        ops: {
          bg: "#080b10",
          panel: "#121820",
          edge: "#243041",
          text: "#e7edf5",
          dim: "#8b97a8",
          accent: "#36c4ff",
          warn: "#ffb020",
          danger: "#ff5d52",
          ok: "#3ddc84",
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
