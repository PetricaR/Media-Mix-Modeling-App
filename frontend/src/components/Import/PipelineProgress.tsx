import React, { useEffect, useState } from "react";
import { Alert, Box, Button, CircularProgress, Collapse, LinearProgress, Paper, Typography } from "@mui/material";
import CheckCircleIcon from "@mui/icons-material/CheckCircle";
import ErrorIcon from "@mui/icons-material/Error";
import RemoveCircleOutlineIcon from "@mui/icons-material/RemoveCircleOutline";
import ExpandMoreIcon from "@mui/icons-material/ExpandMore";
import StopCircleOutlinedIcon from "@mui/icons-material/StopCircleOutlined";
import HourglassEmptyIcon from "@mui/icons-material/HourglassEmpty";
import { isRunActive } from "../../api/hooks";
import type { RunDetail, StageStatus } from "../../api/types";
import { PALETTE } from "../../theme";
import { formatDuration, pipelineElapsedMs, stageDurationMs } from "./format";

const LANES = [
  { queue: "analysis", title: "Analysis", detail: "Data audit, EDA, preparation and the OLS benchmark" },
  { queue: "meridian", title: "Meridian", detail: "Bayesian MMM, ROI, optimiser, forecast and reports" },
];

/** A clock that ticks every second while `enabled`, so running stages show a live duration. */
function useNow(enabled: boolean): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!enabled) return;
    setNow(Date.now());
    const id = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(id);
  }, [enabled]);
  return now;
}

const StageIcon: React.FC<{ status: StageStatus["status"] }> = ({ status }) => {
  switch (status) {
    case "succeeded":
      return <CheckCircleIcon sx={{ fontSize: 18, color: PALETTE.semanticGood }} />;
    case "running":
      return <CircularProgress size={15} thickness={5} color="secondary" />;
    case "failed":
      return <ErrorIcon sx={{ fontSize: 18, color: PALETTE.semanticBad }} />;
    case "skipped":
    case "cancelled":
      return <RemoveCircleOutlineIcon sx={{ fontSize: 18, color: PALETTE.inkMuted }} />;
    default:
      return <Box sx={{ width: 12, height: 12, borderRadius: "50%", border: `2px solid ${PALETTE.borderStrong}` }} />;
  }
};

const StageRow: React.FC<{ stage: StageStatus; now: number }> = ({ stage, now }) => {
  const ms = stageDurationMs(stage, now);
  const running = stage.status === "running";
  let state: React.ReactNode;
  if (stage.status === "succeeded") {
    state = ms === null ? "Finished" : (
      <>
        <span className="visually-hidden">Finished in </span>
        {formatDuration(ms)}
      </>
    );
  } else if (running) {
    state = `Running${ms === null ? "" : ` · ${formatDuration(ms)}`}`;
  } else {
    const labels: Record<string, string> = { pending: "Waiting", failed: "Failed", skipped: "Skipped", cancelled: "Cancelled" };
    state = labels[stage.status] ?? stage.status;
  }

  return (
    <Box
      component="li"
      sx={{
        display: "grid",
        gridTemplateColumns: "20px minmax(0, 1fr) auto",
        columnGap: 1.25,
        alignItems: "start",
        py: 0.875,
        px: 1.25,
        mx: -1.25,
        borderRadius: 1,
        backgroundColor: running ? PALETTE.bgAccentTint : "transparent",
      }}
    >
      <Box aria-hidden="true" sx={{ height: 21, display: "flex", alignItems: "center", justifyContent: "center" }}>
        <StageIcon status={stage.status} />
      </Box>
      <Box sx={{ minWidth: 0 }}>
        <Typography variant="body2" sx={{ color: stage.status === "pending" ? PALETTE.inkSecondary : PALETTE.inkPrimary, fontWeight: running ? 700 : 550 }}>
          {stage.label}
        </Typography>
        {stage.error && (
          <Typography variant="caption" sx={{ display: "block", overflowWrap: "anywhere", color: stage.status === "failed" ? PALETTE.semanticBad : PALETTE.inkMuted }}>
            {stage.error}
          </Typography>
        )}
      </Box>
      <Typography
        variant="caption"
        sx={{
          pt: "2px",
          whiteSpace: "nowrap",
          fontVariantNumeric: "tabular-nums",
          fontWeight: running || stage.status === "failed" ? 700 : 500,
          color: stage.status === "failed" ? PALETTE.semanticBad : running ? PALETTE.brandPrimary : PALETTE.inkMuted,
        }}
      >
        {state}
      </Typography>
    </Box>
  );
};

export const PipelineProgress: React.FC<{ run: RunDetail; onCancel: () => void }> = ({ run, onCancel }) => {
  const active = isRunActive(run);
  const now = useNow(active);
  const stages = run.stages;
  const counted = stages.filter((s) => s.status !== "skipped");
  const finished = counted.filter((s) => s.status === "succeeded" || s.status === "failed").length;
  const skipped = stages.length - counted.length;
  const elapsed = pipelineElapsedMs(stages, now, active);
  const parallel = LANES.every((lane) => stages.some((s) => s.queue === lane.queue && s.status !== "skipped"));

  // The stage list is the Run status page's main content, so it starts open for every run
  const [open, setOpen] = useState(true);

  const queuePos = typeof run.queue_position === "number" ? run.queue_position : run.queue_position?.meridian ?? run.queue_position?.analysis ?? null;
  const summary = [
    `${finished} of ${counted.length} stages finished`,
    elapsed !== null ? `${formatDuration(elapsed)}${active ? " elapsed" : " in total"}` : null,
    skipped ? `${skipped} skipped` : null,
  ]
    .filter(Boolean)
    .join(" · ");

  return (
    <Paper component="section" aria-labelledby="pipeline-heading" sx={{ p: { xs: 2.5, sm: 3 } }}>
      <Box sx={{ display: "flex", alignItems: "flex-start", justifyContent: "space-between", flexWrap: "wrap", gap: 1.5 }}>
        <Box>
          <Typography id="pipeline-heading" variant="h3" component="h2">
            Pipeline
          </Typography>
          <Typography variant="body2" sx={{ fontVariantNumeric: "tabular-nums" }}>
            {summary}
          </Typography>
        </Box>
        {active && (
          <Button size="small" variant="outlined" color="warning" startIcon={<StopCircleOutlinedIcon />} onClick={onCancel}>
            Cancel run
          </Button>
        )}
      </Box>

      {active && (
        <>
          <LinearProgress
            color="secondary"
            variant={counted.length > 0 && finished > 0 ? "determinate" : "indeterminate"}
            value={counted.length > 0 ? (finished / counted.length) * 100 : 0}
            aria-label="Pipeline progress"
            aria-valuetext={`${finished} of ${counted.length} stages finished`}
            sx={{ mt: 2, height: 8, borderRadius: 4, backgroundColor: PALETTE.bgAccentTint, "& .MuiLinearProgress-bar": { borderRadius: 4 } }}
          />
          <Typography variant="caption" component="p" sx={{ mt: 1, color: PALETTE.inkSecondary, display: "flex", alignItems: "center", gap: 0.75 }}>
            {run.status === "queued" && <HourglassEmptyIcon aria-hidden="true" sx={{ fontSize: 15 }} />}
            {run.status === "queued"
              ? `Waiting for a free worker${queuePos && queuePos > 0 ? ` · position ${queuePos} in the queue` : ""}.`
              : "You can leave this page: the run continues on the server and each page fills in as its stage finishes."}
          </Typography>
        </>
      )}

      {run.error && (
        <Alert severity="error" sx={{ mt: 2 }}>
          {run.error}
        </Alert>
      )}

      <Button
        size="small"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        aria-controls="pipeline-stages"
        endIcon={<ExpandMoreIcon sx={{ transform: open ? "rotate(180deg)" : "none", transition: "transform 0.2s" }} />}
        sx={{ mt: 1.5, ml: -1.5 }}
      >
        {open ? "Hide stage details" : "Show stage details"}
      </Button>

      {/* position: relative keeps the visually hidden (absolute) spans inside the clipped panel when it is closed */}
      <Collapse in={open} id="pipeline-stages" sx={{ position: "relative" }}>
        <Box sx={{ display: "grid", gap: { xs: 2.5, md: 4 }, gridTemplateColumns: "repeat(auto-fit, minmax(min(100%, 300px), 1fr))", pt: 1.5 }}>
          {LANES.map((lane) => {
            const laneStages = stages.filter((s) => s.queue === lane.queue);
            if (laneStages.length === 0) return null;
            const laneCounted = laneStages.filter((s) => s.status !== "skipped");
            const laneDone = laneCounted.filter((s) => s.status === "succeeded" || s.status === "failed").length;
            const allSkipped = laneCounted.length === 0;
            return (
              <Box key={lane.queue} component="section" aria-labelledby={`lane-${lane.queue}`}>
                <Box sx={{ display: "flex", alignItems: "baseline", justifyContent: "space-between", gap: 1, pb: 0.75, mb: 0.75, borderBottom: `1px solid ${PALETTE.borderSubtle}` }}>
                  <Typography id={`lane-${lane.queue}`} component="h3" sx={{ fontWeight: 700, fontSize: "0.9rem", color: PALETTE.inkPrimary }}>
                    {lane.title}
                  </Typography>
                  <Typography variant="caption" sx={{ color: PALETTE.inkMuted, fontVariantNumeric: "tabular-nums" }}>
                    {allSkipped ? "Skipped" : `${laneDone}/${laneCounted.length}`}
                  </Typography>
                </Box>
                <Typography variant="caption" component="p" sx={{ color: PALETTE.inkSecondary, mb: 0.75 }}>
                  {allSkipped ? "Meridian is switched off for this run." : lane.detail}
                </Typography>
                {!allSkipped && (
                  <Box component="ol" sx={{ listStyle: "none", m: 0, p: 0 }}>
                    {laneStages.map((st) => (
                      <StageRow key={st.key} stage={st} now={now} />
                    ))}
                  </Box>
                )}
              </Box>
            );
          })}
        </Box>
        {parallel && (
          <Typography variant="caption" component="p" sx={{ mt: 1.5, color: PALETTE.inkMuted }}>
            The two queues run in parallel, so the total is less than the sum of the stage times.
          </Typography>
        )}
      </Collapse>
    </Paper>
  );
};
