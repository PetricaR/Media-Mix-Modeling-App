import React, { Suspense } from "react";
import { Accordion, AccordionDetails, AccordionSummary, Box, Skeleton, Table, TableBody, TableCell, TableContainer, TableHead, TableRow, Typography } from "@mui/material";
import ExpandMoreIcon from "@mui/icons-material/ExpandMore";
import type { ScenarioResult } from "../../api/types";
import { NUMERIC, PALETTE } from "../../theme";
import { effectsFigure } from "./figures";
import { eur, eurCompact, multiplierLabel, signedEur } from "./format";

const LazyPlotlyChart = React.lazy(() => import("../Charts/PlotlyChart"));

const num = { ...NUMERIC, whiteSpace: "nowrap" } as const;

/** What each changed channel adds to (or takes from) the forecast, as a bar chart with its numbers one click away. */
export const ChannelEffects: React.FC<{ result: ScenarioResult; stale: boolean; compact?: boolean }> = ({ result, stale, compact = false }) => {
  const rows = result.channel_summary.filter((r) => Math.abs(r.multiplier - 1) > 1e-6 && r.delta_sales != null);
  if (!rows.length) return null;
  const fig = effectsFigure(rows, { delta: result.delta_sales, lo: result.delta_sales_lo, hi: result.delta_sales_hi }, compact);
  const hasCi = result.delta_sales_lo != null && result.delta_sales_hi != null;
  const ci = (lo?: number | null, hi?: number | null) => (lo != null && hi != null ? `${eurCompact(lo, true)} to ${eurCompact(hi, true)}` : "–");

  return (
    <Box sx={{ mt: 1.5, opacity: stale ? 0.55 : 1, transition: "opacity 0.2s" }}>
      <Suspense fallback={<Skeleton variant="rounded" height={fig.layout.height} />}>
        <LazyPlotlyChart data={fig.data} layout={fig.layout} height={fig.layout.height} ariaLabel="Effect of each budget change on forecast sales" />
      </Suspense>
      <Typography variant="caption" component="p" sx={{ color: PALETTE.inkSecondary }}>
        Δ sales / Δ spend is the return on the euros added or removed, not the channel's average ROI.
        {!hasCi && result.method ? ` Point estimates only: ${result.method}.` : ""}
      </Typography>
      <Accordion disableGutters elevation={0} sx={{ mt: 1.5, border: `1px solid ${PALETTE.borderSubtle}`, borderRadius: 1.5, "&:before": { display: "none" } }}>
        <AccordionSummary expandIcon={<ExpandMoreIcon />} aria-controls="effects-table" id="effects-table-header">
          <Typography variant="body2" sx={{ fontWeight: 650, color: PALETTE.inkPrimary }}>
            Numbers behind the chart
          </Typography>
        </AccordionSummary>
        <AccordionDetails id="effects-table">
          <TableContainer role="region" aria-label="Effect of each budget change" tabIndex={0} sx={{ border: `1px solid ${PALETTE.borderSubtle}`, borderRadius: 1.5 }}>
            <Table size="small">
              <TableHead>
                <TableRow>
                  <TableCell scope="col">Channel</TableCell>
                  <TableCell scope="col" align="right">Budget</TableCell>
                  <TableCell scope="col" align="right">Plan spend</TableCell>
                  <TableCell scope="col" align="right">Scenario spend</TableCell>
                  <TableCell scope="col" align="right">Δ spend</TableCell>
                  <TableCell scope="col" align="right">{hasCi ? "Δ sales (90 % interval)" : "Δ sales"}</TableCell>
                  <TableCell scope="col" align="right">Δ sales / Δ spend</TableCell>
                </TableRow>
              </TableHead>
              <TableBody>
                {rows.map((r) => (
                  <TableRow key={r.slug} hover>
                    <TableCell component="th" scope="row" sx={{ fontWeight: 600 }}>
                      {r.channel}
                    </TableCell>
                    <TableCell align="right" sx={num}>{multiplierLabel(r.multiplier)}</TableCell>
                    <TableCell align="right" sx={num}>{eur(r.base_spend)}</TableCell>
                    <TableCell align="right" sx={num}>{eur(r.scenario_spend)}</TableCell>
                    <TableCell align="right" sx={num}>{signedEur(r.delta_spend)}</TableCell>
                    <TableCell align="right" sx={num}>
                      <Box sx={{ fontWeight: 700 }}>{signedEur(r.delta_sales ?? 0)}</Box>
                      {hasCi && <Box sx={{ fontSize: "0.74rem", color: PALETTE.inkSecondary }}>{ci(r.delta_sales_lo, r.delta_sales_hi)}</Box>}
                    </TableCell>
                    <TableCell align="right" sx={num}>{r.delta_roas == null ? "–" : r.delta_roas.toFixed(2)}</TableCell>
                  </TableRow>
                ))}
                <TableRow sx={{ "& > *": { borderTop: `2px solid ${PALETTE.borderSubtle}` } }}>
                  <TableCell component="th" scope="row" sx={{ fontWeight: 700 }}>
                    All changes
                  </TableCell>
                  <TableCell />
                  <TableCell align="right" sx={num}>{eur(result.base_total_spend)}</TableCell>
                  <TableCell align="right" sx={num}>{eur(result.scenario_total_spend)}</TableCell>
                  <TableCell align="right" sx={num}>{signedEur(result.delta_spend)}</TableCell>
                  <TableCell align="right" sx={num}>
                    <Box sx={{ fontWeight: 700 }}>{signedEur(result.delta_sales)}</Box>
                    {hasCi && <Box sx={{ fontSize: "0.74rem", color: PALETTE.inkSecondary }}>{ci(result.delta_sales_lo, result.delta_sales_hi)}</Box>}
                  </TableCell>
                  <TableCell align="right" sx={num}>{result.marginal_roas == null ? "–" : result.marginal_roas.toFixed(2)}</TableCell>
                </TableRow>
              </TableBody>
            </Table>
          </TableContainer>
        </AccordionDetails>
      </Accordion>
    </Box>
  );
};
