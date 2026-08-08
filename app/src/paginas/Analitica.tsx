import { useAnalytics } from "@/api/hooks";
import { Calibracion, HistogramaConviccion } from "@/components/graficas/Calibracion";
import { CurvaCapital, Drawdown } from "@/components/graficas/CurvaCapital";
import { PnlPorSimbolo, RechazosPorRegla } from "@/components/graficas/PorSimbolo";
import { TituloPagina } from "@/components/piezas";
import { Seccion } from "@/components/Seccion";
import { usePerfilActivo } from "@/perfil/usePerfilActivo";
import { useTitulo } from "@/layout/useTitulo";

/**
 * Las seis gráficas del experimento (F4.6).
 *
 * Todas salen de **una sola petición** a `/api/analytics`: son agregados del
 * mismo fichero local y seis peticiones darían seis estados de carga y seis
 * formas de fallar a medias para pintar una pantalla.
 *
 * Tres de los agregados los calcula SQL con vistas que ya existían, así que esta
 * pantalla y `run.py report` no pueden acabar contando cosas distintas del mismo
 * experimento.
 */
export function Analitica() {
  const { perfil, referencia } = usePerfilActivo();
  useTitulo("Analítica", perfil?.name);
  const consulta = useAnalytics(referencia);
  const simbolo = perfil?.currency_symbol ?? "";

  return (
    <>
      <TituloPagina>Analítica</TituloPagina>

      <Seccion consulta={consulta}>
        {(datos) => (
          <div className="grid gap-4 xl:grid-cols-2">
            {/* La calibración va primera y ocupa el ancho: es la que responde a
                la pregunta del experimento, no una más de las seis. */}
            <div className="xl:col-span-2">
              <Calibracion tramos={datos.calibration ?? []} simbolo={simbolo} />
            </div>

            <CurvaCapital
              puntos={datos.equity_curve ?? []}
              simbolo={simbolo}
              presupuesto={perfil?.metrics.initial_budget}
            />
            <Drawdown puntos={datos.equity_curve ?? []} simbolo={simbolo} />
            <HistogramaConviccion tramos={datos.conviction_histogram ?? []} />
            <PnlPorSimbolo filas={datos.by_symbol ?? []} simbolo={simbolo} />
            <div className="xl:col-span-2">
              <RechazosPorRegla filas={datos.rejections ?? []} />
            </div>
          </div>
        )}
      </Seccion>
    </>
  );
}
