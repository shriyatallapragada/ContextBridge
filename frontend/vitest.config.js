import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";

// Separate from vite.config.js on purpose: tests run in jsdom without the
// Tailwind plugin or the /ui/ base path, and never touch the network.
export default defineConfig({
  plugins: [react()],
  test: {
    environment: "jsdom",
    include: ["src/**/*.test.jsx", "tests/**/*.test.jsx"],
    setupFiles: ["./src/test-setup.js"],
    css: false,
    restoreMocks: true,
  },
});
