/** Euro and percentage formatting shared by the forecast planner. */

const MINUS = "−";

export const eur = (v: number) => `${v < 0 ? MINUS : ""}€${Math.abs(Math.round(v)).toLocaleString("en-GB")}`;

export const signedEur = (v: number) => `${v >= 0 ? "+" : MINUS}€${Math.abs(Math.round(v)).toLocaleString("en-GB")}`;

/** €8.01M, €514k, €950: for chart labels and the short lines under each lever. */
export const eurCompact = (v: number, signed = false) => {
  const a = Math.abs(v);
  const sign = v < 0 ? MINUS : signed ? "+" : "";
  if (a >= 1e6) return `${sign}€${(a / 1e6).toFixed(a >= 1e7 ? 1 : 2)}M`;
  if (a >= 1e3) return `${sign}€${(a / 1e3).toFixed(a >= 1e5 ? 0 : 1)}k`;
  return `${sign}€${Math.round(a)}`;
};

export const signedPct = (v: number, digits = 1) => `${v >= 0 ? "+" : MINUS}${Math.abs(v).toFixed(digits)} %`;

/** A budget multiplier as a change: 1.2 → "+20 %", 0 → "−100 %", 1 → "as planned". */
export const multiplierLabel = (m: number) => (Math.abs(m - 1) < 1e-9 ? "as planned" : signedPct((m - 1) * 100, 0));

/** ISO date (YYYY-MM-DD) shifted by whole days, in UTC so daylight saving never moves it. */
export const addDays = (iso: string, days: number) => {
  const d = new Date(`${iso}T00:00:00Z`);
  d.setUTCDate(d.getUTCDate() + days);
  return d.toISOString().slice(0, 10);
};

export const shortDate = (iso: string) =>
  new Date(`${iso}T00:00:00Z`).toLocaleDateString("en-GB", { day: "numeric", month: "short", year: "numeric", timeZone: "UTC" });
