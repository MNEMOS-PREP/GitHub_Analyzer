import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

/**
 * M03 - GitHub Analyzer, on its reserved port 5203 (PORTS.txt).
 *
 * This repo holds one module, so this is the app. The platform shell mounts
 * the same `src/routes.tsx`, so what you fix here is what students get.
 */
const API_TARGET = process.env.VITE_API_BASE ?? "http://127.0.0.1:8103";

export default defineConfig({
  plugins: [react(), tailwindcss()],
  optimizeDeps: { exclude: ["@ai/core"] },
  server: {
    port: 5203,
    proxy: { "/v1": { target: API_TARGET, changeOrigin: true } },
  },
  preview: { port: 5203 },
});
