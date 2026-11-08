import React, { useState } from "react";
import {
  Box,
  Chip,
  IconButton,
  Link,
  ListItemIcon,
  ListItemText,
  Menu,
  MenuItem,
  Paper,
  Skeleton,
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableRow,
  Tooltip,
  Typography,
} from "@mui/material";
import MoreHorizIcon from "@mui/icons-material/MoreHoriz";
import StarIcon from "@mui/icons-material/Star";
import StarOutlineIcon from "@mui/icons-material/StarOutline";
import DeleteOutlineIcon from "@mui/icons-material/DeleteOutline";
import TuneIcon from "@mui/icons-material/Tune";
import TimelineIcon from "@mui/icons-material/Timeline";
import { Link as RouterLink } from "react-router-dom";
import type { RunSummaryItem } from "../../api/types";
import { PALETTE } from "../../theme";
import { PROFILES, RUN_STATUS_LABEL, formatDateTime, runStatusColor } from "./format";
import { ExplainButton } from "../Explain/ExplainButton";

/** Where a run opens: a draft still needs its settings reviewed, every other run has a pipeline to look at. */
export const runHome = (run: Pick<RunSummaryItem, "status">): string => (run.status === "draft" ? "/setup" : "/status");

const samplingLabel = (r: RunSummaryItem) => (r.config?.skip_meridian ? "EDA and OLS only" : PROFILES[r.profile]?.label ?? r.profile);

const SettingsCell: React.FC<{ run: RunSummaryItem }> = ({ run }) => {
  const changes = run.settings_changes ?? [];
  if (changes.length === 0) {
    return (
      <Typography variant="caption" sx={{ color: PALETTE.inkSecondary }}>
        Default settings
      </Typography>
    );
  }
  const list = changes.map((c) => `${c.setting}: ${c.value}`).join("\n");
  return (
    <Tooltip title={<Box sx={{ whiteSpace: "pre-line" }}>{list}</Box>}>
      <Chip
        size="small"
        variant="outlined"
        color="primary"
        label={`Custom · ${changes.length}`}
        aria-label={`Custom settings: ${changes.length} change${changes.length === 1 ? "" : "s"} from the defaults`}
        tabIndex={0}
      />
    </Tooltip>
  );
};

const RowActions: React.FC<{
  run: RunSummaryItem;
  onSelect: (id: string) => void;
  onMakeDefault: (run: RunSummaryItem) => void;
  onDelete: (run: RunSummaryItem) => void;
}> = ({ run, onSelect, onMakeDefault, onDelete }) => {
  const [anchor, setAnchor] = useState<HTMLElement | null>(null);
  const menuId = `run-actions-${run.id}`;
  const close = () => setAnchor(null);
  return (
    <>
      <IconButton
        size="small"
        aria-label={`Actions for ${run.name}`}
        aria-haspopup="menu"
        aria-controls={anchor ? menuId : undefined}
        aria-expanded={anchor ? "true" : undefined}
        onClick={(e) => setAnchor(e.currentTarget)}
      >
        <MoreHorizIcon fontSize="small" />
      </IconButton>
      <Menu id={menuId} anchorEl={anchor} open={Boolean(anchor)} onClose={close} anchorOrigin={{ vertical: "bottom", horizontal: "right" }} transformOrigin={{ vertical: "top", horizontal: "right" }}>
        <MenuItem component={RouterLink} to="/setup" onClick={() => onSelect(run.id)}>
          <ListItemIcon>
            <TuneIcon fontSize="small" />
          </ListItemIcon>
          <ListItemText>Open run setup</ListItemText>
        </MenuItem>
        <MenuItem component={RouterLink} to="/status" onClick={() => onSelect(run.id)} disabled={run.status === "draft"}>
          <ListItemIcon>
            <TimelineIcon fontSize="small" />
          </ListItemIcon>
          <ListItemText>Open run status</ListItemText>
        </MenuItem>
        <MenuItem
          disabled={run.is_active}
          onClick={() => {
            close();
            onMakeDefault(run);
          }}
        >
          <ListItemIcon>{run.is_active ? <StarIcon fontSize="small" sx={{ color: PALETTE.brandPrimary }} /> : <StarOutlineIcon fontSize="small" />}</ListItemIcon>
          <ListItemText>{run.is_active ? "This is the default run" : "Make this the default run"}</ListItemText>
        </MenuItem>
        <MenuItem
          onClick={() => {
            close();
            onDelete(run);
          }}
          sx={{ color: PALETTE.semanticBad }}
        >
          <ListItemIcon>
            <DeleteOutlineIcon fontSize="small" sx={{ color: PALETTE.semanticBad }} />
          </ListItemIcon>
          <ListItemText>Delete run…</ListItemText>
        </MenuItem>
      </Menu>
    </>
  );
};

const HIDE_NARROW = { display: { xs: "none", md: "table-cell" } };

/** Every run of the studio. Opening a run selects it for the whole studio (the header switcher shows the same). */
export const RunTable: React.FC<{
  runs: RunSummaryItem[];
  loading: boolean;
  selectedRunId: string | null;
  onSelect: (id: string) => void;
  onMakeDefault: (run: RunSummaryItem) => void;
  onDelete: (run: RunSummaryItem) => void;
}> = ({ runs, loading, selectedRunId, onSelect, onMakeDefault, onDelete }) => (
  <Paper component="section" aria-labelledby="runs-heading" sx={{ overflow: "hidden" }}>
    <Box sx={{ px: { xs: 2, sm: 2.5 }, pt: 2, pb: 1.5, display: "flex", alignItems: "baseline", gap: 1 }}>
      <Typography id="runs-heading" variant="h3" component="h2">
        All runs
      </Typography>
      {!loading && (
        <Typography variant="body2" component="span" sx={{ color: PALETTE.inkMuted }}>
          {runs.length}
        </Typography>
      )}
      {!loading && runs.length > 0 && (
        <Box sx={{ ml: "auto", alignSelf: "center" }}>
          <ExplainButton kind="table" title="All runs" />
        </Box>
      )}
    </Box>

    <Table size="small" aria-labelledby="runs-heading" sx={{ "& td, & th": { borderColor: PALETTE.borderSubtle }, "& td": { py: 1.25 } }}>
      <TableHead>
        <TableRow sx={{ backgroundColor: PALETTE.bgSubtle }}>
          <TableCell scope="col">Run</TableCell>
          <TableCell scope="col">Status</TableCell>
          <TableCell scope="col" sx={HIDE_NARROW}>
            Sampling
          </TableCell>
          <TableCell scope="col" sx={HIDE_NARROW}>
            Settings
          </TableCell>
          <TableCell scope="col" sx={HIDE_NARROW}>
            Created
          </TableCell>
          <TableCell scope="col" align="right">
            <span className="visually-hidden">Actions</span>
          </TableCell>
        </TableRow>
      </TableHead>
      <TableBody>
        {loading &&
          [0, 1].map((i) => (
            <TableRow key={i}>
              <TableCell colSpan={6}>
                <Skeleton width="45%" />
              </TableCell>
            </TableRow>
          ))}
        {runs.map((r) => {
          const selected = r.id === selectedRunId;
          return (
            <TableRow key={r.id} sx={{ backgroundColor: selected ? PALETTE.bgAccentTint : undefined, "& > th": { boxShadow: selected ? `inset 3px 0 0 ${PALETTE.brandPrimary}` : undefined } }}>
              <TableCell component="th" scope="row" sx={{ minWidth: 0, maxWidth: 360 }}>
                <Link
                  component={RouterLink}
                  to={runHome(r)}
                  onClick={() => onSelect(r.id)}
                  aria-current={selected ? "true" : undefined}
                  underline="hover"
                  sx={{ fontWeight: 700, fontSize: "0.9rem", color: PALETTE.inkPrimary, overflowWrap: "anywhere" }}
                >
                  {r.name}
                </Link>
                <Typography component="span" variant="caption" sx={{ display: "flex", alignItems: "center", flexWrap: "wrap", columnGap: 0.75, color: PALETTE.inkSecondary }}>
                  <span>{r.filename}</span>
                  <Box component="span" sx={{ display: { md: "none" } }}>
                    · {samplingLabel(r)} · {formatDateTime(r.created_at)}
                  </Box>
                  {r.is_active && (
                    <Box component="span" sx={{ display: "inline-flex", alignItems: "center", gap: 0.25, color: PALETTE.brandPrimary, fontWeight: 700 }}>
                      <StarIcon aria-hidden="true" sx={{ fontSize: 13 }} />
                      Default
                    </Box>
                  )}
                </Typography>
              </TableCell>
              <TableCell>
                <Chip size="small" color={runStatusColor(r.status)} label={RUN_STATUS_LABEL[r.status] ?? r.status} />
              </TableCell>
              <TableCell sx={{ ...HIDE_NARROW, whiteSpace: "nowrap" }}>{samplingLabel(r)}</TableCell>
              <TableCell sx={HIDE_NARROW}>
                <SettingsCell run={r} />
              </TableCell>
              <TableCell sx={{ ...HIDE_NARROW, whiteSpace: "nowrap", color: PALETTE.inkSecondary }}>{formatDateTime(r.created_at)}</TableCell>
              <TableCell align="right" sx={{ width: 48 }}>
                <RowActions run={r} onSelect={onSelect} onMakeDefault={onMakeDefault} onDelete={onDelete} />
              </TableCell>
            </TableRow>
          );
        })}
      </TableBody>
    </Table>

    {runs.some((r) => r.is_active) && (
      <Typography variant="caption" component="p" sx={{ px: { xs: 2, sm: 2.5 }, py: 1.25, color: PALETTE.inkSecondary, backgroundColor: PALETTE.bgCanvas }}>
        The default run is what visitors see until they pick another one.
      </Typography>
    )}
  </Paper>
);
