import { createTheme } from "@mui/material/styles";

/**
 * Colours follow the Formare AI design tokens from formare.dev (its CSS defines them in OKLCH; the hex values below
 * are those tokens converted to sRGB): cool "paper" surfaces, navy-slate ink, one accent blue (the logo square) and
 * the dark navy `.inverse` band the site uses for its header, which the sidebar wears here. Type is IBM Plex Sans and
 * IBM Plex Mono, self-hosted like on formare.dev (`main.tsx`).
 */
export const PALETTE = {
  bgCanvas: "#F8FAFD", // paper, oklch(98.5% .004 250)
  bgCard: "#FFFFFF", // surface
  bgSubtle: "#EFF3F8", // paper-2, oklch(96.2% .008 255)
  bgAccentTint: "#E5F1FF", // accent-wash, oklch(95.5% .025 255)
  bgGoodTint: "#E3F8E9",
  bgBadTint: "#FFEDEB",
  inkPrimary: "#0F1828", // ink, oklch(21% .035 262)
  inkSecondary: "#3A4353", // ink-2, oklch(38% .03 262)
  inkMuted: "#5A6475", // ink-3, oklch(50% .03 262) (6.0:1 on white)
  borderSubtle: "#D9DFE6", // rule, oklch(90% .012 255)
  borderStrong: "#86909F", // rule-strong, oklch(65% .025 258) (3.2:1 on white: inputs, outlined controls)
  brandPrimary: "#1957D2", // accent / cta, oklch(50% .2 262): the logo square
  brandPrimaryDark: "#0643B5", // accent-hover, oklch(43% .19 262)
  brandNavy: "#070F1F", // inverse paper, oklch(17% .035 262): the site header band
  brandSky: "#71BFFF", // inverse accent, oklch(78% .12 245): the accent on navy
  semanticGood: "#10703A",
  semanticWarn: "#9D6300",
  semanticBad: "#B32322", // danger, oklch(50% .18 27)
  semanticInfo: "#1957D2",
};

/** formare.dev's `.inverse` palette (dark navy band), used by the sidebar. */
export const INVERSE = {
  bg: "#070F1F", // paper
  surface: "#141E31", // surface, hover
  ink: "#F3F5F8", // ink (17.5:1 on bg)
  inkSecondary: "#C4CBD4", // ink-2 (11.7:1)
  inkMuted: "#9DA5B1", // ink-3 (7.7:1)
  rule: "#293345",
  accent: "#71BFFF", // accent (9.7:1): current item, focus ring
  accentWash: "#182846", // accent-wash: current item background
  cta: "#1A5CDF", // cta: the logo square on navy
  bad: "#FF9F95",
};

/**
 * Channel colours, keyed by display name and slug, the same as `charts/theme.py` so a channel has one colour on every
 * page. SEA takes the Formare accent and Catalog the warm orange that also marks print in the EDA charts; the others
 * are hues of the dataviz reference palette. Checked with the palette validator on white, neighbours in the canonical
 * order: the six media channels pass (worst CVD ΔE 9.2, normal vision ≥ 15.6); with Affiliate, Display ↔ Affiliate is
 * in the 6–8 floor band (ΔE 6.9), which legends, hover labels and axis labels cover. The reference violet is left
 * out: under protanopia it is ΔE 0.7 from the accent.
 */
export const CHANNEL_COLORS: Record<string, string> = {
  SEA: "#1957D2",
  Catalog: "#EB6834",
  Display: "#1BAF7A",
  Affiliate: "#E34948",
  "Display Network": "#008300",
  "Social Media": "#EDA100",
  PSM: "#E87BA4",
  "Direct Type-in": "#5B6472",
  SEO: "#717B89",
  TV: "#6250D6",
  "Direct Referred": "#9EA9B8",
  Owned: "#B4BFCE",
  Instagram: "#1957D2",
  "Google ADS": "#EB6834",
  "Google Ads": "#EB6834",
  "Paid Search": "#1957D2",
  OTT: "#1BAF7A",
  "Connected TV": "#1BAF7A",
  Influencer: "#EDA100",
  Youtube: "#E34948",
  YouTube: "#E34948",
  Video: "#E34948",
  Newspaper: "#008300",
  Print: "#EB6834",
  TikTok: "#E87BA4",
  Facebook: "#4C8EDF",
  Meta: "#4C8EDF",
  LinkedIn: "#1B315B",
  Radio: "#9D6300",
  OOH: "#008300",
  Email: "#9D6300",
  Podcast: "#E87BA4",
  Programmatic: "#1BAF7A",
};
for (const [name, colour] of Object.entries({ ...CHANNEL_COLORS })) {
  CHANNEL_COLORS[name.toLowerCase().replace(/[\s-]+/g, "_")] = colour;
}

/** Channels without a fixed colour take these in order. */
export const FALLBACK_COLORS = ["#1957D2", "#EB6834", "#1BAF7A", "#E34948", "#008300", "#EDA100", "#E87BA4", "#6250D6", "#5B6472", "#1B315B"];

const dynamicChannelColors = new Map<string, string>();
const usedFallbackColors = new Set<string>();

/** A channel's colour by display name or slug; unknown channels take a distinct fallback colour. */
export function channelColor(name: string, index = 0): string {
  const slug = name.trim().toLowerCase().replace(/[\s-]+/g, "_");
  const known = CHANNEL_COLORS[name] ?? CHANNEL_COLORS[slug];
  if (known) return known;
  if (index > 0) return FALLBACK_COLORS[index % FALLBACK_COLORS.length];
  let assigned = dynamicChannelColors.get(slug);
  if (!assigned) {
    const avail = FALLBACK_COLORS.find((c) => !usedFallbackColors.has(c));
    assigned = avail ?? FALLBACK_COLORS[dynamicChannelColors.size % FALLBACK_COLORS.length];
    dynamicChannelColors.set(slug, assigned);
    usedFallbackColors.add(assigned);
  }
  return assigned;
}

/** Chart chrome shared by the figures built in the browser (the backend's are in `charts/theme.py`). */
export const CHART = {
  navy: "#1B315B", // dark series (actual sales), oklch(32% .08 262)
  grid: "#E8EBF1",
  axis: "#D9DFE6",
  muted: "#717B89",
};

export const FONT_SANS = '"IBM Plex Sans Variable", "IBM Plex Sans", ui-sans-serif, system-ui, -apple-system, "Segoe UI", sans-serif';
/** Code, file paths and column names only; numbers use NUMERIC. */
export const FONT_MONO = '"IBM Plex Mono", ui-monospace, "SF Mono", Menlo, Consolas, monospace';
/** Figures in KPIs and tables: the brand sans with tabular (equal-width) digits, so columns of numbers line up. */
export const NUMERIC = { fontFamily: FONT_SANS, fontVariantNumeric: "tabular-nums lining-nums" } as const;

/** Shell dimensions shared by the app bar, the sidebar and anything sticky below the app bar. */
export const LAYOUT = { headerHeight: 60, drawerWidth: 248, stickyTop: 84 } as const;

export const theme = createTheme({
  palette: {
    mode: "light",
    background: { default: PALETTE.bgCanvas, paper: PALETTE.bgCard },
    text: { primary: PALETTE.inkPrimary, secondary: PALETTE.inkSecondary },
    primary: { main: PALETTE.brandPrimary, dark: PALETTE.brandPrimaryDark, contrastText: "#FFFFFF" },
    // formare.dev has one accent; its secondary controls (outline buttons, toggles) are drawn in ink
    secondary: { main: PALETTE.inkPrimary, dark: PALETTE.brandNavy, light: PALETTE.inkSecondary, contrastText: "#FFFFFF" },
    error: { main: PALETTE.semanticBad },
    warning: { main: PALETTE.semanticWarn },
    info: { main: PALETTE.semanticInfo },
    success: { main: PALETTE.semanticGood },
    divider: PALETTE.borderSubtle,
  },
  typography: {
    fontFamily: FONT_SANS,
    h1: { fontSize: "1.75rem", lineHeight: 1.2, fontWeight: 600, letterSpacing: "-0.025em", color: PALETTE.inkPrimary },
    h2: { fontSize: "1.35rem", lineHeight: 1.3, fontWeight: 600, letterSpacing: "-0.02em", color: PALETTE.inkPrimary },
    h3: { fontSize: "1.0625rem", lineHeight: 1.4, fontWeight: 600, letterSpacing: "-0.01em", color: PALETTE.inkPrimary },
    body1: { fontSize: "0.9375rem", lineHeight: 1.6 },
    body2: { fontSize: "0.84rem", lineHeight: 1.55, color: PALETTE.inkSecondary },
    caption: { fontSize: "0.78rem", lineHeight: 1.45 },
    button: { fontWeight: 500, letterSpacing: 0 },
  },
  // formare.dev: 4 px controls (--radius), 8 px cards (--radius-lg)
  shape: { borderRadius: 8 },
  components: {
    MuiCssBaseline: {
      styleOverrides: {
        html: {
          textSizeAdjust: "100%",
          // In-page jumps ("On this page") glide unless the reader prefers reduced motion; route changes scroll instantly
          "@media (prefers-reduced-motion: no-preference)": { scrollBehavior: "smooth" },
          // The root scroller exposes its scroll state, so the app bar can lift once content slides under it
          containerType: "scroll-state",
          containerName: "page",
        },
        body: {
          backgroundColor: PALETTE.bgCanvas,
          fontVariantNumeric: "lining-nums",
          WebkitFontSmoothing: "antialiased",
          MozOsxFontSmoothing: "grayscale",
        },
        // Jump targets land below the fixed app bar instead of under it
        "[data-anchor]": { scrollMarginTop: LAYOUT.headerHeight + 16 },
        // Visible keyboard focus with ≥ 3:1 contrast on every surface (outline, not box-shadow, for forced colours)
        ":where(a, button, [role='button'], [role='tab'], input, select, textarea, [tabindex]):focus-visible": {
          outline: `2px solid ${PALETTE.brandPrimary}`,
          outlineOffset: "2px",
        },
        ".skip-link": {
          position: "fixed",
          insetInlineStart: 12,
          insetBlockStart: -60,
          zIndex: 2000,
          padding: "10px 16px",
          borderRadius: 4,
          background: PALETTE.brandNavy,
          color: "#FFFFFF",
          fontWeight: 600,
          textDecoration: "none",
          transition: "inset-block-start 0.15s ease-out",
          "&:focus-visible, &:focus": { insetBlockStart: 12 },
        },
        ".visually-hidden": {
          position: "absolute",
          width: 1,
          height: 1,
          padding: 0,
          margin: -1,
          overflow: "hidden",
          clip: "rect(0 0 0 0)",
          whiteSpace: "nowrap",
          border: 0,
        },
        "::selection": { background: PALETTE.brandPrimary, color: "#FFFFFF" },
      },
    },
    MuiPaper: {
      defaultProps: { elevation: 0 },
      styleOverrides: {
        // formare.dev cards: a hairline rule on white, no shadow
        root: { backgroundImage: "none", border: `1px solid ${PALETTE.borderSubtle}`, boxShadow: "none" },
      },
    },
    MuiButton: {
      defaultProps: { disableElevation: true },
      styleOverrides: {
        root: { textTransform: "none", borderRadius: 4, minHeight: 36, paddingInline: 18 },
        sizeSmall: { minHeight: 32, paddingInline: 14 },
        outlined: { borderColor: PALETTE.borderStrong, "&:hover": { borderColor: "currentColor" } },
      },
    },
    MuiIconButton: { styleOverrides: { root: { minWidth: 32, minHeight: 32 } } },
    MuiChip: { styleOverrides: { root: { fontWeight: 600, borderRadius: 4 } } },
    MuiOutlinedInput: {
      styleOverrides: {
        root: { borderRadius: 4 },
        notchedOutline: { borderColor: PALETTE.borderStrong },
      },
    },
    MuiTab: { styleOverrides: { root: { textTransform: "none", fontWeight: 600, minHeight: 44 } } },
    MuiTabs: { styleOverrides: { indicator: { backgroundColor: PALETTE.brandPrimary, height: 2 } } },
    MuiTableCell: {
      styleOverrides: {
        head: {
          fontWeight: 600,
          backgroundColor: PALETTE.bgSubtle,
          color: PALETTE.inkPrimary,
          fontSize: "0.76rem",
          textTransform: "uppercase",
          letterSpacing: "0.05em",
          // Long labels ("ROI Display Network") wrap so wide tables fit the card instead of hiding their last columns
          whiteSpace: "normal",
          verticalAlign: "bottom",
          lineHeight: 1.3,
          minWidth: 64,
        },
        body: { fontVariantNumeric: "tabular-nums", fontSize: "0.84rem", borderColor: PALETTE.borderSubtle },
        sizeSmall: { padding: "6px 12px" },
      },
    },
    MuiAlert: { styleOverrides: { root: { borderRadius: 8 } } },
    MuiLinearProgress: {
      styleOverrides: {
        // Indeterminate bars keep moving forever: slow them right down for people who prefer reduced motion
        bar: { "@media (prefers-reduced-motion: reduce)": { animationDuration: "6s" } },
      },
    },
    MuiCircularProgress: {
      styleOverrides: {
        root: { "@media (prefers-reduced-motion: reduce)": { animationDuration: "4s" } },
        circle: { "@media (prefers-reduced-motion: reduce)": { animation: "none", strokeDasharray: "60px, 200px" } },
      },
    },
    MuiTooltip: { styleOverrides: { tooltip: { backgroundColor: PALETTE.brandNavy, fontSize: "0.78rem", borderRadius: 4 } } },
  },
});
