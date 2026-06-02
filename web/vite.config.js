import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

export default defineConfig({
  plugins: [react()],
  build: {
    outDir: "../static",
    emptyOutDir: true,
    rollupOptions: {
      output: {
        manualChunks: {
          charts: ["recharts"],
          react: ["react", "react-dom", "react-router-dom"],
        },
      },
    },
  },
  server: {
    proxy: {
      "/events": "http://127.0.0.1:3001",
      "/sessions": "http://127.0.0.1:3001",
      "/summary": "http://127.0.0.1:3001",
      "/dapr": "http://127.0.0.1:3001",
      "/healthz": "http://127.0.0.1:3001",
      "/readyz": "http://127.0.0.1:3001",
    },
  },
});
