/** Euro and percentage formatting shared by the forecast planner. */

const MINUS = "−";

export const eur = (v: number) => `${v < 0 ? MINUS : ""}€${Math.abs(Math.round(v)).toLocaleString("en-GB")}`;

export const signedEur = (v: number) => `${v >= 0 ? "+" : MINUS}€${Math.abs(Math.round(v)).toLocaleString("en-GB")}`;

/** €8.01M, €514k, €950: for chart labels and the short lines under each lever. */
export const eurCompact = (v: number, signed = false) => {
  if (!Number.isFinite(v)) return "—";
  const a = Math.abs(v);
  const sign = v < 0 ? MINUS : signed ? "+" : "";
  if (a >= 999_500) return `${sign}€${(a / 1e6).toFixed(a >= 1e7 ? 1 : 2)}M`;
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

export type BudgetInputMode = "pct" | "eur";

/** Whether fixed euro inputs are entered and displayed as the period total (€) or daily average (€/day). */
export type SpendGranularity = "period" | "daily";

/** Convert a period euro total into the user's chosen display granularity (period total vs daily average). */
export function toDisplayEuro(periodSpend: number, granularity: SpendGranularity, horizonDays: number): number {
  if (!Number.isFinite(periodSpend)) return 0;
  if (granularity === "daily") {
    return periodSpend / Math.max(1, horizonDays);
  }
  return periodSpend;
}

/** Convert an entered euro amount from the user's chosen display granularity back into a period euro total. */
export function fromDisplayEuro(enteredSpend: number, granularity: SpendGranularity, horizonDays: number): number {
  if (!Number.isFinite(enteredSpend)) return 0;
  if (granularity === "daily") {
    return enteredSpend * Math.max(1, horizonDays);
  }
  return enteredSpend;
}

/** Count how many days in the first `horizonDays` of a channel's daily plan have positive spend (> €0). */
export function channelActiveDays(dailySpend: number[] | undefined, horizonDays: number): number {
  const days = Math.max(1, horizonDays);
  if (!dailySpend || dailySpend.length === 0) return days;
  return dailySpend.slice(0, days).filter((v) => (v ?? 0) > 0).length;
}

/**
 * Parse a euro amount entered by the user:
 * - Plain numbers or thousands-grouped numbers: "2555073", "2,555,073", "2 555 073", "€ 150,000", "0"
 * - Shorthand with k or M/Mio suffix: "500k", "514.5k", "2.5M", "1,8 Mio", "€1.25m"
 * Returns rounded whole euros (>= 0), or null when empty or invalid.
 */
export function parseEuroAmount(raw: string): number | null {
  const cleaned = raw
    .trim()
    .replace(/€/g, "")
    .replace(/EUR/gi, "")
    .trim();
  if (!cleaned) return null;

  // Check for negative sign (including unicode minus)
  if (cleaned.startsWith("-") || cleaned.startsWith(MINUS)) return null;

  const suffixMatch = /^([+]?\d+(?:[.,]\d+)?)\s*(k|thousand|m|mn|mio\.?|millions?)$/i.exec(cleaned.replace(/\s+/g, " "));
  if (suffixMatch) {
    const num = Number(suffixMatch[1].replace(",", "."));
    if (!Number.isFinite(num) || num < 0) return null;
    const unit = suffixMatch[2].toLowerCase();
    const factor = unit.startsWith("k") || unit.startsWith("t") ? 1e3 : 1e6;
    return Math.round(num * factor);
  }

  // Grouped thousands with commas or spaces: e.g. "2,555,073" or "2 555 073"
  const noSpaces = cleaned.replace(/\s+/g, "");
  if (/^[+]?\d{1,3}(,\d{3})+(\.\d+)?$/.test(noSpaces)) {
    const val = Number(noSpaces.replace(/,/g, ""));
    return Number.isFinite(val) && val >= 0 ? Math.round(val) : null;
  }
  if (/^[+]?\d{1,3}(\.\d{3})+(,\d+)?$/.test(noSpaces)) {
    const val = Number(noSpaces.replace(/\./g, "").replace(",", "."));
    return Number.isFinite(val) && val >= 0 ? Math.round(val) : null;
  }

  // Plain integer or decimal euro number: "2555073", "1500.5", "1500,5"
  if (/^[+]?\d+([.,]\d+)?$/.test(noSpaces)) {
    const val = Number(noSpaces.replace(",", "."));
    return Number.isFinite(val) && val >= 0 ? Math.round(val) : null;
  }

  return null;
}

/**
 * Parse a percentage change string (e.g. "+20", "-15.5", "−10 %", "0") and clamp to [min, max].
 * Returns null when empty or invalid.
 */
export function parsePctChange(raw: string, min = -100, max = 300): number | null {
  const cleaned = raw
    .trim()
    .replace(/%/g, "")
    .replace(new RegExp(MINUS, "g"), "-")
    .replace(",", ".")
    .trim();
  if (!cleaned) return null;
  const val = Number(cleaned);
  if (!Number.isFinite(val)) return null;
  return Math.max(min, Math.min(max, val));
}

/**
 * Compute a channel's planned spend over the first `horizonDays` of the plan.
 * Uses the exact daily plan array when present so channels with lumpy spend (e.g. Catalog send-outs)
 * or weekday profiles sum accurately over the chosen forecast period.
 */
export function channelPeriodBaseSpend(
  baseSpend: number,
  dailySpend: number[] | undefined,
  horizonDays: number,
  horizonMax: number
): number {
  const days = Math.max(1, Math.min(horizonDays, horizonMax));
  if (dailySpend && dailySpend.length > 0) {
    return dailySpend.slice(0, days).reduce((acc, v) => acc + (v ?? 0), 0);
  }
  if (days >= horizonMax || horizonMax <= 0) return baseSpend;
  return Math.round(baseSpend * (days / horizonMax));
}

/**
 * Scale active adjustable channels proportionally so their combined spend over the period equals `targetTotalSpend`.
 * If all channels are currently paused (0), scales all channels with positive base spend from their planned baseline.
 */
export function scaleMultipliersToTotal(
  slugs: string[],
  baseBySlug: Record<string, number>,
  currentMultipliers: Record<string, number>,
  targetTotalSpend: number
): Record<string, number> {
  const spendable = slugs.filter((s) => (baseBySlug[s] ?? 0) > 0);
  if (spendable.length === 0) return { ...currentMultipliers };

  const target = Math.max(0, targetTotalSpend);
  if (target === 0) {
    const next = { ...currentMultipliers };
    for (const s of spendable) next[s] = 0;
    return next;
  }

  const currentTotal = spendable.reduce((acc, s) => acc + (baseBySlug[s] ?? 0) * (currentMultipliers[s] ?? 1), 0);
  const baseTotal = spendable.reduce((acc, s) => acc + (baseBySlug[s] ?? 0), 0);

  const next = { ...currentMultipliers };
  if (currentTotal > 0) {
    const ratio = target / currentTotal;
    for (const s of spendable) {
      const curM = currentMultipliers[s] ?? 1;
      if (curM > 0) {
        next[s] = Math.round(curM * ratio * 1000) / 1000;
      }
    }
  } else if (baseTotal > 0) {
    const ratio = target / baseTotal;
    for (const s of spendable) {
      next[s] = Math.round(ratio * 1000) / 1000;
    }
  }
  return next;
}

/**
 * Split `targetTotalSpend` equally in euros across all active (unpaused) channels that have positive base spend.
 */
export function splitMultipliersEqually(
  slugs: string[],
  baseBySlug: Record<string, number>,
  currentMultipliers: Record<string, number>,
  targetTotalSpend: number
): Record<string, number> {
  const spendable = slugs.filter((s) => (baseBySlug[s] ?? 0) > 0);
  if (spendable.length === 0) return { ...currentMultipliers };

  const active = spendable.filter((s) => (currentMultipliers[s] ?? 1) > 0);
  const pool = active.length > 0 ? active : spendable;
  const perChannelSpend = Math.max(0, targetTotalSpend) / pool.length;

  const next = { ...currentMultipliers };
  for (const s of pool) {
    const base = baseBySlug[s] ?? 0;
    if (base > 0) {
      next[s] = Math.round((perChannelSpend / base) * 1000) / 1000;
    }
  }
  return next;
}

