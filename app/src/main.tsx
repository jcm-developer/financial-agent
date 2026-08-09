import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { QueryClientProvider } from "@tanstack/react-query";

import { createQueryClient } from "@/api/queryClient";
import { App } from "@/App";
import "@/index.css";

const root = document.getElementById("root");
if (!root) {
  // Should never happen: index.html brings it. But failing here with a reason
  // beats a `null!` that blows up three layers down.
  throw new Error("Falta <div id=\"root\"> en index.html.");
}

// Created once and outside the component: inside, every render would make a new
// client and the cache would empty itself.
const queryClient = createQueryClient();

createRoot(root).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <App />
    </QueryClientProvider>
  </StrictMode>,
);
