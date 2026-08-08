import { Link, Outlet } from "react-router";

import { useStream } from "@/api/stream";
import { IndicadorEnVivo } from "@/components/IndicadorEnVivo";
import { clasesBoton, Tarjeta, TituloBloque } from "@/components/piezas";
import { BarraLateral } from "@/layout/BarraLateral";
import { BotonTema } from "@/layout/BotonTema";
import { SelectorPerfil } from "@/layout/SelectorPerfil";
import { usePerfilActivo } from "@/perfil/usePerfilActivo";

/**
 * Marco de la aplicación (F4.3).
 *
 * **El stream se abre aquí, una sola vez.** Si cada pantalla llamara a
 * `useStream()` habría una conexión SSE por pantalla montada, y el servidor haría
 * el mismo sondeo a SQLite tantas veces como pestañas abiertas —justo lo que F3.5
 * quería evitar moviendo el sondeo del navegador al servidor—. Las pantallas leen
 * de la caché de Query, que es donde el stream escribe.
 */
export function Layout() {
  const { referencia, perfil, noEncontrado } = usePerfilActivo();
  const stream = useStream();

  return (
    <div className="min-h-dvh">
      <a className="salto" href="#contenido">
        Saltar al contenido
      </a>
      <header className="border-b border-border">
        <div className="mx-auto flex max-w-7xl flex-wrap items-center justify-between gap-4 px-5 py-3">
          <Link to="/" className="text-[15px] font-semibold tracking-tight">
            financial-bot
          </Link>
          <div className="flex flex-wrap items-center gap-3">
            <SelectorPerfil />
            <IndicadorEnVivo
              estado={stream.estado}
              reconexiones={stream.reconexiones}
              aviso={stream.ultimoAviso}
            />
            <BotonTema />
          </div>
        </div>
      </header>

      <div className="mx-auto flex max-w-7xl flex-col gap-6 px-5 py-6 md:flex-row">
        <aside className="md:w-52 md:shrink-0">
          <BarraLateral perfil={perfil?.name ?? referencia} />
        </aside>

        <main id="contenido" tabIndex={-1} className="min-w-0 flex-1 pb-16">
          {noEncontrado ? <PerfilInexistente referencia={referencia!} /> : <Outlet />}
        </main>
      </div>
    </div>
  );
}

/**
 * No se redirige en silencio a propósito.
 *
 * Un enlace guardado que apunta a un experimento renombrado o borrado tiene que
 * decirlo: mandar al usuario al inicio le dejaría creyendo que se ha equivocado
 * de clic, y si el perfil se borró por accidente sería la única señal que
 * existía.
 */
function PerfilInexistente({ referencia }: { referencia: string }) {
  return (
    <Tarjeta relleno="p-6">
      <TituloBloque como="h1" className="text-[15px]">
        No hay ningún experimento llamado «{referencia}»
      </TituloBloque>
      <p className="mt-2 text-[13px] text-text-secondary">
        Puede que lo hayas renombrado o borrado. La URL lleva el nombre del perfil, así que
        un enlace guardado deja de valer cuando el nombre cambia.
      </p>
      <Link to="/perfiles" className={clasesBoton("neutro", "mt-4")}>
        Ver los experimentos que hay
      </Link>
    </Tarjeta>
  );
}
