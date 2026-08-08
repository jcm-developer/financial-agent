import { BrowserRouter, Navigate, Route, Routes } from "react-router";

import { Layout } from "@/layout/Layout";
import { Diagnostico } from "@/paginas/Diagnostico";
import { Inicio } from "@/paginas/Inicio";
import { NoEncontrado } from "@/paginas/NoEncontrado";
import { Pendiente } from "@/paginas/Pendiente";
import { Perfiles } from "@/paginas/Perfiles";

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
            <Route
              path="resumen"
              element={
                <Pendiente
                  titulo="Resumen"
                  tarea="F4.7"
                  descripcion="Capital, P&L del día y total, posiciones abiertas, win rate y el último ciclo."
                />
              }
            />
            <Route
              path="posiciones"
              element={
                <Pendiente
                  titulo="Posiciones"
                  tarea="F4.7"
                  descripcion="Abiertas y cerradas, con su stop, su objetivo y de dónde sale el precio con el que se valoran (live o cycle)."
                />
              }
            />
            <Route
              path="decisiones"
              element={
                <Pendiente
                  titulo="Decisiones"
                  tarea="F4.7"
                  descripcion="Lo que propuso el analista, con su tesis, sus riesgos y su convicción, y el veredicto del Risk Manager al lado."
                />
              }
            />
            <Route
              path="ordenes"
              element={
                <Pendiente
                  titulo="Órdenes"
                  tarea="F4.7"
                  descripcion="Enviadas, ejecutadas y no ejecutadas, con el motivo de las que se quedaron sin ejecutar."
                />
              }
            />
            <Route
              path="riesgo"
              element={
                <Pendiente
                  titulo="Eventos de riesgo"
                  tarea="F4.7"
                  descripcion="Contra qué límite chocó cada propuesta rechazada, y los disparos del kill switch."
                />
              }
            />
            <Route
              path="ciclos"
              element={
                <Pendiente
                  titulo="Ciclos"
                  tarea="F4.7"
                  descripcion="Cada ejecución del agente con su log en vivo, y las llamadas del analista que se quedaron sin respuesta (F6.9)."
                />
              }
            />
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
