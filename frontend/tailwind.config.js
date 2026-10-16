/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      fontFamily: {
        sans: ['"IBM Plex Sans"', "ui-sans-serif", "system-ui", "-apple-system", "Segoe UI", "sans-serif"],
        mono: ['"IBM Plex Mono"', "ui-monospace", "SFMono-Regular", "Menlo", "monospace"],
      },
      colors: {
        canvas: "#F4F5F7",
        line: "#E2E6EC",
        ink: { DEFAULT: "#1A2332", soft: "#4A5568", faint: "#7A8699" },
        navy: { DEFAULT: "#14213A", hover: "#1D2F52", line: "#2A3C61" },
        brand: { DEFAULT: "#0F7A66", soft: "#E3F4EF", strong: "#0B5E4F" },
        st: {
          pending: "#9A6700", "pending-bg": "#FFF4D6",
          processing: "#0B5CAD", "processing-bg": "#E1EEFB",
          completed: "#0B6B3A", "completed-bg": "#DDF3E4",
          failed: "#A4282B", "failed-bg": "#FBE4E4",
        },
      },
    },
  },
  plugins: [],
};
