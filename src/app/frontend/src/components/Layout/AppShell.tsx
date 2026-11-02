import React, { useEffect, useState } from "react";
import {
  AppBar,
  Box,
  Button,
  Chip,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  Divider,
  Drawer,
  FormControl,
  IconButton,
  LinearProgress,
  List,
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
} from "@mui/material";
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
import { useLocation, useNavigate } from "react-router-dom";
import { api, getAccessCode, onAuthRequired, setAccessCode } from "../../api/client";
import { useRun, useRuns } from "../../api/hooks";
import { PALETTE } from "../../theme";
import { useQueryClient } from "@tanstack/react-query";

const DRAWER_WIDTH = 248;

interface NavGroup {
  header: string;
  items: { label: string; path: string; icon: React.ReactNode }[];
}

const NAV_GROUPS: NavGroup[] = [
  {
    header: "Run",
    items: [
      { label: "Import & Runs", path: "/import", icon: <UploadFileIcon fontSize="small" /> },
      { label: "Executive Overview", path: "/overview", icon: <DashboardOutlinedIcon fontSize="small" /> },
    ],
  },
  {
    header: "Data & EDA",
    items: [
      { label: "Data Quality & Audit", path: "/data-quality", icon: <FactCheckOutlinedIcon fontSize="small" /> },
      { label: "Exploratory Analysis", path: "/explore", icon: <InsightsOutlinedIcon fontSize="small" /> },
      { label: "Feature Preparation", path: "/preparation", icon: <BuildCircleOutlinedIcon fontSize="small" /> },
    ],
  },
  {
    header: "Modelling",
    items: [
      { label: "Meridian Bayesian MMM", path: "/model", icon: <HubOutlinedIcon fontSize="small" /> },
      { label: "Drivers, ROI & Curves", path: "/drivers", icon: <TrendingUpOutlinedIcon fontSize="small" /> },
      { label: "Benchmark vs OLS", path: "/benchmark", icon: <CompareArrowsOutlinedIcon fontSize="small" /> },
    ],
  },
  {
    header: "Decisioning",
    items: [
      { label: "Budget Optimiser", path: "/optimizer", icon: <TuneOutlinedIcon fontSize="small" /> },
      { label: "Forecast & Scenarios", path: "/forecast", icon: <TimelineOutlinedIcon fontSize="small" /> },
    ],
  },
  {
    header: "Deliverables",
    items: [
      { label: "Reports & Downloads", path: "/reports", icon: <DescriptionOutlinedIcon fontSize="small" /> },
      { label: "Management Brief", path: "/brief", icon: <ArticleOutlinedIcon fontSize="small" /> },
    ],
  },
];

export const AppShell: React.FC<{
  selectedRunId: string | null;
  onSelectRunId: (id: string | null) => void;
  children: React.ReactNode;
}> = ({ selectedRunId, onSelectRunId, children }) => {
  const location = useLocation();
  const navigate = useNavigate();
  const queryClient = useQueryClient();

  const { data: runsData } = useRuns();
  const runs = runsData?.runs || [];

  useEffect(() => {
    if (!selectedRunId && runs.length > 0) {
      const active = runs.find((r) => r.is_active) || runs[0];
      onSelectRunId(active.id);
    }
  }, [runs, selectedRunId, onSelectRunId]);

  const { data: currentRun } = useRun(selectedRunId);

  const [authDialogOpen, setAuthDialogOpen] = useState(false);
  const [codeInput, setCodeInput] = useState(getAccessCode());

  useEffect(() => {
    return onAuthRequired(() => {
      setCodeInput(getAccessCode());
      setAuthDialogOpen(true);
    });
  }, []);

  const handleSaveCode = () => {
    setAccessCode(codeInput.trim());
    setAuthDialogOpen(false);
  };

  const handleCancelRun = async () => {
    if (!selectedRunId) return;
    await api.cancelRun(selectedRunId);
    queryClient.invalidateQueries({ queryKey: ["run", selectedRunId] });
    queryClient.invalidateQueries({ queryKey: ["runs"] });
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

  const completedStages = currentRun?.stages?.filter((s) => s.status === "succeeded").length || 0;
  const totalStages = currentRun?.stages?.filter((s) => s.status !== "skipped").length || 0;
  const runningStage = currentRun?.stages?.find((s) => s.status === "running");

  return (
    <Box sx={{ display: "flex", minHeight: "100vh", backgroundColor: PALETTE.bgCanvas }}>
      <AppBar
        position="fixed"
        color="inherit"
        elevation={0}
        sx={{
          width: { md: `calc(100% - ${DRAWER_WIDTH}px)` },
          ml: { md: `${DRAWER_WIDTH}px` },
          height: 56,
          justifyContent: "center",
          backgroundColor: "rgba(255, 255, 255, 0.92)",
          backdropFilter: "blur(8px)",
          borderBottom: `1px solid ${PALETTE.borderSubtle}`,
        }}
      >
        <Toolbar variant="dense" sx={{ justifyContent: "space-between", gap: 2 }}>
          <Stack direction="row" alignItems="center" spacing={1.5}>
            <Typography variant="body2" sx={{ fontWeight: 600, color: PALETTE.inkSecondary }}>
              Active Run:
            </Typography>
            <FormControl size="small" sx={{ minWidth: 240 }}>
              <Select
                value={selectedRunId || ""}
                displayEmpty
                onChange={(e) => onSelectRunId(e.target.value || null)}
                sx={{ fontSize: "0.85rem", backgroundColor: "#FFFFFF" }}
              >
                {runs.length === 0 && (
                  <MenuItem value="" disabled>
                    No runs yet — upload workbook
                  </MenuItem>
                )}
                {runs.map((r) => (
                  <MenuItem key={r.id} value={r.id}>
                    {r.name} ({r.profile}) — {r.status}
                  </MenuItem>
                ))}
              </Select>
            </FormControl>

            {currentRun && (
              <Chip
                size="small"
                color={statusColor(currentRun.status)}
                label={
                  currentRun.status === "running" && runningStage
                    ? `Running: ${runningStage.label} (${completedStages}/${totalStages})`
                    : currentRun.status === "queued" &&
                      currentRun.queue_position?.meridian &&
                      currentRun.queue_position.meridian > 0
                    ? `Queued (#${currentRun.queue_position.meridian} in Meridian queue)`
                    : `${currentRun.status.toUpperCase()}`
                }
              />
            )}

            {currentRun && (currentRun.status === "running" || currentRun.status === "queued") && (
              <Tooltip title="Cancel running job">
                <IconButton size="small" color="warning" onClick={handleCancelRun}>
                  <CancelOutlinedIcon fontSize="small" />
                </IconButton>
              </Tooltip>
            )}
          </Stack>

          <Stack direction="row" alignItems="center" spacing={1}>
            <Tooltip title="Set Access Code for write/compute operations">
              <IconButton size="small" onClick={() => setAuthDialogOpen(true)}>
                <LockOutlinedIcon fontSize="small" />
              </IconButton>
            </Tooltip>
            <Button
              variant="contained"
              size="small"
              startIcon={<UploadFileIcon fontSize="small" />}
              onClick={() => navigate("/import")}
            >
              Upload Workbook
            </Button>
          </Stack>
        </Toolbar>
        {currentRun && (currentRun.status === "running" || currentRun.status === "queued") && (
          <LinearProgress
            variant={totalStages > 0 && completedStages > 0 ? "determinate" : "indeterminate"}
            value={totalStages > 0 ? (completedStages / totalStages) * 100 : 0}
            sx={{ height: 2 }}
          />
        )}
      </AppBar>

      <Drawer
        variant="permanent"
        sx={{
          width: DRAWER_WIDTH,
          flexShrink: 0,
          display: { xs: "none", md: "block" },
          "& .MuiDrawer-paper": {
            width: DRAWER_WIDTH,
            boxSizing: "border-box",
            backgroundColor: "#FFFFFF",
            borderRight: `1px solid ${PALETTE.borderSubtle}`,
          },
        }}
      >
        <Box sx={{ px: 2.5, py: 2, borderBottom: `1px solid ${PALETTE.borderSubtle}` }}>
          <Typography
             sx={{
              fontFamily: '"Fraunces", Georgia, serif',
              fontSize: "1.2rem",
              fontWeight: 700,
              color: PALETTE.brandPrimary,
              letterSpacing: "-0.01em",
            }}
          >
            MMM Studio
          </Typography>
          <Typography variant="caption" sx={{ color: PALETTE.inkSecondary, display: "block" }}>
            Bayesian Marketing Mix & ROI
          </Typography>
        </Box>

        <Box sx={{ overflowY: "auto", flexGrow: 1, py: 1 }}>
          {NAV_GROUPS.map((group, gIdx) => (
            <React.Fragment key={group.header}>
              {gIdx > 0 && <Divider sx={{ my: 1 }} />}
              <List
                dense
                subheader={
                  <ListSubheader
                    sx={{
                      lineHeight: "28px",
                      fontSize: "0.7rem",
                      fontWeight: 700,
                      textTransform: "uppercase",
                      letterSpacing: "0.06em",
                      color: PALETTE.inkMuted,
                      backgroundColor: "transparent",
                    }}
                  >
                    {group.header}
                  </ListSubheader>
                }
              >
                {group.items.map((item) => {
                  const active = location.pathname.startsWith(item.path);
                  return (
                    <ListItemButton
                      key={item.path}
                      selected={active}
                      onClick={() => navigate(item.path)}
                      sx={{
                        mx: 1,
                        borderRadius: 1.5,
                        mb: 0.25,
                        "&.Mui-selected": {
                          backgroundColor: PALETTE.bgSubtle,
                          color: PALETTE.brandPrimary,
                          "& .MuiListItemIcon-root": {
                            color: PALETTE.brandPrimary,
                          },
                        },
                      }}
                    >
                      <ListItemIcon sx={{ minWidth: 32, color: PALETTE.inkSecondary }}>
                        {item.icon}
                      </ListItemIcon>
                      <ListItemText
                        primary={item.label}
                        primaryTypographyProps={{
                          fontSize: "0.84rem",
                          fontWeight: active ? 600 : 500,
                        }}
                      />
                    </ListItemButton>
                  );
                })}
              </List>
            </React.Fragment>
          ))}
        </Box>
      </Drawer>

      <Box
        component="main"
        sx={{
          flexGrow: 1,
          p: { xs: 2, md: 3.5 },
          mt: "56px",
          maxWidth: 1440,
          width: "100%",
          mx: "auto",
        }}
      >
        {children}
      </Box>

      <Dialog open={authDialogOpen} onClose={() => setAuthDialogOpen(false)} maxWidth="xs" fullWidth>
        <DialogTitle>Studio Access Code</DialogTitle>
        <DialogContent>
          <Typography variant="body2" sx={{ mb: 2 }}>
            Viewing pre-computed runs and dashboards is open. Entering the studio access code unlocks
            workbook uploads, Meridian fits, and interactive scenario jobs.
          </Typography>
          <TextField
            fullWidth
            size="small"
            type="password"
            label="X-Access-Code"
            value={codeInput}
            onChange={(e) => setCodeInput(e.target.value)}
            autoFocus
          />
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setAuthDialogOpen(false)}>Cancel</Button>
          <Button variant="contained" onClick={handleSaveCode}>
            Save Code
          </Button>
        </DialogActions>
      </Dialog>
    </Box>
  );
};
