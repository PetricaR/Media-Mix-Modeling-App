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
  Drawer,
  FormControl,
  IconButton,
  InputAdornment,
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
import LogoutIcon from "@mui/icons-material/Logout";
import CancelOutlinedIcon from "@mui/icons-material/CancelOutlined";
import { Link as RouterLink, useLocation, useNavigationType } from "react-router-dom";
import { useQueryClient } from "@tanstack/react-query";
import { api } from "../../api/client";
import { isRunActive, useHealth, useRun, useRuns } from "../../api/hooks";
import { INVERSE, LAYOUT, PALETTE } from "../../theme";
import { PROFILES, RUN_STATUS_LABEL as STATUS_LABEL, runStatusColor as statusColor } from "../Import/format";
import { refreshSession } from "./AuthGate";
import { BrandMark } from "./BrandMark";
import { NAV_GROUPS, findNavItem } from "./nav";
import { WorkflowPager } from "./WorkflowPager";

const DRAWER_WIDTH = LAYOUT.drawerWidth;
const HEADER_HEIGHT = LAYOUT.headerHeight;

export const AppShell: React.FC<{
  selectedRunId: string | null;
  onSelectRunId: (id: string | null) => void;
  children: React.ReactNode;
}> = ({ selectedRunId, onSelectRunId, children }) => {
  const location = useLocation();
  const queryClient = useQueryClient();
  const desktop = useMediaQuery(useTheme().breakpoints.up("lg"), { noSsr: true });
  const [mobileOpen, setMobileOpen] = useState(false);

  const { data: runsData } = useRuns();
  const runs = runsData?.runs || [];

  // Fall back to the active succeeded run (or newest succeeded run) when nothing is selected or the stored run no longer exists
  useEffect(() => {
    if (!runsData) return;
    if (selectedRunId && runs.some((r) => r.id === selectedRunId)) return;
    const fallback =
      runs.find((r) => r.is_active && r.status === "succeeded") ||
      runs.find((r) => r.status === "succeeded") ||
      runs.find((r) => r.is_active) ||
      runs[0];
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
        for (const key of ["section", "sections", "artifacts", "forecasts", "history", "runs"]) {
          queryClient.invalidateQueries({ queryKey: [key] });
        }
      }
    }
    lastStatus.current = st;
  }, [currentRun, queryClient]);

  const [confirmCancel, setConfirmCancel] = useState(false);
  const [cancelError, setCancelError] = useState<string | null>(null);
  // Sign-out only exists on servers that require an access code (the AuthGate handles signing in)
  const { data: health } = useHealth();

  useEffect(() => setMobileOpen(false), [location.pathname]);

  // Every route gets its own document title (browser tabs, history, screen readers)
  const navEntry = findNavItem(location.pathname);
  useEffect(() => {
    document.title = navEntry ? `${navEntry.item.label} · MMM Studio` : "MMM Studio · Formare AI";
  }, [navEntry?.item.label]);

  // A link to another page behaves like a page load: it starts at the top and focus moves to the new content.
  // Redirects (replace) and back/forward (pop) keep the browser's own behaviour; run pages that focus their own
  // heading pass state.focusHeading.
  const navigationType = useNavigationType();
  const lastPath = useRef(location.pathname);
  useEffect(() => {
    if (lastPath.current === location.pathname) return;
    lastPath.current = location.pathname;
    if (navigationType !== "PUSH") return;
    window.scrollTo({ top: 0, behavior: "instant" });
    if (!(location.state as { focusHeading?: boolean } | null)?.focusHeading) {
      document.getElementById("main-content")?.focus({ preventScroll: true });
    }
  }, [location.pathname, location.state, navigationType]);

  const handleSignOut = async () => {
    try {
      await api.logout();
    } finally {
      await refreshSession(queryClient);
    }
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

  // Short status hints next to the run pages, so the sidebar shows where the selected run stands
  const navHint = (path: string): { text: string; label: string; tone?: "bad" } | null => {
    if (!currentRun) return null;
    if (path === "/setup" && currentRun.status === "draft") return { text: "Review", label: ": the selected run is a draft to review" };
    if (path === "/status" && active) {
      return currentRun.status === "queued"
        ? { text: "Queued", label: ": the selected run is queued" }
        : { text: `${completedStages}/${counted.length}`, label: `: ${completedStages} of ${counted.length} stages finished` };
    }
    if (path === "/status" && currentRun.status === "failed") return { text: "Failed", label: ": the selected run failed", tone: "bad" };
    return null;
  };

  const nav = (
    <Box
      component="nav"
      aria-label="Main"
      sx={{
        display: "flex",
        flexDirection: "column",
        height: "100%",
        // formare.dev's navy `.inverse` band: light ink, sky accent, and a focus ring that shows on navy
        color: INVERSE.inkSecondary,
        backgroundColor: INVERSE.bg,
        colorScheme: "dark",
        "& :focus-visible": { outlineColor: INVERSE.accent },
      }}
    >
      <Box
        component={RouterLink}
        to="/overview"
        sx={{
          px: 2,
          // Same height as the app bar, so the two bottom borders form one line
          height: HEADER_HEIGHT + 1,
          flexShrink: 0,
          borderBottom: `1px solid ${INVERSE.rule}`,
          display: "flex",
          alignItems: "center",
          textDecoration: "none",
        }}
      >
        <BrandMark tone="dark" />
      </Box>
      <Box sx={{ overflowY: "auto", flexGrow: 1, py: 1.25, scrollbarWidth: "thin", scrollbarColor: `${INVERSE.rule} transparent` }}>
        {NAV_GROUPS.map((group, gIdx) => (
          <React.Fragment key={group.header ?? "home"}>
            <List
              dense
              disablePadding
              aria-labelledby={group.header ? `nav-${gIdx}` : undefined}
              sx={{ mb: 1.25 }}
              subheader={
                group.header ? (
                  <ListSubheader
                    id={`nav-${gIdx}`}
                    disableSticky
                    sx={{
                      px: 2.25,
                      pt: 0.75,
                      lineHeight: "24px",
                      fontSize: "0.68rem",
                      fontWeight: 600,
                      textTransform: "uppercase",
                      letterSpacing: "0.09em",
                      color: INVERSE.inkMuted,
                      backgroundColor: "transparent",
                    }}
                  >
                    {group.header}
                  </ListSubheader>
                ) : undefined
              }
            >
              {group.items.map((item) => {
                const isCurrent = location.pathname.startsWith(item.path);
                const hint = navHint(item.path);
                return (
                  <ListItem key={item.path} disablePadding>
                    <ListItemButton
                      component={RouterLink}
                      to={item.path}
                      selected={isCurrent}
                      aria-current={isCurrent ? "page" : undefined}
                      sx={{
                        mx: 1,
                        px: 1.25,
                        borderRadius: 0.75,
                        mb: "2px",
                        minHeight: 34,
                        py: 0.25,
                        color: INVERSE.inkSecondary,
                        "&:hover": { backgroundColor: INVERSE.surface, color: INVERSE.ink },
                        "&.Mui-selected": {
                          backgroundColor: INVERSE.accentWash,
                          color: INVERSE.ink,
                          // The current page also carries a sky bar, so it never rests on colour alone
                          boxShadow: `inset 2px 0 0 ${INVERSE.accent}`,
                          "& .MuiListItemIcon-root": { color: INVERSE.accent },
                        },
                        "&.Mui-selected:hover": { backgroundColor: INVERSE.accentWash },
                        "&.Mui-focusVisible": { backgroundColor: INVERSE.surface },
                      }}
                    >
                      <ListItemIcon sx={{ minWidth: 32, color: INVERSE.inkMuted, "& svg": { fontSize: 19 } }}>{item.icon}</ListItemIcon>
                      <ListItemText primary={item.label} primaryTypographyProps={{ fontSize: "0.86rem", fontWeight: isCurrent ? 600 : 450, color: "inherit", noWrap: true }} />
                      {hint && (
                        <Box
                          component="span"
                          sx={{
                            ml: 1,
                            px: 0.75,
                            borderRadius: 0.5,
                            fontSize: "0.7rem",
                            fontWeight: 600,
                            lineHeight: "20px",
                            fontVariantNumeric: "tabular-nums",
                            whiteSpace: "nowrap",
                            color: hint.tone === "bad" ? INVERSE.bad : INVERSE.accent,
                            border: `1px solid ${INVERSE.rule}`,
                            backgroundColor: INVERSE.surface,
                          }}
                        >
                          <span aria-hidden="true">{hint.text}</span>
                          <span className="visually-hidden">{hint.label}</span>
                        </Box>
                      )}
                    </ListItemButton>
                  </ListItem>
                );
              })}
            </List>
          </React.Fragment>
        ))}
      </Box>
      <Box sx={{ px: 2.25, py: 1.5, borderTop: `1px solid ${INVERSE.rule}` }}>
        <Typography variant="caption" sx={{ color: INVERSE.inkMuted, display: "block", lineHeight: 1.45, fontSize: "0.72rem" }}>
          Built by{" "}
          <Box
            component="a"
            href="https://formare.dev/"
            target="_blank"
            rel="noopener"
            sx={{ color: INVERSE.ink, textDecorationColor: INVERSE.rule, textUnderlineOffset: "2px", "&:hover": { color: INVERSE.accent } }}
          >
            Formare AI
          </Box>
          .
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
          backgroundColor: "rgba(248, 250, 253, 0.92)",
          backdropFilter: "blur(8px)",
          borderBottom: `1px solid ${PALETTE.borderSubtle}`,
          transition: "box-shadow 0.2s",
          // Progressive enhancement (scroll-state queries): a soft shadow while the page is scrolled; elsewhere the border alone
          "@container page scroll-state(scrollable: top)": { boxShadow: "0 4px 16px rgba(15, 24, 40, 0.07)" },
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
            {/* A context switcher rather than a form field: the label sits inside, like a project picker */}
            <FormControl size="small" sx={{ minWidth: { xs: 0, sm: 250 }, maxWidth: 380, flexShrink: 1 }}>
              <Select
                labelId="run-select-label"
                value={runs.some((r) => r.id === selectedRunId) ? selectedRunId ?? "" : ""}
                displayEmpty
                onChange={(e) => onSelectRunId((e.target.value as string) || null)}
                startAdornment={
                  <InputAdornment position="start" sx={{ mr: 0.75 }}>
                    <Box
                      component="span"
                      id="run-select-label"
                      sx={{ fontSize: "0.66rem", fontWeight: 600, letterSpacing: "0.09em", textTransform: "uppercase", color: PALETTE.brandPrimary }}
                    >
                      Run
                    </Box>
                  </InputAdornment>
                }
                sx={{
                  fontSize: "0.86rem",
                  fontWeight: 600,
                  borderRadius: 0.5,
                  backgroundColor: PALETTE.bgCard,
                  "& .MuiSelect-select": { py: "7px" },
                  "& .MuiOutlinedInput-notchedOutline": { borderColor: PALETTE.borderSubtle },
                  "&:hover .MuiOutlinedInput-notchedOutline": { borderColor: PALETTE.borderStrong },
                }}
                MenuProps={{ slotProps: { paper: { sx: { mt: 0.75, minWidth: 320 } } } }}
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
                    <Stack direction="row" spacing={1.5} alignItems="center" sx={{ width: "100%" }}>
                      <Box sx={{ flexGrow: 1, minWidth: 0 }}>
                        <Typography sx={{ fontSize: "0.88rem", fontWeight: 650, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{r.name}</Typography>
                        <Typography component="span" variant="caption" sx={{ display: "block", color: PALETTE.inkMuted }}>
                          {PROFILES[r.profile]?.label ?? r.profile} sampling
                          {r.is_active ? " · default run" : ""}
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

          {health?.auth_required && (
            <Tooltip title="Sign out">
              <IconButton size="small" aria-label="Sign out" onClick={() => void handleSignOut()}>
                <LogoutIcon fontSize="small" />
              </IconButton>
            </Tooltip>
          )}
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
            "& .MuiDrawer-paper": { width: DRAWER_WIDTH, boxSizing: "border-box", backgroundColor: INVERSE.bg, border: "none", borderRight: `1px solid ${INVERSE.rule}` },
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
          sx={{ "& .MuiDrawer-paper": { width: DRAWER_WIDTH, boxSizing: "border-box", backgroundColor: INVERSE.bg, border: "none" } }}
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
        <WorkflowPager />
      </Box>

      <Dialog open={confirmCancel} onClose={() => setConfirmCancel(false)} maxWidth="xs" fullWidth>
        <DialogTitle>Cancel this run?</DialogTitle>
        <DialogContent>
          <DialogContentText>
            The workers stop “{currentRun?.name}” after the current step. Results already computed stay available; the
            run can be started again from the run setup page.
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

    </Box>
  );
};
