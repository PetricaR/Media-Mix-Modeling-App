/**
 * One CSV cell, quoted. A value that a spreadsheet would run as a formula (starting with =, +, -, @, tab or carriage
 * return) gets a leading apostrophe; negative numbers such as "-12.5" stay numbers.
 */
export function escapeCsvCell(raw: unknown): string {
  const s = String(raw ?? "");
  const safe = /^[=+\-@\t\r]/.test(s) && !/^-\d/.test(s) ? `'${s}` : s;
  return `"${safe.replace(/"/g, '""')}"`;
}
