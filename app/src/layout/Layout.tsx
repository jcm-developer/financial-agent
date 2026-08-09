import { Link, Outlet } from "react-router";

import { useStream } from "@/api/stream";
import { LiveIndicator } from "@/components/LiveIndicator";
import { buttonClasses, Card, BlockTitle } from "@/components/pieces";
import { Sidebar } from "@/layout/Sidebar";
import { ThemeButton } from "@/layout/ThemeButton";
import { ProfileSelector } from "@/layout/ProfileSelector";
import { useActiveProfile } from "@/profile/useActiveProfile";

/**
 * The application's frame (F4.3).
 *
 * **The stream is opened here, once.** If every screen called `useStream()`
 * there would be one SSE connection per mounted screen, and the server would run
 * the same SQLite poll as many times as there are open tabs —exactly what F3.5
 * set out to avoid by moving the polling from the browser to the server. The
 * screens read from the Query cache, which is where the stream writes.
 *
 * @return The rendered frame, with the active screen in its outlet.
 */
export function Layout() {
  const { ref, profile, notFound } = useActiveProfile();
  const stream = useStream();

  return (
    <div className="min-h-dvh">
      <a className="salto" href="#contenido">
        Saltar al contenido
      </a>
      <header className="border-b border-border">
        <div className="mx-auto flex max-w-7xl flex-wrap items-center justify-between gap-4 px-5 py-3">
          <Link to="/" className="text-[15px] font-semibold tracking-tight">
            financial-agent
          </Link>
          <div className="flex flex-wrap items-center gap-3">
            <ProfileSelector />
            <LiveIndicator
              state={stream.state}
              reconnections={stream.reconnections}
              notice={stream.lastNotice}
            />
            <ThemeButton />
          </div>
        </div>
      </header>

      <div className="mx-auto flex max-w-7xl flex-col gap-6 px-5 py-6 md:flex-row">
        <aside className="md:w-52 md:shrink-0">
          <Sidebar profile={profile?.name ?? ref} />
        </aside>

        <main id="contenido" tabIndex={-1} className="min-w-0 flex-1 pb-16">
          {notFound ? <ProfileNotFound name={ref!} /> : <Outlet />}
        </main>
      </div>
    </div>
  );
}

/**
 * There is deliberately no silent redirect.
 *
 * A saved link pointing at a renamed or deleted experiment has to say so:
 * sending the user home would leave them thinking they misclicked, and if the
 * profile was deleted by accident this would have been the only signal there was.
 *
 * @param props - Screen props.
 * @param props.name - The name the URL asked for, quoted back so the mismatch is
 *     visible.
 * @return The rendered screen.
 */
function ProfileNotFound({ name }: { name: string }) {
  return (
    <Card padding="p-6">
      <BlockTitle as="h1" className="text-[15px]">
        No hay ningún experimento llamado «{name}»
      </BlockTitle>
      <p className="mt-2 text-[13px] text-text-secondary">
        Puede que lo hayas renombrado o borrado. La URL lleva el nombre del perfil, así que
        un enlace guardado deja de valer cuando el nombre cambia.
      </p>
      <Link to="/profiles" className={buttonClasses("neutral", "mt-4")}>
        Ver los experimentos que hay
      </Link>
    </Card>
  );
}
