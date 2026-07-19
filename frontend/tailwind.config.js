/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        paper: "#f8f6f2",
        ink: "#1c1917",
        "ink-light": "#44403c",
        muted: "#78716c",
        faint: "#a8a29e",
        border: "#e5e0d8",
        "border-light": "#efeae2",
        surface: "#ffffff",
        accent: {
          DEFAULT: "#1e4a72",
          light: "rgba(30, 74, 114, 0.06)",
          border: "rgba(30, 74, 114, 0.22)",
        },
        highlight: "#8a6d1f",
        positive: "#166534",
        warning: "#92400e",
        danger: "#b91c1c",
      },
      fontFamily: {
        sans: [
          "Pretendard Variable",
          "Pretendard",
          "-apple-system",
          "BlinkMacSystemFont",
          "system-ui",
          "Malgun Gothic",
          "맑은 고딕",
          "sans-serif",
        ],
        display: [
          "Bricolage Grotesque",
          "Pretendard Variable",
          "Pretendard",
          "system-ui",
          "sans-serif",
        ],
        mono: [
          "JetBrains Mono",
          "Pretendard Variable",
          "ui-monospace",
          "SFMono-Regular",
          "Consolas",
          "monospace",
        ],
      },
      borderRadius: {
        none: "0",
        DEFAULT: "0",
      },
    },
  },
  plugins: [require("@tailwindcss/typography")],
};
