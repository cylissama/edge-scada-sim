import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// Dev: `kubectl -n scada port-forward svc/api 8000` then `npm run dev`.
export default defineConfig({
  plugins: [react()],
  server: { proxy: { "/api": "http://localhost:8000" } },
});
