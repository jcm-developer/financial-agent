import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { QueryClientProvider } from "@tanstack/react-query";

import { crearQueryClient } from "@/api/queryClient";
import { App } from "@/App";
import "@/index.css";

const raiz = document.getElementById("root");
if (!raiz) {
  // No deberia pasar nunca: index.html lo trae. Pero fallar aqui con un motivo
  // es mejor que un `null!` que revienta tres capas mas abajo.
  throw new Error("Falta <div id=\"root\"> en index.html.");
}

// Se crea una sola vez y fuera del componente: dentro, cada renderizado haria un
// cliente nuevo y la cache se vaciaria sola.
const queryClient = crearQueryClient();

createRoot(raiz).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <App />
    </QueryClientProvider>
  </StrictMode>,
);
