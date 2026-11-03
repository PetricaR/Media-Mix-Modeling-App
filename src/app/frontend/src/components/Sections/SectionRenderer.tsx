import React, { Suspense, useId, useState } from "react";
import {
  Alert,
  AlertTitle,
  Box,
  Button,
  Paper,
  Skeleton,
  Stack,
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableRow,
  TableSortLabel,
  ToggleButton,
  ToggleButtonGroup,
  Tooltip,
  Typography,
} from "@mui/material";
import type { SxProps, Theme } from "@mui/material/styles";
import DownloadIcon from "@mui/icons-material/Download";
import HelpOutlineIcon from "@mui/icons-material/HelpOutline";
import HourglassEmptyOutlinedIcon from "@mui/icons-material/HourglassEmptyOutlined";
import { ApiError } from "../../api/client";
import type {
  CalloutBlock,
  ChartBlock,
  KpisBlock,
  MarkdownBlock,
  Section,
  SectionBlock,
  TableBlock,
} from "../../api/types";
import { FONT_MONO, FONT_SANS, PALETTE } from "../../theme";
import { Markdown } from "./Markdown";

const LazyPlotlyChart = React.lazy(() => import("../Charts/PlotlyChart"));

const toneColor = (tone?: string | null) => {
  switch (tone) {
    case "good":
    case "success":
      return PALETTE.semanticGood;
    case "warn":
      return PALETTE.semanticWarn;
    case "bad":
    case "danger":
      return PALETTE.semanticBad;
    case "info":
      return PALETTE.semanticInfo;
    default:
      return PALETTE.brandGold;
  }
};

const KpisRenderer: React.FC<{ block: KpisBlock }> = ({ block }) => (
  <Box
    component="ul"
    sx={{
      listStyle: "none",
      p: 0,
      m: 0,
      mb: 3,
      display: "grid",
      gap: 2,
      gridTemplateColumns: "repeat(auto-fit, minmax(min(100%, 210px), 1fr))",
    }}
  >
    {block.items.map((kpi, idx) => {
      const tone = kpi.tone || kpi.status || "neutral";
      const help = kpi.help || kpi.note;
      return (
        <Paper
          component="li"
          key={`${kpi.label}-${idx}`}
          sx={{ p: 2.25, borderTop: `3px solid ${toneColor(tone)}`, display: "flex", flexDirection: "column", gap: 0.75 }}
        >
          <Stack direction="row" alignItems="flex-start" justifyContent="space-between" spacing={0.5}>
            <Typography
              variant="caption"
              sx={{ fontWeight: 700, textTransform: "uppercase", letterSpacing: "0.04em", color: PALETTE.inkSecondary }}
            >
              {kpi.label}
            </Typography>
            {help && (
              <Tooltip title={help}>
                <HelpOutlineIcon tabIndex={0} aria-label={help} sx={{ fontSize: 16, color: PALETTE.inkMuted, cursor: "help" }} />
              </Tooltip>
            )}
          </Stack>
          <Typography
            sx={{
              fontFamily: FONT_MONO,
              fontSize: "1.4rem",
              fontWeight: 600,
              color: PALETTE.inkPrimary,
              fontVariantNumeric: "tabular-nums",
              overflowWrap: "anywhere",
              lineHeight: 1.25,
            }}
          >
            {kpi.value}
          </Typography>
          {kpi.delta && (
            <Typography variant="caption" sx={{ color: tone === "neutral" ? PALETTE.inkSecondary : toneColor(tone), fontWeight: 600 }}>
              {kpi.delta}
            </Typography>
          )}
        </Paper>
      );
    })}
  </Box>
);

const CalloutRenderer: React.FC<{ block: CalloutBlock }> = ({ block }) => {
  const severityMap: Record<string, "info" | "success" | "warning" | "error"> = {
    info: "info",
    good: "success",
    success: "success",
    warn: "warning",
    bad: "error",
    danger: "error",
  };
  return (
    <Alert
      severity={severityMap[block.tone] || "info"}
      variant="outlined"
      role="note"
      sx={{ mb: 3, backgroundColor: "#FFFFFF", borderLeftWidth: 4, "& .MuiAlert-message": { width: "100%" } }}
    >
      <AlertTitle sx={{ fontWeight: 700 }}>{block.title}</AlertTitle>
      <Markdown source={block.body} dense />
    </Alert>
  );
};

const MarkdownRenderer: React.FC<{ block: MarkdownBlock }> = ({ block }) => (
  <Paper sx={{ p: { xs: 2, md: 3 }, mb: 3 }}>
    {block.title && (
      <Typography variant="h3" component="h2" sx={{ mb: 1.5 }}>
        {block.title}
      </Typography>
    )}
    <Markdown source={block.body} />
  </Paper>
);

const BlockHeader: React.FC<{ id?: string; title: string; subtitle?: string | null; actions?: React.ReactNode }> = ({
  id,
  title,
  subtitle,
  actions,
}) => (
  <Stack direction={{ xs: "column", sm: "row" }} justifyContent="space-between" alignItems={{ xs: "flex-start", sm: "flex-start" }} spacing={1} sx={{ mb: 1.5 }}>
    <Box sx={{ minWidth: 0 }}>
      <Typography id={id} variant="h3" component="h2">
        {title}
      </Typography>
      {subtitle && (
        <Typography variant="body2" sx={{ mt: 0.25 }}>
          {subtitle}
        </Typography>
      )}
    </Box>
    {actions}
  </Stack>
);

const ChartRenderer: React.FC<{ block: ChartBlock }> = ({ block }) => {
  const variantKeys = block.figure.variants ? Object.keys(block.figure.variants) : [];
  const [selectedVariant, setSelectedVariant] = useState<string>(variantKeys[0] || "default");
  const titleId = useId();

  const activeSpec =
    variantKeys.length > 0 && block.figure.variants && block.figure.variants[selectedVariant]
      ? block.figure.variants[selectedVariant]
      : { data: block.figure.data, layout: block.figure.layout };

  return (
    <Paper component="figure" sx={{ p: { xs: 1.5, md: 2.5 }, mb: 3, mx: 0 }} id={block.chart_id} aria-labelledby={titleId}>
      <BlockHeader
        id={titleId}
        title={block.title}
        subtitle={block.subtitle}
        actions={
          variantKeys.length > 0 ? (
            <ToggleButtonGroup
              size="small"
              exclusive
              value={selectedVariant}
              onChange={(_, val) => val && setSelectedVariant(val)}
              aria-label="Correlation method"
            >
              {variantKeys.map((vk) => (
                <ToggleButton key={vk} value={vk} sx={{ textTransform: "capitalize", px: 1.5 }}>
                  {vk}
                </ToggleButton>
              ))}
            </ToggleButtonGroup>
          ) : null
        }
      />
      <Suspense fallback={<Skeleton variant="rounded" height={block.height || 380} />}>
        <LazyPlotlyChart data={activeSpec.data} layout={activeSpec.layout} height={block.height || 420} ariaLabel={block.title} />
      </Suspense>
      {(block.caption || block.note) && (
        <Typography
          component="figcaption"
          variant="caption"
          sx={{ display: "block", mt: 1.5, pt: 1, borderTop: `1px solid ${PALETTE.borderSubtle}`, color: PALETTE.inkSecondary }}
        >
          {block.caption || block.note}
        </Typography>
      )}
    </Paper>
  );
};

/** Numeric value of a formatted cell ("€1.2M", "−8.5%", "×3.7", "0.97×") so sorting follows magnitude, not text. */
function sortValue(v: unknown): number | string {
  if (typeof v === "number") return v;
  const s = String(v ?? "").trim();
  const m = /^([+\-−]?)\s*[€×]?\s*([0-9][0-9,]*\.?[0-9]*)\s*([kM])?/.exec(s.replace(/^\[/, ""));
  if (!m) return s.toLowerCase();
  let n = parseFloat(m[2].replace(/,/g, ""));
  if (m[3] === "k") n *= 1e3;
  if (m[3] === "M") n *= 1e6;
  return m[1] === "-" || m[1] === "−" ? -n : n;
}

const TableRenderer: React.FC<{ block: TableBlock }> = ({ block }) => {
  const [orderBy, setOrderBy] = useState<string | null>(null);
  const [orderDir, setOrderDir] = useState<"asc" | "desc">("asc");
  const captionId = useId();

  const handleSort = (colKey: string) => {
    if (orderBy === colKey) {
      setOrderDir(orderDir === "asc" ? "desc" : "asc");
    } else {
      setOrderBy(colKey);
      setOrderDir("asc");
    }
  };

  const sortedRows = React.useMemo(() => {
    if (!orderBy) return block.rows;
    return [...block.rows].sort((a, b) => {
      const va = sortValue(a[orderBy]);
      const vb = sortValue(b[orderBy]);
      const cmp = typeof va === "number" && typeof vb === "number" ? va - vb : String(va).localeCompare(String(vb));
      return orderDir === "asc" ? cmp : -cmp;
    });
  }, [block.rows, orderBy, orderDir]);

  const exportCsv = () => {
    const headers = block.columns.map((c) => `"${c.label.replace(/"/g, '""')}"`).join(",");
    const lines = sortedRows.map((r) =>
      block.columns.map((c) => `"${String(r[c.key] ?? "").replace(/"/g, '""')}"`).join(",")
    );
    const blob = new Blob([[headers, ...lines].join("\n")], { type: "text/csv;charset=utf-8;" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `${block.table_id || block.id || "table"}.csv`;
    a.click();
    URL.revokeObjectURL(url);
  };

  const formatCell = (val: unknown, fmt?: string | null) => {
    if (val === null || val === undefined || val === "") return "—";
    if (typeof val === "number") {
      switch (fmt) {
        case "eur":
        case "eur_0":
          return `€${val.toLocaleString("en-GB", { maximumFractionDigits: 0 })}`;
        case "pct":
        case "pct_1":
          return `${val.toLocaleString("en-GB", { minimumFractionDigits: 1, maximumFractionDigits: 1 })}%`;
        case "roas":
        case "ratio_2":
        case "float_2":
          return val.toLocaleString("en-GB", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
        case "int":
          return Math.round(val).toLocaleString("en-GB");
        case "float_1":
          return val.toLocaleString("en-GB", { minimumFractionDigits: 1, maximumFractionDigits: 1 });
        case "float_3":
          return val.toLocaleString("en-GB", { minimumFractionDigits: 3, maximumFractionDigits: 3 });
        default:
          return Number.isInteger(val) ? val.toLocaleString("en-GB") : val.toLocaleString("en-GB", { maximumFractionDigits: 3 });
      }
    }
    return String(val);
  };

  const isNumericLike = (v: unknown) => typeof v === "number" || /^[+\-−]?[€×]?\s?[0-9]/.test(String(v ?? ""));
  const longText = (key: string) => block.rows.some((r) => String(r[key] ?? "").length > 40);

  const tableSx: SxProps<Theme> = {
    // Wide tables (e.g. walk-forward folds) get tighter cells so they fit a laptop-width card
    ...(block.columns.length >= 10 ? { "& .MuiTableCell-sizeSmall": { px: 1 } } : {}),
    // Narrow containers (phones): each row becomes a card labelled from data-label, as in the responsive-table guide
    "@container (max-width: 599px)": {
      "&, & tbody, & tr, & th, & td": { display: "block" },
      "& thead": { display: "none" },
      "& tbody tr": { py: 1, borderBottom: `1px solid ${PALETTE.borderSubtle}` },
      "& tbody tr:last-of-type": { borderBottom: "none" },
      "& tbody th": { border: "none", whiteSpace: "normal", minWidth: 0, pb: 0.5, fontWeight: 700 },
      "& tbody td": {
        display: "flex",
        flexDirection: "row", // MUI's right-aligned cells use row-reverse, which would put the label after the value
        justifyContent: "space-between",
        alignItems: "baseline",
        gap: 2,
        border: "none",
        py: 0.25,
        whiteSpace: "normal",
        minWidth: 0,
        textAlign: "right",
      },
      "& tbody td::before": {
        content: "attr(data-label)",
        flexShrink: 0,
        maxWidth: "50%",
        textAlign: "left",
        fontFamily: FONT_SANS,
        fontSize: "0.78rem",
        fontWeight: 600,
        color: PALETTE.inkSecondary,
      },
    },
  };

  return (
    <Paper sx={{ p: { xs: 1.5, md: 2.5 }, mb: 3 }} id={block.table_id || block.id}>
      <BlockHeader
        id={captionId}
        title={block.title}
        subtitle={block.subtitle}
        actions={
          <Button size="small" variant="outlined" startIcon={<DownloadIcon fontSize="small" />} onClick={exportCsv} aria-label={`Download "${block.title}" as CSV`}>
            CSV
          </Button>
        }
      />
      <Box
        role="region"
        aria-labelledby={captionId}
        tabIndex={0}
        sx={{
          overflow: "auto",
          maxBlockSize: "min(560px, 75vh)",
          border: `1px solid ${PALETTE.borderSubtle}`,
          borderRadius: 1.5,
          containerType: "inline-size",
        }}
      >
        <Table size="small" stickyHeader sx={tableSx}>
          <caption className="visually-hidden">{block.title}</caption>
          <TableHead>
            <TableRow>
              {block.columns.map((col) => (
                <TableCell key={col.key} align={col.align || "left"} scope="col" sortDirection={orderBy === col.key ? orderDir : false}>
                  <TableSortLabel
                    active={orderBy === col.key}
                    direction={orderBy === col.key ? orderDir : "asc"}
                    onClick={() => handleSort(col.key)}
                    sx={{ "& .MuiTableSortLabel-icon": { fontSize: 16, mx: 0.25 } }}
                  >
                    {col.label}
                  </TableSortLabel>
                </TableCell>
              ))}
            </TableRow>
          </TableHead>
          <TableBody>
            {sortedRows.map((row, rIdx) => (
              <TableRow key={rIdx} hover>
                {block.columns.map((col, cIdx) => {
                  const val = row[col.key];
                  const numeric = isNumericLike(val) && !longText(col.key);
                  return (
                    <TableCell
                      key={col.key}
                      component={cIdx === 0 ? "th" : "td"}
                      scope={cIdx === 0 ? "row" : undefined}
                      data-label={cIdx === 0 ? undefined : col.label}
                      align={col.align || (typeof val === "number" ? "right" : "left")}
                      sx={{
                        fontFamily: numeric ? FONT_MONO : "inherit",
                        fontSize: numeric ? "0.8rem" : undefined,
                        fontWeight: cIdx === 0 ? 600 : undefined,
                        // Text columns and the row header may wrap; numbers never do
                        whiteSpace: longText(col.key) || (cIdx === 0 && !numeric) ? "normal" : "nowrap",
                        minWidth: longText(col.key) ? 240 : cIdx === 0 && !numeric ? 140 : undefined,
                        verticalAlign: "top",
                      }}
                    >
                      {formatCell(val, col.format)}
                    </TableCell>
                  );
                })}
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </Box>
      {(block.caption || block.note) && (
        <Typography variant="caption" sx={{ display: "block", mt: 1.5, color: PALETTE.inkSecondary }}>
          {block.caption || block.note}
        </Typography>
      )}
    </Paper>
  );
};

export const BlockRenderer: React.FC<{ block: SectionBlock }> = ({ block }) => {
  switch (block.type) {
    case "kpis":
      return <KpisRenderer block={block} />;
    case "callout":
      return <CalloutRenderer block={block} />;
    case "markdown":
      return <MarkdownRenderer block={block} />;
    case "chart":
      return <ChartRenderer block={block} />;
    case "table":
      return <TableRenderer block={block} />;
    default:
      return null;
  }
};

export const SectionSkeleton: React.FC = () => (
  <Box aria-busy="true" aria-label="Loading section">
    <Skeleton variant="text" width="45%" height={44} />
    <Skeleton variant="text" width="70%" />
    <Box sx={{ display: "grid", gap: 2, gridTemplateColumns: "repeat(auto-fit, minmax(210px, 1fr))", my: 3 }}>
      {[0, 1, 2, 3].map((i) => (
        <Skeleton key={i} variant="rounded" height={104} />
      ))}
    </Box>
    <Skeleton variant="rounded" height={360} />
  </Box>
);

export const EmptyState: React.FC<{ title: string; children?: React.ReactNode; action?: React.ReactNode }> = ({ title, children, action }) => (
  <Paper sx={{ p: { xs: 3, md: 5 }, textAlign: "center", borderStyle: "dashed", backgroundColor: PALETTE.bgSubtle }}>
    <HourglassEmptyOutlinedIcon sx={{ fontSize: 36, color: PALETTE.brandGold, mb: 1 }} aria-hidden="true" />
    <Typography variant="h3" component="h2" sx={{ mb: 0.75 }}>
      {title}
    </Typography>
    {children && (
      <Typography variant="body2" sx={{ maxWidth: 560, mx: "auto" }}>
        {children}
      </Typography>
    )}
    {action && <Box sx={{ mt: 2 }}>{action}</Box>}
  </Paper>
);

export const SectionRenderer: React.FC<{
  section?: Section | null;
  isLoading?: boolean;
  error?: Error | null;
  emptyMessage?: string;
  pendingMessage?: string;
}> = ({ section, isLoading, error, emptyMessage, pendingMessage }) => {
  if (isLoading) return <SectionSkeleton />;

  if (!section) {
    const notReady = error instanceof ApiError && error.status === 404;
    if (error && !notReady) {
      return (
        <Alert severity="error" sx={{ my: 2 }}>
          <AlertTitle>Could not load this page</AlertTitle>
          {error.message}
        </Alert>
      );
    }
    return (
      <EmptyState title={notReady ? "Not computed yet" : "No run selected"}>
        {notReady
          ? pendingMessage || "This page fills in automatically as soon as the pipeline stage that produces it has finished."
          : emptyMessage || "Upload the workbook on the Runs page, or pick a run in the header."}
      </EmptyState>
    );
  }

  return (
    <Box component="article" aria-labelledby={`section-${section.id}-title`}>
      <Box sx={{ mb: 3 }}>
        <Typography id={`section-${section.id}-title`} variant="h1" sx={{ mb: 0.5 }}>
          {section.title}
        </Typography>
        {section.subtitle && (
          <Typography variant="body1" sx={{ color: PALETTE.inkSecondary, maxWidth: "75ch" }}>
            {section.subtitle}
          </Typography>
        )}
      </Box>
      {section.blocks.map((block, idx) => (
        <BlockRenderer key={idx} block={block} />
      ))}
    </Box>
  );
};
