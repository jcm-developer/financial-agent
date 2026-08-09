import { Link, useLocation } from "react-router";

import { buttonClasses, Card, BlockTitle } from "@/components/pieces";

/**
 * The router's 404, which is not the same as the API's 404.
 *
 * The requested route is shown on purpose: the API returns index.html for any
 * route that does not start with `/api/` (F3.7), so a mistyped link reaches here
 * instead of giving a server error. Without the route in sight, a typo in the
 * URL looks like a bug in the application.
 *
 * @return The rendered screen, naming the route that was asked for.
 */
export function NotFound() {
  const { pathname } = useLocation();

  return (
    <Card padding="p-6">
      <BlockTitle as="h1" className="text-[15px]">
        Esta página no existe
      </BlockTitle>
      <p className="mt-2 text-[13px] text-text-secondary">
        No hay nada en <code className="text-foreground">{pathname}</code>.
      </p>
      <Link to="/" className={buttonClasses("neutral", "mt-4")}>
        Volver al inicio
      </Link>
    </Card>
  );
}
