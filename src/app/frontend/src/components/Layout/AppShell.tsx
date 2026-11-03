import React, { useEffect, useRef, useState } from "react";
import {
  AppBar,
  Box,
  Button,
  Chip,
  Dialog,
  DialogActions,
  DialogContent,
  DialogContentText,
  DialogTitle,
  Divider,
  Drawer,
  FormControl,
  IconButton,
  InputLabel,
  LinearProgress,
  List,
  ListItem,
  ListItemButton,
  ListItemIcon,
  ListItemText,
  ListSubheader,
  MenuItem,
  Select,
  Stack,
  TextField,
  Toolbar,
  Tooltip,
  Typography,
  useMediaQuery,
  useTheme,
} from "@mui/material";
import MenuIcon from "@mui/icons-material/Menu";
import UploadFileIcon from "@mui/icons-material/UploadFile";
import DashboardOutlinedIcon from "@mui/icons-material/DashboardOutlined";
import FactCheckOutlinedIcon from "@mui/icons-material/FactCheckOutlined";
import InsightsOutlinedIcon from "@mui/icons-material/InsightsOutlined";
import BuildCircleOutlinedIcon from "@mui/icons-material/BuildCircleOutlined";
import HubOutlinedIcon from "@mui/icons-material/HubOutlined";
import TrendingUpOutlinedIcon from "@mui/icons-material/TrendingUpOutlined";
import CompareArrowsOutlinedIcon from "@mui/icons-material/CompareArrowsOutlined";
import TuneOutlinedIcon from "@mui/icons-material/TuneOutlined";
import TimelineOutlinedIcon from "@mui/icons-material/TimelineOutlined";
import DescriptionOutlinedIcon from "@mui/icons-material/DescriptionOutlined";
import ArticleOutlinedIcon from "@mui/icons-material/ArticleOutlined";
import LockOutlinedIcon from "@mui/icons-material/LockOutlined";
import CancelOutlinedIcon from "@mui/icons-material/CancelOutlined";
import { Link as RouterLink, useLocation, useNavigate } from "react-router-dom";
import { useQueryClient } from "@tanstack/react-query";
import { api, getAccessCode, onAuthRequired, setAccessCode } from "../../api/client";
import { isRunActive, useRun, useRuns } from "../../api/hooks";
import { PALETTE } from "../../theme";
import { BrandMark } from "./BrandMark";

const DRAWER_WIDTH = 256;
const HEADER_HEIGHT = 60;

interface NavGroup {
  header: string;
  items: { label: string; path: string; icon: React.ReactNode }[];
}

const NAV_GROUPS: NavGroup[] = [
  {
    header: "Run",
    items: [
      { label: "Import & runs", path: "/import", icon: <UploadFileIcon fontSize="small" /> },
      { label: "Executive overview", path: "/overview", icon: <DashboardOutlinedIcon fontSize="small" /> },
    ],
  },
  {
    header: "Data & EDA",
    items: [
      { label: "Data quality", path: "/data-quality", icon: <FactCheckOutlinedIcon fontSize="small" /> },
      { label: "Exploratory analysis", path: "/explore", icon: <InsightsOutlinedIcon fontSize="small" /> },
      { label: "Data preparation", path: "/preparation", icon: <BuildCircleOutlinedIcon fontSize="small" /> },
    ],
  },
  {
    header: "Modelling",
    items: [
      { label: "Meridian model", path: "/model", icon: <HubOutlinedIcon fontSize="small" /> },
      { label: "Drivers & ROI", path: "/drivers", icon: <TrendingUpOutlinedIcon fontSize="small" /> },
      { label: "Benchmark vs OLS", path: "/benchmark", icon: <CompareArrowsOutlinedIcon fontSize="small" /> },
    ],
  },
  {
    header: "Decisions",
    items: [
      { label: "Budget optimiser", path: "/optimizer", icon: <TuneOutlinedIcon fontSize="small" /> },
      { label: "Forecast & scenarios", path: "/forecast", icon: <TimelineOutlinedIcon fontSize="small" /> },
    ],
  },
  {
    header: "Deliverables",
    items: [
      { label: "Reports & downloads", path: "/reports", icon: <DescriptionOutlinedIcon fontSize="small" /> },
      { label: "Answers to the brief", path: "/brief", icon: <ArticleOutlinedIcon fontSize="small" /> },
    ],
  },
];

const STATUS_LABEL: Record<string, string> = {
  draft: "Draft",
  queued: "Queued",
  running: "Running",
  succeeded: "Finished",
  failed: "Failed",
  cancelled: "Cancelled",
};

const statusColor = (status?: string): "default" | "success" | "warning" | "error" | "info" => {
  switch (status) {
    case "succeeded":
      return "success";
    case "running":
    case "queued":
      return "info";
    case "failed":
      return "error";
    case "cancelled":
      return "warning";
    default:
      return "default";
  }
};

export const AppShell: React.FC<{
  selectedRunId: string | null;
  onSelectRunId: (id: string | null) => void;
  children: React.ReactNode;
}> = ({ selectedRunId, onSelectRunId, children }) => {
  const location = useLocation();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const desktop = useMediaQuery(useTheme().breakpoints.up("lg"), { noSsr: true });
  const [mobileOpen, setMobileOpen] = useState(false);

  const { data: runsData } = useRuns();
  const runs = runsData?.runs || [];

  // Fall back to the active (or newest) run when nothing is selected or the stored run no longer exists
  useEffect(() => {
    if (!runsData) return;
    if (selectedRunId && runs.some((r) => r.id === selectedRunId)) return;
    const fallback = runs.find((r) => r.is_active) || runs[0];
    onSelectRunId(fallback ? fallback.id : null);
  }, [runsData, runs, selectedRunId, onSelectRunId]);

  const { data: currentRun } = useRun(selectedRunId);

  // Announce status changes politely and refresh everything once a run reaches a final state
  const [announcement, setAnnouncement] = useState("");
  const lastStatus = useRef<string | undefined>(undefined);
  useEffect(() => {
    const st = currentRun?.status;
    if (!st) return;
    if (lastStatus.current && lastStatus.current !== st) {
      setAnnouncement(`Run ${currentRun?.name}: ${STATUS_LABEL[st] ?? st}`);
      if (!isRunActive(currentRun)) {
        for (const key of ["section", "sections", "artifacts", "forecasts", "runs"]) {
          queryClient.invalidateQueries({ queryKey: [key] });
        }
      }
    }
    lastStatus.current = st;
  }, [currentRun, queryClient]);

  const [authDialogOpen, setAuthDialogOpen] = useState(false);
  const [codeInput, setCodeInput] = useState(getAccessCode());
  const [confirmCancel, setConfirmCancel] = useState(false);
  const [cancelError, setCancelError] = useState<string | null>(null);

  useEffect(
    () =>
      onAuthRequired(() => {
        setCodeInput(getAccessCode());
        setAuthDialogOpen(true);
      }),
    []
  );

  useEffect(() => setMobileOpen(false), [location.pathname]);

  const handleSaveCode = (e: React.FormEvent) => {
    e.preventDefault();
    setAccessCode(codeInput.trim());
    setAuthDialogOpen(false);
  };

  const handleCancelRun = async () => {
    if (!selectedRunId) return;
    setCancelError(null);
    try {
      await api.cancelRun(selectedRunId);
      setConfirmCancel(false);
    } catch (err) {
      setCancelError(err instanceof Error ? err.message : "Could not cancel the run");
    }
    queryClient.invalidateQueries({ queryKey: ["run", selectedRunId] });
    queryClient.invalidateQueries({ queryKey: ["runs"] });
  };

  const stages = currentRun?.stages ?? [];
  const counted = stages.filter((s) => s.status !== "skipped");
  const completedStages = counted.filter((s) => s.status === "succeeded" || s.status === "failed").length;
  const runningStage = stages.find((s) => s.status === "running");
  const queuePos =
    typeof currentRun?.queue_position === "number"
      ? currentRun.queue_position
      : currentRun?.queue_position?.meridian ?? currentRun?.queue_position?.analysis ?? null;
  const active = isRunActive(currentRun);

  const chipLabel = !currentRun
    ? ""
    : currentRun.status === "running" && runningStage
    ? `${runningStage.label} · ${completedStages}/${counted.length}`
    : currentRun.status === "queued" && queuePos && queuePos > 0
    ? `Queued · #${queuePos}`
    : STATUS_LABEL[currentRun.status] ?? currentRun.status;

  const nav = (
    <Box component="nav" aria-label="Main" sx={{ display: "flex", flexDirection: "column", height: "100%" }}>
      <Box sx={{ px: 2.5, py: 2, borderBottom: `1px solid ${PALETTE.borderSubtle}`, minHeight: HEADER_HEIGHT, display: "flex", alignItems: "center" }}>
        <BrandMark />
      </Box>
      <Box sx={{ overflowY: "auto", flexGrow: 1, py: 1 }}>
        {NAV_GROUPS.map((group, gIdx) => (
          <React.Fragment key={group.header}>
            {gIdx > 0 && <Divider sx={{ my: 1, mx: 2 }} />}
            <List
              dense
              aria-labelledby={`nav-${gIdx}`}
              subheader={
                <ListSubheader
                  id={`nav-${gIdx}`}
                  disableSticky
                  sx={{
                    lineHeight: "28px",
                    fontSize: "0.7rem",
                    fontWeight: 800,
                    textTransform: "uppercase",
                    letterSpacing: "0.08em",
                    color: PALETTE.brandGoldDark,
                    backgroundColor: "transparent",
                  }}
                >
                  {group.header}
                </ListSubheader>
              }
            >
              {group.items.map((item) => {
                const isCurrent = location.pathname.startsWith(item.path);
                return (
                  <ListItem key={item.path} disablePadding>
                    <ListItemButton
                      component={RouterLink}
                      to={item.path}
                      selected={isCurrent}
                      aria-current={isCurrent ? "page" : undefined}
                      sx={{
                        mx: 1,
                        borderRadius: 999,
                        mb: 0.25,
                        minHeight: 40,
                        "&.Mui-selected": {
                          backgroundColor: PALETTE.bgGoldTint,
                          color: PALETTE.brandNavy,
                          "& .MuiListItemIcon-root": { color: PALETTE.brandGold },
                        },
                        "&.Mui-selected:hover": { backgroundColor: PALETTE.bgSubtle },
                      }}
                    >
                      <ListItemIcon sx={{ minWidth: 34, color: PALETTE.inkSecondary }}>{item.icon}</ListItemIcon>
                      <ListItemText primary={item.label} primaryTypographyProps={{ fontSize: "0.88rem", fontWeight: isCurrent ? 700 : 550 }} />
                    </ListItemButton>
                  </ListItem>
                );
              })}
            </List>
          </React.Fragment>
        ))}
      </Box>
      <Box sx={{ px: 2.5, py: 1.75, borderTop: `1px solid ${PALETTE.borderSubtle}` }}>
        <Typography variant="caption" sx={{ color: PALETTE.inkMuted, display: "block", lineHeight: 1.4 }}>
          Independent case-study prototype, not an official Tchibo product.
        </Typography>
      </Box>
    </Box>
  );

  return (
    <Box sx={{ display: "flex", minHeight: "100dvh", backgroundColor: PALETTE.bgCanvas }}>
      <a className="skip-link" href="#main-content">
        Skip to main content
      </a>
      <Box className="visually-hidden" aria-live="polite" role="status">
        {announcement}
      </Box>

      <AppBar
        position="fixed"
        color="inherit"
        elevation={0}
        sx={{
          width: { lg: `calc(100% - ${DRAWER_WIDTH}px)` },
          ml: { lg: `${DRAWER_WIDTH}px` },
          minHeight: HEADER_HEIGHT,
          justifyContent: "center",
          backgroundColor: "rgba(255, 255, 255, 0.94)",
          backdropFilter: "blur(8px)",
          borderBottom: `1px solid ${PALETTE.borderSubtle}`,
        }}
      >
        <Toolbar sx={{ minHeight: `${HEADER_HEIGHT}px !important`, gap: 1.5, px: { xs: 1.5, sm: 2.5 } }}>
          {!desktop && (
            <IconButton edge="start" aria-label="Open navigation" onClick={() => setMobileOpen(true)}>
              <MenuIcon />
            </IconButton>
          )}
          {!desktop && (
            <Box sx={{ display: { xs: "none", sm: "block" } }}>
              <BrandMark compact />
            </Box>
          )}
          <Stack direction="row" alignItems="center" spacing={1.25} sx={{ flexGrow: 1, minWidth: 0 }}>
            <FormControl size="small" sx={{ minWidth: { xs: 140, sm: 220 }, maxWidth: 360, flexShrink: 1 }}>
              <InputLabel id="run-select-label" shrink>
                Run
              </InputLabel>
              <Select
                labelId="run-select-label"
                label="Run"
                notched
                value={runs.some((r) => r.id === selectedRunId) ? selectedRunId ?? "" : ""}
                displayEmpty
                onChange={(e) => onSelectRunId((e.target.value as string) || null)}
                sx={{ fontSize: "0.86rem", backgroundColor: "#FFFFFF" }}
                renderValue={(val) => {
                  const r = runs.find((x) => x.id === val);
                  return r ? r.name : "No run yet";
                }}
              >
                {runs.length === 0 && (
                  <MenuItem value="" disabled>
                    No runs yet — upload the workbook
                  </MenuItem>
                )}
                {runs.map((r) => (
                  <MenuItem key={r.id} value={r.id}>
                    <Stack direction="row" spacing={1} alignItems="center" sx={{ width: "100%" }}>
                      <Box sx={{ flexGrow: 1, minWidth: 0, overflow: "hidden", textOverflow: "ellipsis" }}>
                        {r.name}
                        <Typography component="span" variant="caption" sx={{ ml: 1, color: PALETTE.inkMuted }}>
                          {r.profile}
                          {r.is_active ? " · default" : ""}
                        </Typography>
                      </Box>
                      <Chip size="small" color={statusColor(r.status)} label={STATUS_LABEL[r.status] ?? r.status} />
                    </Stack>
                  </MenuItem>
                ))}
              </Select>
            </FormControl>

            {currentRun && (
              <Chip
                size="small"
                color={statusColor(currentRun.status)}
                label={chipLabel}
                sx={{ maxWidth: { xs: 130, sm: 340 }, display: { xs: active ? "inline-flex" : "none", sm: "inline-flex" } }}
              />
            )}

            {currentRun && active && (
              <Tooltip title="Cancel this run">
                <IconButton size="small" color="warning" aria-label="Cancel this run" onClick={() => setConfirmCancel(true)}>
                  <CancelOutlinedIcon fontSize="small" />
                </IconButton>
              </Tooltip>
            )}
          </Stack>

          <Tooltip title="Access code for uploads and computations">
            <IconButton size="small" aria-label="Set access code" onClick={() => setAuthDialogOpen(true)}>
              <LockOutlinedIcon fontSize="small" />
            </IconButton>
          </Tooltip>
          <Button
            variant="contained"
            color="secondary"
            size="small"
            startIcon={<UploadFileIcon fontSize="small" />}
            onClick={() => navigate("/import")}
            sx={{ display: { xs: "none", sm: "inline-flex" } }}
          >
            Upload workbook
          </Button>
        </Toolbar>
        {currentRun && active && (
          <LinearProgress
            color="secondary"
            variant={counted.length > 0 && completedStages > 0 ? "determinate" : "indeterminate"}
            value={counted.length > 0 ? (completedStages / counted.length) * 100 : 0}
            aria-label="Pipeline progress"
            sx={{ height: 3 }}
          />
        )}
      </AppBar>

      {desktop ? (
        <Drawer
          variant="permanent"
          sx={{
            width: DRAWER_WIDTH,
            flexShrink: 0,
            "& .MuiDrawer-paper": { width: DRAWER_WIDTH, boxSizing: "border-box", backgroundColor: "#FFFFFF", borderRight: `1px solid ${PALETTE.borderSubtle}` },
          }}
        >
          {nav}
        </Drawer>
      ) : (
        <Drawer
          variant="temporary"
          open={mobileOpen}
          onClose={() => setMobileOpen(false)}
          ModalProps={{ keepMounted: true }}
          sx={{ "& .MuiDrawer-paper": { width: DRAWER_WIDTH, boxSizing: "border-box" } }}
        >
          {nav}
        </Drawer>
      )}

      <Box
        component="main"
        id="main-content"
        tabIndex={-1}
        sx={{
          flexGrow: 1,
          minWidth: 0,
          p: { xs: 2, md: 3.5 },
          mt: `${HEADER_HEIGHT}px`,
          maxWidth: 1440,
          width: "100%",
          mx: "auto",
          "&:focus": { outline: "none" },
        }}
      >
        {children}
      </Box>

      <Dialog open={confirmCancel} onClose={() => setConfirmCancel(false)} maxWidth="xs" fullWidth>
        <DialogTitle>Cancel this run?</DialogTitle>
        <DialogContent>
          <DialogContentText>
            The workers stop “{currentRun?.name}” after the current step. Results already computed stay available; the
            run can be started again from the Import page.
          </DialogContentText>
          {cancelError && (
            <Typography role="alert" color="error" variant="body2" sx={{ mt: 1.5 }}>
              {cancelError}
            </Typography>
          )}
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setConfirmCancel(false)} autoFocus>
            Keep running
          </Button>
          <Button color="warning" variant="contained" onClick={handleCancelRun}>
            Cancel run
          </Button>
        </DialogActions>
      </Dialog>

      <Dialog open={authDialogOpen} onClose={() => setAuthDialogOpen(false)} maxWidth="xs" fullWidth>
        <form onSubmit={handleSaveCode}>
          <DialogTitle>Access code</DialogTitle>
          <DialogContent>
            <DialogContentText sx={{ mb: 2 }}>
              Viewing runs is open. Uploading workbooks, starting fits and running scenarios need the studio access code
              when the server has one configured. It is kept for this browser tab only.
            </DialogContentText>
            <TextField
              fullWidth
              size="small"
              type="password"
              label="Access code"
              autoComplete="current-password"
              value={codeInput}
              onChange={(e) => setCodeInput(e.target.value)}
              autoFocus
            />
          </DialogContent>
          <DialogActions>
            <Button onClick={() => setAuthDialogOpen(false)}>Close</Button>
            <Button type="submit" variant="contained">
              Save
            </Button>
          </DialogActions>
        </form>
      </Dialog>
    </Box>
  );
};
