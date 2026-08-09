import { useAnalytics } from "@/api/hooks";
import { Calibration, ConvictionHistogram } from "@/components/charts/Calibration";
import { EquityCurve, Drawdown } from "@/components/charts/EquityCurve";
import { PnlBySymbol, RejectionsByRule } from "@/components/charts/BySymbol";
import { PageTitle } from "@/components/pieces";
import { Section } from "@/components/Section";
import { useActiveProfile } from "@/profile/useActiveProfile";
import { useTitle } from "@/layout/useTitle";

/**
 * The experiment's six charts (F4.6).
 *
 * They all come from **a single request** to `/api/analytics`: they are
 * aggregates of the same local file, and six requests would give six loading
 * states and six ways of half-failing to paint one screen.
 *
 * Three of the aggregates are computed by SQL through views that already
 * existed, so this screen and `run.py report` cannot end up telling different
 * stories about the same experiment.
 *
 * @return The rendered screen with its six charts.
 */
export function Analytics() {
  const { profile, ref } = useActiveProfile();
  useTitle("Analítica", profile?.name);
  const query = useAnalytics(ref);
  const symbol = profile?.currency_symbol ?? "";

  return (
    <>
      <PageTitle>Analítica</PageTitle>

      <Section query={query}>
        {(data) => (
          <div className="grid gap-4 xl:grid-cols-2">
            {/* Calibration goes first and takes the full width: it is the one
                that answers the experiment's question, not one of the six. */}
            <div className="xl:col-span-2">
              <Calibration buckets={data.calibration ?? []} symbol={symbol} />
            </div>

            <EquityCurve
              points={data.equity_curve ?? []}
              symbol={symbol}
              budget={profile?.metrics.initial_budget}
            />
            <Drawdown points={data.equity_curve ?? []} symbol={symbol} />
            <ConvictionHistogram buckets={data.conviction_histogram ?? []} />
            <PnlBySymbol rows={data.by_symbol ?? []} symbol={symbol} />
            <div className="xl:col-span-2">
              <RejectionsByRule rows={data.rejections ?? []} />
            </div>
          </div>
        )}
      </Section>
    </>
  );
}
