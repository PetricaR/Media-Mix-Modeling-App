import React from "react";
import { Box, Typography } from "@mui/material";
import ArrowBackIcon from "@mui/icons-material/ArrowBack";
import ArrowForwardIcon from "@mui/icons-material/ArrowForward";
import { Link as RouterLink, useLocation } from "react-router-dom";
import { PALETTE } from "../../theme";
import { WORKFLOW } from "./nav";

const PagerLink: React.FC<{ to: string; step: string; label: string; direction: "prev" | "next" }> = ({ to, step, label, direction }) => (
  <Box
    component={RouterLink}
    to={to}
    sx={{
      display: "flex",
      flexDirection: "column",
      alignItems: direction === "next" ? "flex-end" : "flex-start",
      gap: 0.25,
      px: 2,
      py: 1.5,
      minWidth: 0,
      border: `1px solid ${PALETTE.borderSubtle}`,
      borderRadius: 2,
      backgroundColor: PALETTE.bgCard,
      textDecoration: "none",
      color: PALETTE.inkPrimary,
      transition: "border-color 0.15s",
      "&:hover": { borderColor: PALETTE.brandPrimary },
      "&:focus-visible": { outline: `3px solid ${PALETTE.brandPrimary}`, outlineOffset: 2 },
      "@media (prefers-reduced-motion: reduce)": { transition: "none" },
    }}
  >
    <Typography component="span" sx={{ fontSize: "0.72rem", fontWeight: 600, letterSpacing: "0.09em", textTransform: "uppercase", color: PALETTE.inkMuted }}>
      {direction === "next" ? "Next" : "Previous"} · {step}
    </Typography>
    <Box component="span" sx={{ display: "inline-flex", alignItems: "center", gap: 0.75, fontWeight: 600, color: PALETTE.brandPrimary }}>
      {direction === "prev" && <ArrowBackIcon sx={{ fontSize: 17 }} aria-hidden="true" />}
      {label}
      {direction === "next" && <ArrowForwardIcon sx={{ fontSize: 17 }} aria-hidden="true" />}
    </Box>
  </Box>
);

/** Previous / next step of the MMM workflow at the end of every workflow page (nothing elsewhere). */
export const WorkflowPager: React.FC = () => {
  const { pathname } = useLocation();
  const idx = WORKFLOW.findIndex((w) => pathname === w.item.path || pathname.startsWith(`${w.item.path}/`));
  if (idx < 0) return null;
  const prev = WORKFLOW[idx - 1];
  const next = WORKFLOW[idx + 1];
  return (
    <Box
      component="nav"
      aria-label="Workflow steps"
      sx={{ mt: 2, pt: 3, borderTop: `1px solid ${PALETTE.borderSubtle}`, display: "grid", gap: 2, gridTemplateColumns: { xs: "minmax(0, 1fr)", sm: "repeat(2, minmax(0, 1fr))" } }}
    >
      <Box sx={{ display: "grid" }}>{prev && <PagerLink to={prev.item.path} step={prev.group} label={prev.item.label} direction="prev" />}</Box>
      <Box sx={{ display: "grid" }}>{next && <PagerLink to={next.item.path} step={next.group} label={next.item.label} direction="next" />}</Box>
    </Box>
  );
};
