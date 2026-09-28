import { useEffect, useState } from "react";
import { Link, Outlet, useLocation } from "react-router";
import { Menu, X } from "lucide-react";

import { useStream } from "@/api/stream";
import { LiveIndicator } from "@/components/LiveIndicator";
import { ThemeToggle } from "@/components/ThemeToggle";
import { Button, buttonClasses, Card, BlockTitle } from "@/components/pieces";
import { Sidebar } from "@/layout/Sidebar";
import { cn } from "@/lib/utils";
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
 * **Below `md` the sidebar folds behind a «Menú» button** (2026-09-28). It used
 * to stack above the content, so at phone width the header and twelve links
 * filled the whole first screen and every page began with a scroll past them.
 * Folded, the content starts under the header; the panel opens in the page flow
 * rather than as a drawer over it, which keeps it a plain list with nothing to
 * trap focus in or dismiss. It closes on navigation, since choosing a section
 * is what it was opened for. The theme switch moves into it at that width,
 * because the header has room for the name, the live indicator and one button.
 *
 * @return The rendered frame, with the active screen in its outlet.
 */
export function Layout() {
  const { ref, profile, notFound } = useActiveProfile();
  const stream = useStream();
  const location = useLocation();
  const [menuOpen, setMenuOpen] = useState(false);

  useEffect(() => {
    setMenuOpen(false);
  }, [location.pathname, location.search]);

  return (
    <div className="min-h-dvh">
      <a className="skip-link" href="#content">
        Saltar al contenido
      </a>

      <header className="sticky top-0 z-30 border-b border-border bg-card shadow-sm">
        <div className="mx-auto flex max-w-7xl flex-wrap items-center justify-between gap-4 px-4 py-3 md:px-6">
          <Link
            to="/"
            className="font-headline text-h4 font-bold tracking-tight text-foreground"
          >
            financial-agent
          </Link>
          <div className="flex items-center gap-2 md:gap-4">
            <LiveIndicator
              state={stream.state}
              reconnections={stream.reconnections}
              notice={stream.lastNotice}
            />
            <div className="hidden md:block">
              <ThemeToggle />
            </div>
            <Button
              variant="ghost"
              size="sm"
              className="md:hidden"
              icon={menuOpen ? X : Menu}
              aria-expanded={menuOpen}
              aria-controls="sections"
              title={menuOpen ? "Cerrar el menú" : "Abrir el menú"}
              onClick={() => setMenuOpen((value) => !value)}
            >
              {/* The same word open and closed: «Cerrar» is wider than «Menú»,
                  and at 390 px that difference was enough to wrap the header
                  onto two rows the moment the panel opened. The icon and
                  `aria-expanded` carry the state. */}
              Menú
            </Button>
          </div>
        </div>
      </header>

      <div className="mx-auto flex max-w-7xl flex-col gap-8 px-4 py-6 md:flex-row md:px-6 md:py-8">
        <aside
          id="sections"
          className={cn("md:block md:w-56 md:shrink-0", menuOpen ? "block" : "hidden")}
        >
          <div className="md:sticky md:top-24">
            <Sidebar profile={profile?.name ?? ref} />
            <div className="mt-6 border-t border-border pt-4 md:hidden">
              <ThemeToggle />
            </div>
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
        Puede que se haya renombrado o borrado.
      </p>
      <Link to="/profiles" className={buttonClasses("primary", "mt-6")}>
        Ver experimentos
      </Link>
    </Card>
  );
}
