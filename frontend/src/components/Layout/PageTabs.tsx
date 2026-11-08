import React from "react";
import { Box, Tab, Tabs } from "@mui/material";
import { useSearchParams } from "react-router-dom";
import { PALETTE } from "../../theme";

export interface PageTab {
  slug: string;
  label: string;
}

/**
 * The active tab of a page, kept in the URL (`?tab=…`) so it survives reloads and can be linked. Unknown or missing
 * values fall back to the first tab.
 */
export function usePageTab<T extends PageTab>(tabs: T[]): [T, (slug: string) => void] {
  const [params, setParams] = useSearchParams();
  const active = tabs.find((t) => t.slug === params.get("tab")) ?? tabs[0];
  const select = (slug: string) => setParams({ tab: slug }, { replace: true });
  return [active, select];
}

export const tabId = (scope: string, slug: string) => `${scope}-tab-${slug}`;
export const panelId = (scope: string) => `${scope}-panel`;

/** Underlined page tabs below the page header; the panel is rendered by the page (`role="tabpanel"`, `panelId`). */
export const PageTabs: React.FC<{ scope: string; tabs: PageTab[]; active: string; onChange: (slug: string) => void; label: string }> = ({
  scope,
  tabs,
  active,
  onChange,
  label,
}) => (
  <Box sx={{ mb: 3, borderBottom: `1px solid ${PALETTE.borderSubtle}` }}>
    <Tabs
      value={Math.max(0, tabs.findIndex((t) => t.slug === active))}
      onChange={(_, idx: number) => onChange(tabs[idx].slug)}
      variant="scrollable"
      scrollButtons="auto"
      allowScrollButtonsMobile
      aria-label={label}
      sx={{ minHeight: 44, "& .MuiTab-root": { minHeight: 44, px: 1.75, textTransform: "none", fontSize: "0.9rem", fontWeight: 550 } }}
    >
      {tabs.map((t) => (
        <Tab key={t.slug} label={t.label} id={tabId(scope, t.slug)} aria-controls={panelId(scope)} />
      ))}
    </Tabs>
  </Box>
);
