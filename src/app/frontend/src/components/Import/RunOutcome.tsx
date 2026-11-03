import React from "react";
import { Box, Button, Paper, Typography, alpha } from "@mui/material";
import CheckCircleIcon from "@mui/icons-material/CheckCircle";
import ErrorIcon from "@mui/icons-material/Error";
import InfoIcon from "@mui/icons-material/Info";
import ArrowForwardIcon from "@mui/icons-material/ArrowForward";
import ReplayIcon from "@mui/icons-material/Replay";
import { Link as RouterLink } from "react-router-dom";
import type { RunDetail } from "../../api/types";
import { PALETTE } from "../../theme";
import { formatDuration, pipelineElapsedMs } from "./format";

const num = (v: unknown): number | null => (typeof v === "number" && Number.isFinite(v) ? v : null);
const metric = (summary: Record<string, unknown> | null | undefined, block: string, key: string): number | null => {
  const b = summary?.[block];
  return b && typeof b === "object" ? num((b as Record<string, unknown>)[key]) : null;
};

/** "Q4 2024 hold-out" from the window label the pipeline stored (the hold-out length is a run setting). */
const windowLabel = (summary: Record<string, unknown> | null | undefined, key: string, fallback: string, suffix = "hold-out"): string => {
  const label = summary?.[key];
  return typeof label === "string" && label ? `${label} ${suffix}` : fallback;
};

const TONES = {
  good: { border: PALETTE.semanticGood, bg: alpha(PALETTE.semanticGood, 0.06), icon: <CheckCircleIcon sx={{ color: PALETTE.semanticGood }} /> },
  bad: { border: PALETTE.semanticBad, bg: alpha(PALETTE.semanticBad, 0.06), icon: <ErrorIcon sx={{ color: PALETTE.semanticBad }} /> },
  warn: { border: PALETTE.semanticWarn, bg: alpha(PALETTE.semanticWarn, 0.08), icon: <InfoIcon sx={{ color: PALETTE.semanticWarn }} /> },
};

/**
 * The outcome of a finished, failed or cancelled run and the obvious next step. Model checks come from the run's own
 * summary (written by the pipeline) and are left out when a value does not exist, for example when Meridian was skipped.
 */
export const RunOutcome: React.FC<{ run: RunDetail; onRerun: () => void }> = ({ run, onRerun }) => {
  if (!["succeeded", "failed", "cancelled"].includes(run.status)) return null;

  const counted = run.stages.filter((s) => s.status !== "skipped");
  const ok = counted.filter((s) => s.status === "succeeded").length;
  const failedStages = counted.filter((s) => s.status === "failed");
  const elapsed = pipelineElapsedMs(run.stages, Date.now(), false);
  const took = elapsed !== null ? ` in ${formatDuration(elapsed)}` : "";
  const meridianOff = Boolean(run.config?.skip_meridian);

  let tone: keyof typeof TONES;
  let title: string;
  let body: string;
  if (run.status === "succeeded") {
    tone = "good";
    title = "Results are ready";
    body =
      failedStages.length === 0
        ? `All ${counted.length} stages finished${took}${meridianOff ? " (Meridian was switched off)" : ""}. Every page of the studio now shows this run.`
        : `${ok} of ${counted.length} stages finished${took}; ${failedStages.map((s) => s.label).join(", ")} failed, so the pages built on ${failedStages.length === 1 ? "it" : "them"} are incomplete.`;
  } else if (run.status === "failed") {
    tone = "bad";
    title = "The run failed";
    body = `${failedStages.length ? `It stopped at “${failedStages[0].label}”. ` : ""}Check the error below, adjust the settings if needed and run it again; the uploaded workbook is kept.`;
  } else {
    tone = "warn";
    title = "The run was cancelled";
    body = `${ok} of ${counted.length} stages had finished; their results stay available. Start it again to complete the rest.`;
  }

  const checks =
    run.status === "succeeded"
      ? [
          { label: windowLabel(run.summary, "holdout_label", "Hold-out"), value: metric(run.summary, "holdout_q4_2024", "MAE_over_mean"), fmt: (v: number) => `${(v * 100).toFixed(1)} %`, note: "MAE / mean" },
          { label: windowLabel(run.summary, "forward_label", "Forward hold-out", "forward hold-out"), value: metric(run.summary, "holdout_q3_2024", "MAE_over_mean"), fmt: (v: number) => `${(v * 100).toFixed(1)} %`, note: "MAE / mean" },
          { label: "Max R-hat", value: num(run.summary?.max_rhat), fmt: (v: number) => v.toFixed(3), note: "convergence" },
        ].filter((c) => c.value !== null)
      : [];

  const t = TONES[tone];
  return (
    <Paper component="section" aria-labelledby="outcome-heading" sx={{ p: { xs: 2.5, sm: 3 }, borderColor: t.border, borderLeftWidth: 4, backgroundColor: t.bg }}>
      <Box sx={{ display: "grid", gridTemplateColumns: "24px minmax(0, 1fr)", columnGap: 1.5 }}>
        <Box aria-hidden="true" sx={{ pt: "1px" }}>
          {t.icon}
        </Box>
        <Box>
          <Typography id="outcome-heading" variant="h3" component="h2">
            {title}
          </Typography>
          <Typography variant="body2" sx={{ mt: 0.25, color: PALETTE.inkPrimary }}>
            {body}
          </Typography>

          {checks.length > 0 && (
            <Box component="dl" sx={{ m: 0, mt: 2, display: "flex", flexWrap: "wrap", gap: 1 }}>
              {checks.map((c) => (
                <Box key={c.label} sx={{ px: 1.5, py: 1, borderRadius: 2, backgroundColor: "#FFFFFF", border: `1px solid ${PALETTE.borderSubtle}`, minWidth: 140 }}>
                  <Typography component="dt" variant="caption" sx={{ display: "block", color: PALETTE.inkSecondary, fontWeight: 600 }}>
                    {c.label}
                  </Typography>
                  <Typography component="dd" sx={{ m: 0, fontWeight: 700, fontVariantNumeric: "tabular-nums", color: PALETTE.inkPrimary }}>
                    {c.fmt(c.value as number)}{" "}
                    <Typography component="span" variant="caption" sx={{ color: PALETTE.inkMuted, fontWeight: 500 }}>
                      {c.note}
                    </Typography>
                  </Typography>
                </Box>
              ))}
            </Box>
          )}

          <Box sx={{ display: "flex", flexWrap: "wrap", gap: 1, mt: 2 }}>
            {run.status === "succeeded" ? (
              <>
                <Button variant="contained" component={RouterLink} to="/overview" endIcon={<ArrowForwardIcon />}>
                  Open the executive overview
                </Button>
                <Button variant="outlined" component={RouterLink} to="/brief" sx={{ backgroundColor: "#FFFFFF" }}>
                  Answers to the brief
                </Button>
                <Button component={RouterLink} to="/reports">
                  Reports & downloads
                </Button>
              </>
            ) : (
              <Button variant="contained" startIcon={<ReplayIcon />} onClick={onRerun}>
                Review settings and re-run
              </Button>
            )}
          </Box>
        </Box>
      </Box>
    </Paper>
  );
};
