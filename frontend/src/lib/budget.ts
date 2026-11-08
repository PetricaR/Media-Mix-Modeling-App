/**
 * Parse a total budget typed in € million: "121.8", "121,8", "121.8 M", "€ 121,8 Mio". Returns the amount in euros,
 * null for an empty field, or NaN when the text is not a single number (no thousands separators, no other units), so
 * a typo can never turn into a tiny or huge budget.
 */
export function parseBudgetMillions(input: string): number | null {
  const text = input.trim();
  if (!text) return null;
  const core = text
    .replace(/€/g, "")
    .replace(/\s+/g, "")
    .replace(/(millions?|mio\.?|mn|m)$/i, "");
  if (!/^\d+([.,]\d+)?$/.test(core)) return Number.NaN;
  const millions = Number(core.replace(",", "."));
  return millions > 0 ? Math.round(millions * 1e6) : Number.NaN;
}
