import React from "react";
import FolderOpenOutlinedIcon from "@mui/icons-material/FolderOpenOutlined";
import SettingsSuggestOutlinedIcon from "@mui/icons-material/SettingsSuggestOutlined";
import AccountTreeOutlinedIcon from "@mui/icons-material/AccountTreeOutlined";
import DashboardOutlinedIcon from "@mui/icons-material/DashboardOutlined";
import FactCheckOutlinedIcon from "@mui/icons-material/FactCheckOutlined";
import InsightsOutlinedIcon from "@mui/icons-material/InsightsOutlined";
import BuildCircleOutlinedIcon from "@mui/icons-material/BuildCircleOutlined";
import HubOutlinedIcon from "@mui/icons-material/HubOutlined";
import TrendingUpOutlinedIcon from "@mui/icons-material/TrendingUpOutlined";
import TuneOutlinedIcon from "@mui/icons-material/TuneOutlined";
import TimelineOutlinedIcon from "@mui/icons-material/TimelineOutlined";
import DescriptionOutlinedIcon from "@mui/icons-material/DescriptionOutlined";

export interface NavItem {
  label: string;
  path: string;
  icon: React.ReactNode;
}

export interface NavGroup {
  /** Shown above the group in the sidebar and as the eyebrow of its pages' headers; the home item has none. */
  header: string | null;
  items: NavItem[];
  /** The group is a step of the MMM workflow: its pages get a previous / next step footer. */
  workflow?: boolean;
}

/**
 * The sidebar follows the MMM workflow: the Summary first, the run (upload, settings, pipeline), then the four steps
 * every MMM goes through: understand the data, fit and validate the model, plan the budget, report.
 * Former pages: Benchmark is a tab of Fit & validation (`/benchmark` redirects), the brief's answers are a tab of the
 * report (`/brief` redirects).
 */
export const NAV_GROUPS: NavGroup[] = [
  {
    header: null,
    items: [{ label: "Summary", path: "/overview", icon: <DashboardOutlinedIcon fontSize="small" /> }],
  },
  {
    header: "Run",
    items: [
      { label: "Runs", path: "/runs", icon: <FolderOpenOutlinedIcon fontSize="small" /> },
      { label: "Run setup", path: "/setup", icon: <SettingsSuggestOutlinedIcon fontSize="small" /> },
      { label: "Run status", path: "/status", icon: <AccountTreeOutlinedIcon fontSize="small" /> },
    ],
  },
  {
    header: "1 · Data",
    workflow: true,
    items: [
      { label: "Data audit", path: "/data-quality", icon: <FactCheckOutlinedIcon fontSize="small" /> },
      { label: "Exploration", path: "/explore", icon: <InsightsOutlinedIcon fontSize="small" /> },
      { label: "Data preparation", path: "/preparation", icon: <BuildCircleOutlinedIcon fontSize="small" /> },
    ],
  },
  {
    header: "2 · Model",
    workflow: true,
    items: [
      { label: "Fit & validation", path: "/model", icon: <HubOutlinedIcon fontSize="small" /> },
      { label: "Drivers & ROI", path: "/drivers", icon: <TrendingUpOutlinedIcon fontSize="small" /> },
    ],
  },
  {
    header: "3 · Plan",
    workflow: true,
    items: [
      { label: "Budget optimiser", path: "/optimizer", icon: <TuneOutlinedIcon fontSize="small" /> },
      { label: "Forecast & scenarios", path: "/forecast", icon: <TimelineOutlinedIcon fontSize="small" /> },
    ],
  },
  {
    header: "4 · Report",
    workflow: true,
    items: [{ label: "Report & downloads", path: "/reports", icon: <DescriptionOutlinedIcon fontSize="small" /> }],
  },
];

/** The nav entry (and its group) for a pathname, used for the page eyebrow and the document title. */
export function findNavItem(pathname: string): { item: NavItem; group: string | null } | null {
  for (const group of NAV_GROUPS) {
    const item = group.items.find((i) => pathname === i.path || pathname.startsWith(`${i.path}/`));
    if (item) return { item, group: group.header };
  }
  return null;
}

/** The workflow pages in order, each with its step, for the previous / next footer. */
export const WORKFLOW: { item: NavItem; group: string }[] = NAV_GROUPS.filter((g) => g.workflow).flatMap((g) =>
  g.items.map((item) => ({ item, group: g.header ?? "" }))
);
