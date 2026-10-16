import react from "@vitejs/plugin-react";
import { defineConfig, loadEnv } from "vite";

// In dev the API is proxied so the browser sees one origin (production uses nginx for the same job).
export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), "");
  return {
    plugins: [react()],
    server: { port: 5173, proxy: { "/api": env.VITE_API_PROXY ?? "http://localhost:8000" } },
  };
});

