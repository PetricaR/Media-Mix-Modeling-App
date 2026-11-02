import { createTheme } from "@mui/material/styles";

export const PALETTE = {
  bgCanvas: "#F8F7F4",
  bgCard: "#FFFFFF",
  bgSubtle: "#F2EFE9",
  inkPrimary: "#16191D",
  inkSecondary: "#586069",
  inkMuted: "#8B939C",
  borderSubtle: "#E5E0D8",
  brandPrimary: "#1F3A5F",
  brandSecondary: "#2A7B76",
  accentWarm: "#B55232",
  semanticGood: "#1F7A4C",
  semanticWarn: "#B7791F",
  semanticBad: "#B83227",
  semanticInfo: "#2B6CB0",
};

export const theme = createTheme({
  palette: {
    mode: "light",
    background: {
      default: PALETTE.bgCanvas,
      paper: PALETTE.bgCard,
    },
    text: {
      primary: PALETTE.inkPrimary,
      secondary: PALETTE.inkSecondary,
    },
    primary: {
      main: PALETTE.brandPrimary,
    },
    secondary: {
      main: PALETTE.brandSecondary,
    },
    error: {
      main: PALETTE.semanticBad,
    },
    warning: {
      main: PALETTE.semanticWarn,
    },
    info: {
      main: PALETTE.semanticInfo,
    },
    success: {
      main: PALETTE.semanticGood,
    },
    divider: PALETTE.borderSubtle,
  },
  typography: {
    fontFamily: '"Inter", -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif',
    h1: {
      fontFamily: '"Fraunces", Georgia, serif',
      fontSize: "1.75rem",
      lineHeight: "2.25rem",
      fontWeight: 600,
      color: PALETTE.inkPrimary,
    },
    h2: {
      fontFamily: '"Fraunces", Georgia, serif',
      fontSize: "1.375rem",
      lineHeight: "1.875rem",
      fontWeight: 600,
      color: PALETTE.inkPrimary,
    },
    h3: {
      fontFamily: '"Inter", sans-serif',
      fontSize: "1.0625rem",
      lineHeight: "1.5rem",
      fontWeight: 600,
      color: PALETTE.inkPrimary,
    },
    body1: {
      fontSize: "0.9375rem",
      lineHeight: "1.5rem",
    },
    body2: {
      fontSize: "0.8125rem",
      lineHeight: "1.25rem",
      color: PALETTE.inkSecondary,
    },
  },
  shape: {
    borderRadius: 8,
  },
  components: {
    MuiPaper: {
      styleOverrides: {
        root: {
          backgroundImage: "none",
          border: `1px solid ${PALETTE.borderSubtle}`,
          boxShadow: "0 1px 2px rgba(22, 25, 29, 0.04)",
        },
      },
    },
    MuiButton: {
      styleOverrides: {
        root: {
          textTransform: "none",
          fontWeight: 600,
          borderRadius: 6,
        },
      },
    },
    MuiChip: {
      styleOverrides: {
        root: {
          fontWeight: 500,
        },
      },
    },
    MuiTableCell: {
      styleOverrides: {
        head: {
          fontWeight: 600,
          backgroundColor: PALETTE.bgSubtle,
          color: PALETTE.inkPrimary,
          fontSize: "0.78rem",
          textTransform: "uppercase",
          letterSpacing: "0.03em",
        },
        body: {
          fontVariantNumeric: "tabular-nums",
          fontSize: "0.84rem",
        },
      },
    },
  },
});
