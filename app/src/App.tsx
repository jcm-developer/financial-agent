import { lazy, Suspense } from "react";
import { BrowserRouter, Navigate, Route, Routes } from "react-router";

import { Loading } from "@/components/pieces";
import { Layout } from "@/layout/Layout";
import {
  LEGACY_PROFILE_PATHS,
  LEGACY_TOP_PATHS,
  LegacyRedirect,
} from "@/legacyRoutes";
import { Compare } from "@/pages/Compare";
import { Cycles } from "@/pages/Cycles";
import { Decisions } from "@/pages/Decisions";
import { Diagnostics } from "@/pages/Diagnostics";
import { Home } from "@/pages/Home";
import { NotFound } from "@/pages/NotFound";
import { Orders } from "@/pages/Orders";
import { Profiles } from "@/pages/Profiles";
import { Settings } from "@/pages/Settings";
import { Positions } from "@/pages/Positions";
import { Summary } from "@/pages/Summary";

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
import { Risk } from "@/pages/Risk";

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
          <Route path="diagnostics" element={<Diagnostics />} />

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
            <Route path="positions" element={<Positions />} />
            <Route path="decisions" element={<Decisions />} />
            <Route path="orders" element={<Orders />} />
            <Route path="risk" element={<Risk />} />
            <Route path="cycles" element={<Cycles />} />
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
