import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

// The dashboard talks to the loopback API that serves /ui in production and
// proxies to it here so the dev server on 5173 behaves the same way.
//
// `base` MUST be "/ui/": production mounts the built shell at /ui/, so assets
// have to resolve to /ui/assets/... or every request 401s/404s (blank page).
// The dev server then serves the app at http://localhost:5173/ui/ — API calls
// stay origin-absolute ("/v1/...") so they hit the proxy, not the base path.
export default defineConfig({
  base: "/ui/",
  plugins: [react(), tailwindcss()],
  build: {
    outDir: "dist",
    emptyOutDir: true,
  },
  server: {
    port: 5173,
    proxy: {
      "/v1": "http://127.0.0.1:8765",
    },
  },
});
