import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

// The dashboard talks to the loopback API that serves /ui in production and
// proxies to it here so the dev server on 5173 behaves the same way.
export default defineConfig({
  plugins: [react(), tailwindcss()],
  build: {
    outDir: "dist",
    emptyOutDir: true,
  },
  server: {
    port: 5173,
    proxy: {
      "/v1": "http://127.0.0.1:8765",
      "/ui": "http://127.0.0.1:8765",
    },
  },
});
