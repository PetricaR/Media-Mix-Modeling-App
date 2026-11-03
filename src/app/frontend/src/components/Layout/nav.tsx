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
import CompareArrowsOutlinedIcon from "@mui/icons-material/CompareArrowsOutlined";
import TuneOutlinedIcon from "@mui/icons-material/TuneOutlined";
import TimelineOutlinedIcon from "@mui/icons-material/TimelineOutlined";
import DescriptionOutlinedIcon from "@mui/icons-material/DescriptionOutlined";
import ArticleOutlinedIcon from "@mui/icons-material/ArticleOutlined";

export interface NavItem {
  label: string;
  path: string;
  icon: React.ReactNode;
}

export interface NavGroup {
  /** Shown above the group in the sidebar and as the eyebrow of its pages' headers; the home item has none. */
  header: string | null;
  items: NavItem[];
}

/** The sidebar, in reading order: the overview is the home page, then the run, the analysis and the deliverables. */
export const NAV_GROUPS: NavGroup[] = [
  {
    header: null,
    items: [{ label: "Executive overview", path: "/overview", icon: <DashboardOutlinedIcon fontSize="small" /> }],
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

/** The nav entry (and its group) for a pathname, used for the page eyebrow and the document title. */
export function findNavItem(pathname: string): { item: NavItem; group: string | null } | null {
  for (const group of NAV_GROUPS) {
    const item = group.items.find((i) => pathname === i.path || pathname.startsWith(`${i.path}/`));
    if (item) return { item, group: group.header };
  }
  return null;
}
