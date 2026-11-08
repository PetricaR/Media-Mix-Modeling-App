import React from "react";
import { Box, Stack, Typography } from "@mui/material";
import { INVERSE, PALETTE } from "../../theme";

/** The Formare AI mark from formare.dev: a white "F" on the accent square. */
export const FormareMark: React.FC<{ size?: number; tone?: "light" | "dark" }> = ({ size = 28, tone = "light" }) => (
  <Box
    component="svg"
    viewBox="0 0 24 24"
    width={size}
    height={size}
    aria-hidden="true"
    focusable="false"
    sx={{ flexShrink: 0, display: "block" }}
  >
    <rect width="24" height="24" rx="5" fill={tone === "dark" ? INVERSE.cta : PALETTE.brandPrimary} />
    <path d="M7 6h10v3.25H10.5V11H15v3.1h-4.5V18H7z" fill="#FFFFFF" />
  </Box>
);

/**
 * The app's lockup: the Formare mark, the product name and, below it, the Formare AI wordmark ("AI" in the accent,
 * as on formare.dev). `tone="dark"` is for the navy sidebar.
 */
export const BrandMark: React.FC<{ compact?: boolean; tone?: "light" | "dark" }> = ({ compact = false, tone = "light" }) => {
  const dark = tone === "dark";
  return (
    <Stack direction="row" alignItems="center" spacing={1.25} sx={{ minWidth: 0 }}>
      <FormareMark size={compact ? 26 : 30} tone={tone} />
      <Box sx={{ minWidth: 0 }}>
        <Typography
          component="span"
          sx={{
            display: "block",
            fontWeight: 600,
            fontSize: compact ? "1rem" : "1.0625rem",
            color: dark ? INVERSE.ink : PALETTE.inkPrimary,
            lineHeight: 1.15,
            letterSpacing: "-0.02em",
          }}
        >
          MMM Studio
        </Typography>
        {!compact && (
          <Typography
            component="span"
            noWrap
            sx={{ display: "block", mt: "2px", fontSize: "0.75rem", lineHeight: 1.3, fontWeight: 500, color: dark ? INVERSE.inkMuted : PALETTE.inkMuted }}
          >
            by Formare{" "}
            <Box component="span" sx={{ color: dark ? INVERSE.accent : PALETTE.brandPrimary }}>
              AI
            </Box>
          </Typography>
        )}
      </Box>
    </Stack>
  );
};
