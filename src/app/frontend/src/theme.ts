import { alpha, createTheme } from "@mui/material/styles";

/**
 * Colours follow the Tchibo design tokens used on tchibo.de (light theme): corporate gold, the interaction blue,
 * navy, coral for sale/negative states and warm neutral surfaces. The brand typeface (Tchibo Espresso) is
 * proprietary, so Figtree stands in for it.
 */
export const PALETTE = {
  bgCanvas: "#F9F6F1", // gold-96, corporate high surface
  bgCard: "#FFFFFF",
  bgSubtle: "#F6F2E9", // gold-94, corporate surface
  bgGoldTint: "#F1EBDF", // gold-91
  inkPrimary: "#21292F",
  inkSecondary: "#545C63", // movegray-36
  inkMuted: "#6C767F", // movegray-46 (≥ 4.5:1 on white)
  borderSubtle: "#E4DBC8", // gold-84
  borderStrong: "#C0A468", // gold-58, brand mark
  brandGold: "#8C723B", // gold-39, brand
  brandGoldDark: "#776132", // gold-33, brand variant
  brandGoldLight: "#C0A468",
  brandNavy: "#004266", // navy-20
  brandPrimary: "#0A63BD", // blue-39, interaction
  brandPrimaryDark: "#07427E", // blue-26
  brandSecondary: "#004266",
  accentWarm: "#8C723B",
  semanticGood: "#00753F", // green-23
  semanticWarn: "#9A6A12",
  semanticBad: "#BE2D2F", // coral-46
  semanticInfo: "#0A63BD",
};

export const FONT_SANS = '"Figtree", "Helvetica Neue", Arial, system-ui, sans-serif';
export const FONT_MONO = '"JetBrains Mono", ui-monospace, "SFMono-Regular", Menlo, monospace';

export const theme = createTheme({
  palette: {
    mode: "light",
    background: { default: PALETTE.bgCanvas, paper: PALETTE.bgCard },
    text: { primary: PALETTE.inkPrimary, secondary: PALETTE.inkSecondary },
    primary: { main: PALETTE.brandPrimary, dark: PALETTE.brandPrimaryDark, contrastText: "#FFFFFF" },
    secondary: { main: PALETTE.brandGold, dark: PALETTE.brandGoldDark, contrastText: "#FFFFFF" },
    error: { main: PALETTE.semanticBad },
    warning: { main: PALETTE.semanticWarn },
    info: { main: PALETTE.semanticInfo },
    success: { main: PALETTE.semanticGood },
    divider: PALETTE.borderSubtle,
  },
  typography: {
    fontFamily: FONT_SANS,
    h1: { fontSize: "1.75rem", lineHeight: 1.25, fontWeight: 700, letterSpacing: "-0.01em", color: PALETTE.inkPrimary },
    h2: { fontSize: "1.35rem", lineHeight: 1.3, fontWeight: 700, color: PALETTE.inkPrimary },
    h3: { fontSize: "1.0625rem", lineHeight: 1.4, fontWeight: 650, color: PALETTE.inkPrimary },
    body1: { fontSize: "0.9375rem", lineHeight: 1.6 },
    body2: { fontSize: "0.84rem", lineHeight: 1.5, color: PALETTE.inkSecondary },
    caption: { fontSize: "0.78rem", lineHeight: 1.45 },
    button: { fontWeight: 650, letterSpacing: "0.01em" },
  },
  shape: { borderRadius: 10 },
  components: {
    MuiCssBaseline: {
      styleOverrides: {
        html: { textSizeAdjust: "100%" },
        body: { backgroundColor: PALETTE.bgCanvas, fontVariantNumeric: "lining-nums" },
        // Visible keyboard focus with ≥ 3:1 contrast on every surface (outline, not box-shadow, for forced colours)
        ":where(a, button, [role='button'], [role='tab'], input, select, textarea, [tabindex]):focus-visible": {
          outline: `3px solid ${PALETTE.brandPrimary}`,
          outlineOffset: "2px",
        },
        ".skip-link": {
          position: "fixed",
          insetInlineStart: 12,
          insetBlockStart: -60,
          zIndex: 2000,
          padding: "10px 16px",
          borderRadius: 8,
          background: PALETTE.brandNavy,
          color: "#FFFFFF",
          fontWeight: 700,
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
        "::selection": { background: alpha(PALETTE.brandGoldLight, 0.45) },
      },
    },
    MuiPaper: {
      defaultProps: { elevation: 0 },
      styleOverrides: {
        root: {
          backgroundImage: "none",
          border: `1px solid ${PALETTE.borderSubtle}`,
          boxShadow: "0 1px 2px rgba(33, 41, 47, 0.04)",
        },
      },
    },
    MuiButton: {
      defaultProps: { disableElevation: true },
      styleOverrides: {
        root: { textTransform: "none", borderRadius: 999, minHeight: 36, paddingInline: 18 },
        sizeSmall: { minHeight: 32, paddingInline: 14 },
      },
    },
    MuiIconButton: { styleOverrides: { root: { minWidth: 32, minHeight: 32 } } },
    MuiChip: { styleOverrides: { root: { fontWeight: 600, borderRadius: 999 } } },
    MuiTab: { styleOverrides: { root: { textTransform: "none", fontWeight: 650, minHeight: 44 } } },
    MuiTabs: { styleOverrides: { indicator: { backgroundColor: PALETTE.brandGold, height: 3, borderRadius: 3 } } },
    MuiTableCell: {
      styleOverrides: {
        head: {
          fontWeight: 700,
          backgroundColor: PALETTE.bgSubtle,
          color: PALETTE.inkPrimary,
          fontSize: "0.76rem",
          textTransform: "uppercase",
          letterSpacing: "0.04em",
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
    MuiAlert: { styleOverrides: { root: { borderRadius: 10 } } },
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
    MuiTooltip: { styleOverrides: { tooltip: { backgroundColor: PALETTE.brandNavy, fontSize: "0.78rem" } } },
  },
});
