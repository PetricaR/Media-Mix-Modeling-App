import type { ChannelRole, RunSummaryItem, StageStatus } from "../../api/types";
import { PALETTE } from "../../theme";

export type Profile = RunSummaryItem["profile"];

export const RUN_STATUS_LABEL: Record<string, string> = {
  draft: "Draft",
  queued: "Queued",
  running: "Running",
  succeeded: "Finished",
  failed: "Failed",
  cancelled: "Cancelled",
};

export const runStatusColor = (status?: string): "default" | "success" | "warning" | "error" | "info" => {
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

export const PROFILE_ORDER: Profile[] = ["full", "quick", "smoke", "custom"];

export const PROFILES: Record<Profile, { label: string; duration: string; detail: string; badge?: string }> = {
  full: {
    label: "Full",
    duration: "about 15–25 min",
    badge: "As in the notebooks",
    detail:
      "4 chains × (1,000 adapt + 500 burn-in + 1,000 draws) for the final, forecast and back-test fits, plus the 9-fit model-selection grid.",
  },
  quick: {
    label: "Quick",
    duration: "about 5–8 min",
    detail: "2 chains × (300 + 200 + 200 draws) for every Meridian fit; no selection grid.",
  },
  smoke: {
    label: "Smoke test",
    duration: "about 4 min",
    detail: "2 chains × 50 draws to check the pipeline end to end. Results are not converged.",
  },
  custom: {
    label: "Custom",
    duration: "depends on the draws",
    detail: "Your own chain and draw counts for the final, forecast and back-test fits; the extra fits never use more than Quick's.",
  },
};

export interface RoleOption {
  value: ChannelRole;
  label: string;
  short: string;
  colour: string;
  /** Drawn as a ring: the channel is left out of the model */
  hollow?: boolean;
}

/** The roles a user can pick; `digital_paid` and `control` only appear on older runs and are shown read-only. */
export const ROLE_OPTIONS: RoleOption[] = [
  { value: "paid", label: "Paid media (gets an ROI)", short: "Paid media", colour: PALETTE.brandPrimary },
  { value: "print_event", label: "Print event (catalog)", short: "Print event", colour: PALETTE.brandGold },
  { value: "commission", label: "Commission / control", short: "Commission", colour: PALETTE.brandNavy },
  { value: "unpaid", label: "Unpaid / organic", short: "Unpaid", colour: PALETTE.inkMuted },
  { value: "ignored", label: "Ignored", short: "Ignored", colour: PALETTE.inkMuted, hollow: true },
];

const LEGACY_ROLES: Record<string, RoleOption> = {
  digital_paid: { value: "digital_paid", label: "Paid media (legacy)", short: "Paid media", colour: PALETTE.brandPrimary },
  control: { value: "control", label: "Control (legacy)", short: "Control", colour: PALETTE.brandNavy },
};

export const roleOption = (role: ChannelRole): RoleOption =>
  ROLE_OPTIONS.find((o) => o.value === role) ?? LEGACY_ROLES[role] ?? { value: role, label: role, short: role, colour: PALETTE.inkMuted };

const ts = (iso?: string | null): number | null => {
  if (!iso) return null;
  const t = Date.parse(iso);
  return Number.isFinite(t) ? t : null;
};

/** "< 1 s", "42 s", "3 min 10 s", "1 h 2 min". */
export function formatDuration(ms: number): string {
  const s = Math.max(0, Math.round(ms / 1000));
  if (s < 1) return "< 1 s";
  if (s < 60) return `${s} s`;
  const m = Math.floor(s / 60);
  if (m < 60) return s % 60 ? `${m} min ${s % 60} s` : `${m} min`;
  return `${Math.floor(m / 60)} h ${m % 60} min`;
}

export function stageDurationMs(stage: StageStatus, now: number): number | null {
  const start = ts(stage.started_at);
  if (start === null) return null;
  const end = stage.status === "running" ? now : ts(stage.finished_at);
  return end === null ? null : end - start;
}

/** Wall-clock time of the whole pipeline: the analysis and Meridian queues run in parallel, so it is first start → last finish. */
export function pipelineElapsedMs(stages: StageStatus[], now: number, active: boolean): number | null {
  const starts = stages.map((s) => ts(s.started_at)).filter((t): t is number => t !== null);
  if (starts.length === 0) return null;
  const ends = stages.map((s) => ts(s.finished_at)).filter((t): t is number => t !== null);
  const end = active || ends.length === 0 ? now : Math.max(...ends);
  return end - Math.min(...starts);
}

export const formatDateTime = (iso?: string | null): string =>
  iso ? new Date(iso).toLocaleString("en-GB", { dateStyle: "medium", timeStyle: "short" }) : "—";

/** Calendar dates ("2023-01-01") are read as UTC so the day never shifts with the viewer's time zone. */
export const formatDay = (isoDate?: string | null): string =>
  isoDate
    ? new Date(`${isoDate.slice(0, 10)}T00:00:00Z`).toLocaleDateString("en-GB", { day: "numeric", month: "short", year: "numeric", timeZone: "UTC" })
    : "—";

const EUR_COMPACT = new Intl.NumberFormat("en-GB", { style: "currency", currency: "EUR", notation: "compact", maximumFractionDigits: 1 });
export const formatEuroCompact = (value?: number | null): string => (typeof value === "number" && Number.isFinite(value) ? EUR_COMPACT.format(value) : "—");

export const prefersReducedMotion = (): boolean =>
  typeof window !== "undefined" && window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;
