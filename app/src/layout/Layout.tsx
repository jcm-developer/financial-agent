import { Link, Outlet } from "react-router";

import { useStream } from "@/api/stream";
import { LiveIndicator } from "@/components/LiveIndicator";
import { buttonClasses, Card, BlockTitle } from "@/components/pieces";
import { Sidebar } from "@/layout/Sidebar";
import { ProfileSelector } from "@/layout/ProfileSelector";
import { useActiveProfile } from "@/profile/useActiveProfile";

/**
 * The application's frame.
 *
 * **The stream is opened here, once.** If every screen called `useStream()`
 * there would be one SSE connection per mounted screen, and the server would run
 * the same SQLite poll as many times as there are open tabs —exactly what F3.5
 * set out to avoid by moving the polling from the browser to the server. The
 * screens read from the Query cache, which is where the stream writes.
 *
 * The header is **sticky and opaque**. Opaque and not translucent on purpose:
 * Verdana's elevation is diffused shadow, never blur, and a frosted bar over a
 * table of figures lowers the contrast of the text precisely where it is read
 * most. What separates it from the page is the hairline and the sm shadow.
 *
 * @return The rendered frame, with the active screen in its outlet.
 */
export function Layout() {
  const { ref, profile, notFound } = useActiveProfile();
  const stream = useStream();

  return (
    <div className="min-h-dvh">
      <a className="skip-link" href="#content">
        Saltar al contenido
      </a>

      <header className="sticky top-0 z-30 border-b border-border bg-card shadow-sm">
        <div className="mx-auto flex max-w-7xl flex-wrap items-center justify-between gap-4 px-6 py-3">
          <Link
            to="/"
            className="font-headline text-h4 font-bold tracking-tight text-foreground"
          >
            financial-agent
          </Link>
          <div className="flex flex-wrap items-center gap-4">
            <ProfileSelector />
            <LiveIndicator
              state={stream.state}
              reconnections={stream.reconnections}
              notice={stream.lastNotice}
            />
          </div>
        </div>
      </header>

      <div className="mx-auto flex max-w-7xl flex-col gap-8 px-6 py-8 md:flex-row">
        <aside className="md:w-56 md:shrink-0">
          <div className="md:sticky md:top-24">
            <Sidebar profile={profile?.name ?? ref} />
          </div>
        </aside>

        {/* `min-w-0` is not decoration: without it a wide table stretches the
            flex container and breaks the table's own `overflow-x-auto`. */}
        <main id="content" tabIndex={-1} className="min-w-0 flex-1 pb-16">
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
    <Card padding="p-8">
      <BlockTitle as="h1" className="text-h2">
        No hay ningún experimento llamado «{name}»
      </BlockTitle>
      <p className="mt-3 text-body text-text-secondary">
        Puede que lo hayas renombrado o borrado. La URL lleva el nombre del perfil, así que
        un enlace guardado deja de valer cuando el nombre cambia.
      </p>
      <Link to="/profiles" className={buttonClasses("primary", "mt-6")}>
        Ver los experimentos que hay
      </Link>
    </Card>
  );
}
