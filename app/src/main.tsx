import { StrictMode } from "react";
import { createRoot } from "react-dom/client";

import { App } from "@/App";
import "@/index.css";

const raiz = document.getElementById("root");
if (!raiz) {
  // No deberia pasar nunca: index.html lo trae. Pero fallar aqui con un motivo
  // es mejor que un `null!` que revienta tres capas mas abajo.
  throw new Error("Falta <div id=\"root\"> en index.html.");
}

createRoot(raiz).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
