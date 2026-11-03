import React, { useState } from "react";
import { Box, Chip, IconButton, ListItemIcon, ListItemText, Menu, MenuItem, Paper, Step, StepLabel, Stepper, Tooltip, Typography } from "@mui/material";
import MoreHorizIcon from "@mui/icons-material/MoreHoriz";
import StarIcon from "@mui/icons-material/Star";
import StarOutlineIcon from "@mui/icons-material/StarOutline";
import DeleteOutlineIcon from "@mui/icons-material/DeleteOutline";
import InsertDriveFileOutlinedIcon from "@mui/icons-material/InsertDriveFileOutlined";
import ScheduleIcon from "@mui/icons-material/Schedule";
import TuneIcon from "@mui/icons-material/Tune";
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

/** Where the run is in its life: uploaded → reviewed → pipeline → results. Each step reflects the real run status. */
const Lifecycle: React.FC<{ run: RunDetail }> = ({ run }) => {
  const { status } = run;
  const counted = run.stages.filter((s) => s.status !== "skipped");
  const done = counted.filter((s) => s.status === "succeeded" || s.status === "failed").length;
  const active = status === "draft" ? 1 : status === "succeeded" ? 4 : 2;
  const runLabel =
    status === "running" ? `Running · ${done}/${counted.length}` : status === "queued" ? "Queued" : status === "failed" ? "Failed" : status === "cancelled" ? "Cancelled" : "Run pipeline";
  return (
    <Stepper activeStep={active} alternativeLabel sx={{ mt: 2.5, "& .MuiStepLabel-label": { fontSize: "0.8rem", mt: "6px !important" } }}>
      <Step completed>
        <StepLabel>Upload</StepLabel>
      </Step>
      <Step completed={status !== "draft"}>
        <StepLabel>Review settings</StepLabel>
      </Step>
      <Step completed={status === "succeeded"}>
        <StepLabel error={status === "failed"}>{runLabel}</StepLabel>
      </Step>
      <Step completed={status === "succeeded"}>
        <StepLabel>Results</StepLabel>
      </Step>
    </Stepper>
  );
};

export const RunHeader = React.forwardRef<
  HTMLHeadingElement,
  { run: RunDetail; onMakeDefault: () => void; onDelete: () => void }
>(({ run, onMakeDefault, onDelete }, headingRef) => {
  const [menuAnchor, setMenuAnchor] = useState<HTMLElement | null>(null);
  const menuId = `run-actions-${run.id}`;
  const close = () => setMenuAnchor(null);

  return (
    <Paper component="section" aria-labelledby="run-heading" sx={{ p: { xs: 2.5, sm: 3 }, scrollMarginTop: 84 }}>
      <Box sx={{ display: "flex", alignItems: "flex-start", gap: 2 }}>
        <Box sx={{ flexGrow: 1, minWidth: 0 }}>
          <Typography variant="caption" sx={{ display: "block", fontWeight: 800, letterSpacing: "0.08em", textTransform: "uppercase", color: PALETTE.brandGoldDark }}>
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
              {run.config?.skip_meridian ? "EDA and OLS only" : `${PROFILES[run.profile]?.label ?? run.profile} sampling`}
            </Meta>
            {run.is_active && <Meta icon={<StarIcon sx={{ color: PALETTE.brandGold }} />}>Default run</Meta>}
          </Box>
        </Box>

        <Tooltip title="Run actions">
          <IconButton
            aria-label={`Actions for ${run.name}`}
            aria-haspopup="menu"
            aria-controls={menuAnchor ? menuId : undefined}
            aria-expanded={menuAnchor ? "true" : undefined}
            onClick={(e) => setMenuAnchor(e.currentTarget)}
            sx={{ border: `1px solid ${PALETTE.borderSubtle}` }}
          >
            <MoreHorizIcon />
          </IconButton>
        </Tooltip>
        <Menu id={menuId} anchorEl={menuAnchor} open={Boolean(menuAnchor)} onClose={close} anchorOrigin={{ vertical: "bottom", horizontal: "right" }} transformOrigin={{ vertical: "top", horizontal: "right" }}>
          <MenuItem
            disabled={run.is_active}
            onClick={() => {
              close();
              onMakeDefault();
            }}
          >
            <ListItemIcon>{run.is_active ? <StarIcon fontSize="small" sx={{ color: PALETTE.brandGold }} /> : <StarOutlineIcon fontSize="small" />}</ListItemIcon>
            <ListItemText>{run.is_active ? "This is the default run" : "Make this the default run"}</ListItemText>
          </MenuItem>
          <MenuItem
            onClick={() => {
              close();
              onDelete();
            }}
            sx={{ color: PALETTE.semanticBad }}
          >
            <ListItemIcon>
              <DeleteOutlineIcon fontSize="small" sx={{ color: PALETTE.semanticBad }} />
            </ListItemIcon>
            <ListItemText>Delete run…</ListItemText>
          </MenuItem>
        </Menu>
      </Box>

      <Lifecycle run={run} />
    </Paper>
  );
});
RunHeader.displayName = "RunHeader";
