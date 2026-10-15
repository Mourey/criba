/// <reference types="vitest/config" />
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// In development, proxy the backend paths the same way nginx does in Compose.
const backend = process.env.CRIBA_BACKEND_URL ?? "http://localhost:8000";

export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      "/api": backend,
    },
  },
  test: {
    environment: "jsdom",
    globals: false,
    setupFiles: ["./tests/setup.ts", "./tests/warm-lazy-routes.tsx"],
    include: ["src/**/*.test.{ts,tsx}", "tests/unit/**/*.test.{ts,tsx}"],
    // Used by `vitest run --coverage` (src/scripts/check.sh); Cobertura feeds diff-cover.
    coverage: {
      provider: "v8",
      include: ["src/**/*.{ts,tsx}"],
      exclude: ["src/**/*.test.{ts,tsx}"],
      reporter: ["text-summary", "cobertura"],
      reportsDirectory: "coverage",
    },
  },
});
