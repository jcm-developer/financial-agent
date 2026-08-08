import { lazy, Suspense } from "react";
import { BrowserRouter, Navigate, Route, Routes } from "react-router";

import { Cargando } from "@/components/piezas";
import { Layout } from "@/layout/Layout";
import { Ciclos } from "@/paginas/Ciclos";
import { Decisiones } from "@/paginas/Decisiones";
import { Diagnostico } from "@/paginas/Diagnostico";
import { Inicio } from "@/paginas/Inicio";
import { NoEncontrado } from "@/paginas/NoEncontrado";
import { Ordenes } from "@/paginas/Ordenes";
import { Pendiente } from "@/paginas/Pendiente";
import { Perfiles } from "@/paginas/Perfiles";
import { Posiciones } from "@/paginas/Posiciones";
import { Resumen } from "@/paginas/Resumen";

/**
 * La analitica se carga aparte y solo al abrirla.
 *
 * Recharts pesa casi tanto como el resto de la aplicacion junta (el paquete pasaba
 * de 350 a 733 KB al incluirlo), y es la unica pantalla que lo usa. Cargarlo en el
 * arranque haria esperar por seis graficas a quien solo viene a mirar si el ciclo
 * de las 11:20 abrio algo.
 */
const Analitica = lazy(() =>
  import("@/paginas/Analitica").then((m) => ({ default: m.Analitica })),
);
import { Riesgo } from "@/paginas/Riesgo";

/**
 * Enrutado (F4.3).
 *
 * **El perfil va en la URL con su nombre**, no con su id: `/p/europa-01/posiciones`.
 * Con un UUID ahí nadie sabría qué experimento está mirando, que era justo el
 * motivo de sacarlo de la memoria de React. La API acepta nombre o id
 * (`find_profile`), así que no hace falta traducir.
 *
 * Las rutas del perfil van todas dentro de `/p/:perfil/` para que el nombre no se
 * pueda perder al navegar: con el perfil como parámetro de consulta opcional,
 * cualquier enlace que se olvidara de arrastrarlo dejaría al usuario mirando otro
 * experimento sin avisar.
 */
export function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route element={<Layout />}>
          <Route index element={<Inicio />} />
          <Route path="perfiles" element={<Perfiles />} />
          <Route path="diagnostico" element={<Diagnostico />} />

          <Route path="p/:perfil">
            <Route index element={<Navigate to="resumen" replace />} />
            <Route path="resumen" element={<Resumen />} />
            <Route
              path="analitica"
              element={
                <Suspense fallback={<Cargando texto="Cargando gráficas…" />}>
                  <Analitica />
                </Suspense>
              }
            />
            <Route path="posiciones" element={<Posiciones />} />
            <Route path="decisiones" element={<Decisiones />} />
            <Route path="ordenes" element={<Ordenes />} />
            <Route path="riesgo" element={<Riesgo />} />
            <Route path="ciclos" element={<Ciclos />} />
            <Route
              path="ajustes"
              element={
                <Pendiente
                  titulo="Ajustes"
                  tarea="F6.8"
                  descripcion="Los 41 parámetros del experimento, con los deslizadores de riesgo y diversificación y los límites derivados visibles en vivo."
                />
              }
            />
          </Route>

          <Route path="*" element={<NoEncontrado />} />
        </Route>
      </Routes>
    </BrowserRouter>
  );
}
