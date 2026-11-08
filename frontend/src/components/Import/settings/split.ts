/**
 * The chronological split a run will use, for the settings form: training, then validation and test of equal length at
 * the end of the data. It mirrors the backend's `features.holdout_windows` (the pipeline's source of truth): the test is
 * the last `holdoutDays` days; validation is as long, ends the day before it, starts on the nearest first of a month
 * within a week, and exists only when a full year of training days remains before it.
 */

const DAY_MS = 86_400_000;
const SNAP_DAYS = 7;
const MIN_WINDOW_DAYS = 14;

export interface SplitWindow {
  start: string;
  end: string;
  days: number;
}

export interface DataSplit {
  total: number;
  training: SplitWindow;
  /** null when a validation window as long as the test would leave less than a training year before it */
  validation: SplitWindow | null;
  test: SplitWindow;
}

const parseDay = (isoDate: string): number => Date.parse(`${isoDate.slice(0, 10)}T00:00:00Z`);
const isoDay = (t: number): string => new Date(t).toISOString().slice(0, 10);
const span = (start: number, end: number): SplitWindow => ({ start: isoDay(start), end: isoDay(end), days: Math.round((end - start) / DAY_MS) + 1 });

/** The first of a month nearest to `start` within a week and inside [earliest, latest]; else `start`. */
function snapToMonthStart(start: number, earliest: number, latest: number): number {
  const d = new Date(start);
  const candidates = [Date.UTC(d.getUTCFullYear(), d.getUTCMonth(), 1), Date.UTC(d.getUTCFullYear(), d.getUTCMonth() + 1, 1)];
  const fits = candidates.filter((c) => Math.abs(c - start) <= SNAP_DAYS * DAY_MS && c >= earliest && c <= latest);
  return fits.reduce<number | null>((best, c) => (best == null || Math.abs(c - start) < Math.abs(best - start) ? c : best), null) ?? start;
}

export function splitWindows(firstDay: string, lastDay: string, holdoutDays: number, minTrainingDays = 365): DataSplit | null {
  const first = parseDay(firstDay);
  const end = parseDay(lastDay);
  if (!Number.isFinite(first) || !Number.isFinite(end) || end <= first || !Number.isFinite(holdoutDays)) return null;
  const h = Math.max(Math.round(holdoutDays), MIN_WINDOW_DAYS);
  const testStart = end - (h - 1) * DAY_MS;
  if (testStart <= first) return null;
  const valEnd = testStart - DAY_MS;
  const earliest = first + minTrainingDays * DAY_MS;
  const valStart = snapToMonthStart(valEnd - (h - 1) * DAY_MS, earliest, valEnd - (MIN_WINDOW_DAYS - 1) * DAY_MS);
  const validation = valStart >= earliest ? span(valStart, valEnd) : null;
  return {
    total: Math.round((end - first) / DAY_MS) + 1,
    training: span(first, (validation ? valStart : testStart) - DAY_MS),
    validation,
    test: span(testStart, end),
  };
}
