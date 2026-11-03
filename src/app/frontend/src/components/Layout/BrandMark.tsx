import React from "react";
import { Box, Stack, Typography } from "@mui/material";
import { PALETTE } from "../../theme";

/** An original coffee-bean mark in Tchibo's gold and navy (not the Tchibo logo), with the app's wordmark. */
export const BeanIcon: React.FC<{ size?: number }> = ({ size = 32 }) => (
  <Box
    component="svg"
    viewBox="0 0 32 32"
    width={size}
    height={size}
    aria-hidden="true"
    focusable="false"
    sx={{ flexShrink: 0, display: "block" }}
  >
    <rect width="32" height="32" rx="8" fill={PALETTE.brandNavy} />
    <ellipse cx="16" cy="16" rx="8.5" ry="11" transform="rotate(35 16 16)" fill={PALETTE.brandGoldLight} />
    <path d="M11 22c4-3 6-9 10-12" stroke={PALETTE.brandNavy} strokeWidth="2" fill="none" strokeLinecap="round" />
  </Box>
);

export const BrandMark: React.FC<{ compact?: boolean }> = ({ compact = false }) => (
  <Stack direction="row" alignItems="center" spacing={1.25} sx={{ minWidth: 0 }}>
    <BeanIcon size={compact ? 28 : 32} />
    <Box sx={{ minWidth: 0 }}>
      <Typography
        component="span"
        sx={{ display: "block", fontWeight: 800, fontSize: compact ? "1rem" : "1.06rem", color: PALETTE.brandNavy, lineHeight: 1.15, letterSpacing: "-0.01em" }}
      >
        MMM Studio
      </Typography>
      {!compact && (
        <Typography component="span" noWrap sx={{ display: "block", fontSize: "0.72rem", lineHeight: 1.35, color: PALETTE.brandGoldDark, fontWeight: 650 }}>
          Tchibo MMM case study
        </Typography>
      )}
    </Box>
  </Stack>
);
