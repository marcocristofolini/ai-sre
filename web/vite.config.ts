/// <reference types="vitest" />
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

const api = process.env.AISRE_API_URL ?? "http://localhost:8000";

export default defineConfig({
  plugins: [react()],
  server: {
    // Em desenvolvimento, as rotas da API vão para o backend; a UI usa rotas por hash (#/...).
    proxy: Object.fromEntries(
      ["/auth", "/incidents", "/risk", "/scoreboard", "/integrations", "/healthz"].map((p) => [p, api]),
    ),
  },
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["./src/test/setup.ts"],
    css: false,
  },
});
