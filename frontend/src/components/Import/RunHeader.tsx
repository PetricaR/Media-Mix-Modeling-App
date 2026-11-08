import React from "react";
import { Box, Chip, Link, Paper, Step, StepButton, StepLabel, Stepper, Typography } from "@mui/material";
import StarIcon from "@mui/icons-material/Star";
import InsertDriveFileOutlinedIcon from "@mui/icons-material/InsertDriveFileOutlined";
import ScheduleIcon from "@mui/icons-material/Schedule";
import TuneIcon from "@mui/icons-material/Tune";
import { Link as RouterLink } from "react-router-dom";
import type { RunDetail } from "../../api/types";
import { PALETTE } from "../../theme";
import { PROFILES, RUN_STATUS_LABEL, formatDateTime, runStatusColor } from "./format";

const Meta: React.FC<{ icon: React.ReactNode; children: React.ReactNode }> = ({ icon, children }) => (
  <Box component="li" sx={{ display: "inline-flex", alignItems: "center", gap: 0.5, minWidth: 0, color: PALETTE.inkSecondary, fontSize: "0.82rem" }}>
    <Box component="span" aria-hidden="true" sx={{ display: "inline-flex", color: PALETTE.inkMuted, "& svg": { fontSize: 16 } }}>
      {icon}
    </Box>
    <Box component="span" sx={{ minWidth: 0, overflowWrap: "anywhere" }}>
      {children}
    </Box>
  </Box>
);

/**
 * Where the run is in its life: uploaded → reviewed → pipeline → results. Each step reflects the real run status and
 * links to the page for that step.
 */
const Lifecycle: React.FC<{ run: RunDetail }> = ({ run }) => {
  const { status } = run;
  const counted = run.stages.filter((s) => s.status !== "skipped");
  const done = counted.filter((s) => s.status === "succeeded" || s.status === "failed").length;
  const active = status === "draft" ? 1 : status === "succeeded" ? 4 : 2;
  const runLabel =
    status === "running" ? `Running · ${done}/${counted.length}` : status === "queued" ? "Queued" : status === "failed" ? "Failed" : status === "cancelled" ? "Cancelled" : "Run pipeline";
  const steps = [
    { label: "Upload", to: "/runs", completed: true },
    { label: "Review settings", to: "/setup", completed: status !== "draft" },
    { label: runLabel, to: "/status", completed: status === "succeeded", error: status === "failed", current: true },
    { label: "Results", to: "/overview", completed: status === "succeeded" },
  ];
  return (
    <Stepper nonLinear activeStep={active} alternativeLabel sx={{ mt: 2.5, "& .MuiStepLabel-label": { fontSize: "0.8rem", mt: "6px !important" } }}>
      {steps.map((st) => (
        <Step key={st.to} completed={st.completed}>
          <StepButton component={RouterLink} to={st.to} aria-current={st.current ? "step" : undefined} sx={{ borderRadius: 1 }}>
            <StepLabel error={st.error}>{st.label}</StepLabel>
          </StepButton>
        </Step>
      ))}
    </Stepper>
  );
};

export const RunHeader = React.forwardRef<HTMLHeadingElement, { run: RunDetail }>(({ run }, headingRef) => {
  const changes = run.settings_changes ?? [];
  return (
    <Paper component="section" aria-labelledby="run-heading" sx={{ p: { xs: 2.5, sm: 3 }, scrollMarginTop: 84 }}>
      <Typography variant="caption" sx={{ display: "block", fontWeight: 800, letterSpacing: "0.08em", textTransform: "uppercase", color: PALETTE.brandPrimary }}>
        Selected run
      </Typography>
      <Box sx={{ display: "flex", alignItems: "center", flexWrap: "wrap", gap: 1.25, mt: 0.25 }}>
        <Typography id="run-heading" ref={headingRef} tabIndex={-1} variant="h2" component="h2" sx={{ overflowWrap: "anywhere", "&:focus": { outline: "none" } }}>
          {run.name}
        </Typography>
        <Chip size="small" color={runStatusColor(run.status)} label={RUN_STATUS_LABEL[run.status] ?? run.status} />
      </Box>
      <Box component="ul" sx={{ listStyle: "none", p: 0, m: 0, mt: 1, display: "flex", flexWrap: "wrap", columnGap: 2, rowGap: 0.5 }}>
        <Meta icon={<InsertDriveFileOutlinedIcon />}>{run.filename}</Meta>
        <Meta icon={<ScheduleIcon />}>Created {formatDateTime(run.created_at)}</Meta>
        <Meta icon={<TuneIcon />}>
          {run.config?.skip_meridian ? "EDA and OLS only" : `${PROFILES[run.profile]?.label ?? run.profile} sampling`} ·{" "}
          <Link component={RouterLink} to="/setup">
            {changes.length === 0 ? "default settings" : `${changes.length} custom setting${changes.length === 1 ? "" : "s"}`}
          </Link>
        </Meta>
        {run.is_active && <Meta icon={<StarIcon sx={{ color: PALETTE.brandPrimary }} />}>Default run</Meta>}
      </Box>

      <Lifecycle run={run} />
    </Paper>
  );
});
RunHeader.displayName = "RunHeader";
