import { lazy, Suspense } from "react";
import { BrowserRouter, Navigate, Route, Routes } from "react-router";

import { Loading } from "@/components/pieces";
import { Layout } from "@/layout/Layout";
import {
  LEGACY_PROFILE_PATHS,
  LEGACY_TOP_PATHS,
  LegacyRedirect,
} from "@/legacyRoutes";
import { Chat } from "@/pages/Chat";
import { Compare } from "@/pages/Compare";
import { Cycles } from "@/pages/Cycles";
import { Decisions } from "@/pages/Decisions";
import { Home } from "@/pages/Home";
import { NotFound } from "@/pages/NotFound";
import { Profiles } from "@/pages/Profiles";
import { Settings } from "@/pages/Settings";
import { Summary } from "@/pages/Summary";
import { System } from "@/pages/System";

/**
 * The analytics screen is bundled apart and loaded only when opened.
 *
 * Recharts weighs almost as much as the rest of the application together (the
 * bundle went from 350 to 733 KB once it was included), and it is the only
 * screen that uses it. Loading it at startup would make whoever came only to
 * check whether the 11:20 cycle opened anything wait for six charts.
 */
const Analytics = lazy(() =>
  import("@/pages/Analytics").then((m) => ({ default: m.Analytics })),
);

/**
 * Routing (F4.3).
 *
 * **The profile travels in the URL by its name**, not by its id:
 * `/p/europa-01/positions`. With a UUID there nobody would know which
 * experiment they were looking at, which was precisely the reason for taking it
 * out of React's memory. The API accepts a name or an id (`find_profile`), so
 * no translation is needed.
 *
 * The profile's routes all live inside `/p/:profile/` so the name cannot be lost
 * while navigating: with the profile as an optional query parameter, any link
 * that forgot to drag it along would leave the user looking at another
 * experiment with no warning.
 *
 * **Five screens folded into others in the redesign** of 2026-09-28 —Posiciones
 * into Resumen, Órdenes and Riesgo into Decisiones, Ingesta and Base de datos
 * into Sistema— and their addresses stay alive as redirects, for the same
 * reason F8.10 kept the Spanish ones: a bookmark is state the browser remembers.
 * The Spanish ones chain through these (`/ordenes` → `/orders` → the view), so
 * that table did not have to change.
 *
 * `../` in a profile redirect resolves **by route**, which is the default: from
 * `p/:profile/orders` it climbs to `p/:profile` and lands on its `decisions`.
 * The `relative="path"` trap `legacyRoutes` documents is not in play here.
 *
 * @return The router with every route of the application.
 */
export function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route element={<Layout />}>
          <Route index element={<Home />} />
          <Route path="profiles" element={<Profiles />} />
          <Route path="compare" element={<Compare />} />
          <Route path="system" element={<System />} />
          <Route path="diagnostics" element={<Navigate to="/system" replace />} />
          <Route path="database" element={<Navigate to="/system?view=database" replace />} />

          {/*
            The routes F8.8 renamed (F8.10). They are mapped from the tables in
            `legacyRoutes` rather than written out, so a name added there cannot
            be forgotten here — which is the failure a compatibility layer has
            no way of reporting.
          */}
          {LEGACY_TOP_PATHS.map((path) => (
            <Route key={path} path={path} element={<LegacyRedirect />} />
          ))}

          <Route path="p/:profile">
            <Route index element={<Navigate to="summary" replace />} />
            <Route path="summary" element={<Summary />} />
            <Route
              path="analytics"
              element={
                <Suspense fallback={<Loading text="Cargando gráficas…" />}>
                  <Analytics />
                </Suspense>
              }
            />
            <Route path="decisions" element={<Decisions />} />
            <Route path="positions" element={<Navigate to="../summary" replace />} />
            <Route path="orders" element={<Navigate to="../decisions?view=orders" replace />} />
            <Route path="risk" element={<Navigate to="../decisions?view=risk" replace />} />
            <Route path="cycles" element={<Cycles />} />
            <Route path="chat" element={<Chat />} />
            <Route path="settings" element={<Settings />} />
            {LEGACY_PROFILE_PATHS.map((path) => (
              <Route key={path} path={path} element={<LegacyRedirect />} />
            ))}
          </Route>

          <Route path="*" element={<NotFound />} />
        </Route>
      </Routes>
    </BrowserRouter>
  );
}
