import React from "react";
import { Box, Stack, Typography } from "@mui/material";
import { useLocation } from "react-router-dom";
import { PALETTE } from "../../theme";
import { findNavItem } from "./nav";

/**
 * The one page header every page uses: the sidebar group as an eyebrow, the page's h1, a short description and the
 * page-level actions on the right (they wrap below the title on narrow screens).
 */
export const PageHeader: React.FC<{
  /** Defaults to the sidebar label of the current route. */
  title?: React.ReactNode;
  description?: React.ReactNode;
  actions?: React.ReactNode;
  /** Defaults to the sidebar group of the current route; pass null to hide it. */
  eyebrow?: string | null;
  headingId?: string;
  headingRef?: React.Ref<HTMLHeadingElement>;
}> = ({ title, description, actions, eyebrow, headingId, headingRef }) => {
  const nav = findNavItem(useLocation().pathname);
  const kicker = eyebrow === undefined ? nav?.group : eyebrow;
  return (
    <Box component="header" sx={{ mb: 3 }}>
      <Stack direction={{ xs: "column", md: "row" }} justifyContent="space-between" alignItems={{ xs: "flex-start", md: "flex-end" }} spacing={{ xs: 1.5, md: 3 }}>
        <Box sx={{ minWidth: 0, maxWidth: "75ch" }}>
          {kicker && (
            <Typography
              component="p"
              sx={{ mb: 0.75, fontSize: "0.75rem", fontWeight: 600, letterSpacing: "0.09em", textTransform: "uppercase", color: PALETTE.brandPrimary }}
            >
              {kicker}
            </Typography>
          )}
          <Typography ref={headingRef} id={headingId} tabIndex={headingRef ? -1 : undefined} variant="h1" sx={{ "&:focus": { outline: "none" } }}>
            {title ?? nav?.item.label}
          </Typography>
          {description && (
            <Typography variant="body1" component="div" sx={{ mt: 0.75, color: PALETTE.inkSecondary }}>
              {description}
            </Typography>
          )}
        </Box>
        {actions && (
          <Stack direction="row" spacing={1} flexWrap="wrap" useFlexGap sx={{ flexShrink: 0, minWidth: 0, maxWidth: "100%" }}>
            {actions}
          </Stack>
        )}
      </Stack>
    </Box>
  );
};
