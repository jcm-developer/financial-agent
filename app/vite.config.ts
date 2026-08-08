import path from "node:path";
import { fileURLToPath } from "node:url";

import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// `__dirname` no existe en un config ESM, y `new URL(...).pathname` deja una
// barra delante de la letra de unidad en Windows. Esta es la unica forma que
// funciona en los dos sitios, y aqui importa: se desarrolla en Windows y se
// despliega en Linux dentro de Docker.
const here = path.dirname(fileURLToPath(import.meta.url));

export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: {
    // `@/…` es lo que espera shadcn/ui en los componentes que copia.
    alias: { "@": path.resolve(here, "src") },
  },
  build: {
    // Donde lo busca la API (api/deps.py, APP_DIST). Si esto cambia, el
    // servidor sirve la pagina de "falta el frontend" y cuesta un rato ver
    // por que.
    outDir: "dist",
    emptyOutDir: true,
    sourcemap: true,
  },
  server: {
    port: 5173,
    // Nada de `open: true`: el contenedor no tiene navegador.
    proxy: {
      "/api": {
        // El 8000 es el default de `python run.py api`, pero ahi tambien escucha
        // el dashboard viejo (`run.py serve`), y los dos no caben. Mientras F8.2
        // no lo retire hay que poder mover uno de sitio:
        //
        //   python run.py api --port 8001
        //   VITE_API_TARGET=http://127.0.0.1:8001 npm run dev
        //
        // Y hace falta de verdad: el experimento de dos semanas se vigila con el
        // dashboard viejo mientras esto se construye.
        target: process.env.VITE_API_TARGET ?? "http://127.0.0.1:8000",
        changeOrigin: true,
      },
    },
  },
});
