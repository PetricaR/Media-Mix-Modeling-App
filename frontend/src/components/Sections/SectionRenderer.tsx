import React, { Suspense, useCallback, useContext, useId, useRef, useState } from "react";
import {
  Alert,
  AlertTitle,
  Box,
  Button,
  IconButton,
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
import FolderOpenOutlinedIcon from "@mui/icons-material/FolderOpenOutlined";
import ExpandMoreIcon from "@mui/icons-material/ExpandMore";
import ShowChartIcon from "@mui/icons-material/ShowChart";
import TableRowsOutlinedIcon from "@mui/icons-material/TableRowsOutlined";
import ArrowForwardIcon from "@mui/icons-material/ArrowForward";
import { Link as RouterLink } from "react-router-dom";
import { ApiError } from "../../api/client";
import type { CalloutBlock, ChartBlock, KpiCard, KpisBlock, MarkdownBlock, Section, SectionBlock, TableBlock } from "../../api/types";
import { FONT_SANS, NUMERIC, PALETTE } from "../../theme";
import { ExplainButton, ExplainScope } from "../Explain/ExplainButton";
import { PageHeader } from "../Layout/PageHeader";
import { Markdown } from "./Markdown";
import { OnThisPage, type TocEntry } from "./OnThisPage";
import { curateSection, type CuratedBlock, type SectionCuration } from "./curation";
import { escapeCsvCell } from "../../lib/csv";
import { supportsUntilFound, useHiddenUntilFound } from "../../lib/untilFound";

const LazyPlotlyChart = React.lazy(() => import("../Charts/PlotlyChart"));

export const toneColor = (tone?: string | null) => {
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
      return null;
  }
};

/** A KPI tile; `to` adds a link to the page that is the number's home (used by the Summary). */
export type KpiItem = KpiCard & { to?: string; linkLabel?: string };

/**
 * The page's headline numbers as one strip: label, value, and a context line coloured only when it carries a verdict
 * (met / at risk). Cells are separated by hairlines drawn by each cell, so a wrapped last row leaves no grey gaps.
 */
export const KpiStrip: React.FC<{ items: KpiItem[]; label?: string }> = ({ items, label }) => (
  <Box
    component="ul"
    aria-label={label}
    sx={{
      listStyle: "none",
      p: 0,
      m: 0,
      mb: 3,
      display: "grid",
      gridTemplateColumns: { xs: "repeat(2, minmax(0, 1fr))", sm: "repeat(auto-fit, minmax(200px, 1fr))" },
      backgroundColor: PALETTE.bgCard,
      border: `1px solid ${PALETTE.borderSubtle}`,
      borderRadius: 2,
      overflow: "hidden",
    }}
  >
    {items.map((kpi, idx) => {
      const tone = toneColor(kpi.tone || kpi.status);
      const help = kpi.help || kpi.note;
      return (
        <Box
          component="li"
          key={`${kpi.label}-${idx}`}
          sx={{
            p: { xs: 1.5, sm: 2.25 },
            display: "flex",
            flexDirection: "column",
            gap: 0.5,
            minWidth: 0,
            boxShadow: `1px 0 0 0 ${PALETTE.borderSubtle}, 0 1px 0 0 ${PALETTE.borderSubtle}`,
          }}
        >
          <Stack direction="row" alignItems="flex-start" justifyContent="space-between" spacing={0.5}>
            <Typography sx={{ fontSize: "0.8rem", fontWeight: 600, lineHeight: 1.35, color: PALETTE.inkSecondary }}>{kpi.label}</Typography>
            {help && (
              <Tooltip title={help}>
                {/* The focusable wrapper carries the name; MUI icons are aria-hidden */}
                <Box component="span" tabIndex={0} role="img" aria-label={help} sx={{ display: "inline-flex", cursor: "help", flexShrink: 0, borderRadius: "50%" }}>
                  <HelpOutlineIcon sx={{ fontSize: 16, color: PALETTE.inkMuted }} />
                </Box>
              </Tooltip>
            )}
          </Stack>
          <Typography
            sx={{
              ...NUMERIC,
              fontSize: { xs: "1.3rem", sm: "1.65rem" },
              fontWeight: 650,
              letterSpacing: "-0.02em",
              lineHeight: 1.15,
              color: PALETTE.inkPrimary,
              overflowWrap: "anywhere",
            }}
          >
            {kpi.value}
          </Typography>
          {kpi.delta && (
            <Typography variant="caption" sx={{ color: tone ?? PALETTE.inkSecondary, fontWeight: tone ? 600 : 450, display: "flex", gap: 0.75, alignItems: "baseline" }}>
              {tone && (
                <Box component="span" aria-hidden="true" sx={{ width: 7, height: 7, borderRadius: "50%", backgroundColor: tone, flexShrink: 0, transform: "translateY(-1px)" }} />
              )}
              <span>{kpi.delta}</span>
            </Typography>
          )}
          {kpi.to && (
            <Box
              component={RouterLink}
              to={kpi.to}
              sx={{ mt: "auto", pt: 0.75, display: "inline-flex", alignItems: "center", gap: 0.5, fontSize: "0.8rem", fontWeight: 600, color: PALETTE.brandPrimary, textDecoration: "none", "&:hover": { textDecoration: "underline" } }}
            >
              {kpi.linkLabel ?? "Details"}
              <ArrowForwardIcon sx={{ fontSize: 15 }} aria-hidden="true" />
            </Box>
          )}
        </Box>
      );
    })}
  </Box>
);

const KpisRenderer: React.FC<{ block: KpisBlock }> = ({ block }) => <KpiStrip items={block.items} />;

export const CalloutRenderer: React.FC<{ block: CalloutBlock }> = ({ block }) => {
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
      sx={{ mb: 3, backgroundColor: PALETTE.bgCard, borderLeftWidth: 4, "& .MuiAlert-message": { width: "100%" } }}
    >
      <AlertTitle sx={{ fontWeight: 650 }}>{block.title}</AlertTitle>
      <Markdown source={block.body} dense />
    </Alert>
  );
};

const MarkdownRenderer: React.FC<{ block: MarkdownBlock; anchorId?: string }> = ({ block, anchorId }) => (
  <Paper sx={{ p: { xs: 2, md: 3 }, mb: 3 }} id={anchorId} data-anchor={anchorId ? "" : undefined}>
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
  <Stack direction={{ xs: "column", sm: "row" }} justifyContent="space-between" alignItems="flex-start" spacing={1} sx={{ mb: 1.5 }}>
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
    {actions && (
      <Stack direction="row" spacing={0.75} alignItems="center" flexWrap="wrap" useFlexGap sx={{ flexShrink: 0 }}>
        {actions}
      </Stack>
    )}
  </Stack>
);

const Caption: React.FC<{ text?: string | null; rule?: boolean }> = ({ text, rule }) =>
  text ? (
    <Typography
      component="p"
      variant="caption"
      sx={{ display: "block", mt: 1.5, ...(rule ? { pt: 1, borderTop: `1px solid ${PALETTE.borderSubtle}` } : {}), color: PALETTE.inkSecondary }}
    >
      {text}
    </Typography>
  ) : null;

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

/** A cell as the table displays it (also what Explain and the CSV export see). */
export function formatCell(val: unknown, fmt?: string | null): string {
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
}

const tableData = (block: TableBlock) => ({
  columns: block.columns.map((c) => c.label),
  rows: block.rows.map((r) => block.columns.map((c) => formatCell(r[c.key], c.format))),
});

function downloadCsv(block: TableBlock) {
  const headers = block.columns.map((c) => escapeCsvCell(c.label)).join(",");
  const lines = block.rows.map((r) => block.columns.map((c) => escapeCsvCell(r[c.key])).join(","));
  const blob = new Blob([[headers, ...lines].join("\n")], { type: "text/csv;charset=utf-8;" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = `${block.table_id || block.id || "table"}.csv`;
  a.click();
  URL.revokeObjectURL(url);
}

const CsvButton: React.FC<{ block: TableBlock }> = ({ block }) => (
  <Tooltip title="Download CSV">
    <IconButton size="small" onClick={() => downloadCsv(block)} aria-label={`Download "${block.title}" as CSV`}>
      <DownloadIcon fontSize="small" />
    </IconButton>
  </Tooltip>
);

/** The sortable table itself (inside a table card, or as a chart card's "Table" view). */
const TableView: React.FC<{ block: TableBlock; labelledBy: string }> = ({ block, labelledBy }) => {
  const [orderBy, setOrderBy] = useState<string | null>(null);
  const [orderDir, setOrderDir] = useState<"asc" | "desc">("asc");

  const handleSort = (colKey: string) => {
    if (orderBy === colKey) setOrderDir(orderDir === "asc" ? "desc" : "asc");
    else {
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

  const isNumericLike = (v: unknown) => typeof v === "number" || /^[+\-−]?[€×]?\s?[0-9]/.test(String(v ?? ""));
  const longText = (key: string) => block.rows.some((r) => String(r[key] ?? "").length > 40);
  // A short table of prose (the brief's answers, decisions) is read top to bottom, not scrolled inside its card
  const prose = block.rows.length <= 12 && block.columns.some((c) => longText(c.key));

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
    <Box
      role="region"
      aria-labelledby={labelledBy}
      tabIndex={0}
      sx={{
        overflow: "auto",
        maxBlockSize: prose ? undefined : "min(560px, 75vh)",
        border: `1px solid ${PALETTE.borderSubtle}`,
        borderRadius: 1,
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
                const numeric = cIdx > 0 && isNumericLike(val) && !longText(col.key);
                return (
                  <TableCell
                    key={col.key}
                    component={cIdx === 0 ? "th" : "td"}
                    scope={cIdx === 0 ? "row" : undefined}
                    data-label={cIdx === 0 ? undefined : col.label}
                    align={col.align || (typeof val === "number" ? "right" : "left")}
                    sx={{
                      ...(numeric ? NUMERIC : {}),
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
  );
};

const TableCard: React.FC<{ block: TableBlock; anchorId?: string }> = ({ block, anchorId }) => {
  const captionId = useId();
  return (
    <Paper sx={{ p: { xs: 1.5, md: 2.5 }, mb: 3 }} id={anchorId ?? (block.table_id || block.id)} data-anchor="">
      <BlockHeader
        id={captionId}
        title={block.title}
        subtitle={block.subtitle}
        actions={
          <>
            <ExplainButton kind="table" title={block.title} subtitle={block.subtitle} caption={block.caption || block.note} table={() => tableData(block)} />
            <CsvButton block={block} />
          </>
        }
      />
      <TableView block={block} labelledBy={captionId} />
      <Caption text={block.caption || block.note} />
    </Paper>
  );
};

/** Charts inside a closed "More analysis" group wait until it is first opened: Plotly cannot size a hidden chart. */
const DeferCharts = React.createContext(false);

/** True while the chart sits in a "More analysis" group that has not been opened yet. */
export const useChartsDeferred = () => useContext(DeferCharts);

const ViewToggle: React.FC<{ value: "chart" | "table"; onChange: (v: "chart" | "table") => void; title: string }> = ({ value, onChange, title }) => (
  <ToggleButtonGroup
    size="small"
    exclusive
    value={value}
    onChange={(_, v) => v && onChange(v)}
    aria-label={`Show "${title}" as`}
    sx={{ "& .MuiToggleButton-root": { px: 1, py: 0.25, gap: 0.5, fontSize: "0.78rem", textTransform: "none" } }}
  >
    <ToggleButton value="chart">
      <ShowChartIcon sx={{ fontSize: 17 }} aria-hidden="true" />
      Chart
    </ToggleButton>
    <ToggleButton value="table">
      <TableRowsOutlinedIcon sx={{ fontSize: 17 }} aria-hidden="true" />
      Table
    </ToggleButton>
  </ToggleButtonGroup>
);

/**
 * A chart card. With a paired table the card has a Chart / Table switch, so the numbers behind the chart live in the
 * same card instead of a second card below it. The inactive table stays in the DOM as `hidden="until-found"`, so find
 * in page still reaches its cells and switches the card to it; without that feature the table is shown below the
 * chart (the guide's fallback for exclusive panels).
 */
const ChartCard: React.FC<{ block: ChartBlock; table?: TableBlock; tableFirst?: boolean; anchorId?: string }> = ({ block, table, tableFirst, anchorId }) => {
  const deferred = useContext(DeferCharts);
  const variantKeys = block.figure.variants ? Object.keys(block.figure.variants) : [];
  const [selectedVariant, setSelectedVariant] = useState<string>(variantKeys[0] || "default");
  const toggleable = Boolean(table) && supportsUntilFound;
  const [view, setView] = useState<"chart" | "table">(toggleable && tableFirst ? "table" : "chart");
  const titleId = useId();
  const tablePanel = useRef<HTMLDivElement>(null);
  const reveal = useCallback(() => setView("table"), []);
  useHiddenUntilFound(tablePanel, toggleable && view === "chart", reveal);

  const activeSpec =
    variantKeys.length > 0 && block.figure.variants && block.figure.variants[selectedVariant]
      ? block.figure.variants[selectedVariant]
      : { data: block.figure.data, layout: block.figure.layout };
  const showChart = !toggleable || view === "chart";
  const height = block.height || 420;

  return (
    <Paper component="figure" sx={{ p: { xs: 1.5, md: 2.5 }, mb: 3, mx: 0 }} id={anchorId ?? block.chart_id} data-anchor="" aria-labelledby={titleId}>
      <BlockHeader
        id={titleId}
        title={block.title}
        subtitle={block.subtitle}
        actions={
          <>
            {showChart && variantKeys.length > 0 && (
              <ToggleButtonGroup size="small" exclusive value={selectedVariant} onChange={(_, val) => val && setSelectedVariant(val)} aria-label="Chart variant">
                {variantKeys.map((vk) => (
                  <ToggleButton key={vk} value={vk} sx={{ textTransform: "none", px: 1.25, py: 0.25, fontSize: "0.78rem" }}>
                    {vk}
                  </ToggleButton>
                ))}
              </ToggleButtonGroup>
            )}
            {toggleable && <ViewToggle value={view} onChange={setView} title={block.title} />}
            {showChart ? (
              <ExplainButton kind="chart" title={block.title} subtitle={block.subtitle} caption={block.caption || block.note} figure={activeSpec} />
            ) : (
              table && <ExplainButton kind="table" title={table.title} subtitle={table.subtitle} caption={table.caption || table.note} table={() => tableData(table)} />
            )}
            {table && !showChart && <CsvButton block={table} />}
          </>
        }
      />
      {showChart &&
        (deferred ? (
          <Box sx={{ height, borderRadius: 1, backgroundColor: PALETTE.bgSubtle }} aria-hidden="true" />
        ) : (
          <Suspense fallback={<Skeleton variant="rounded" height={height} />}>
            <LazyPlotlyChart data={activeSpec.data} layout={activeSpec.layout} height={height} ariaLabel={block.title} viewKey={selectedVariant} />
          </Suspense>
        ))}
      {showChart && <Caption text={block.caption || block.note} rule />}
      {table && (
        <Box ref={tablePanel} sx={toggleable ? undefined : { mt: 2.5 }}>
          {!toggleable && (
            <Stack direction="row" justifyContent="space-between" alignItems="center" spacing={1} sx={{ mb: 1 }}>
              <Typography id={`${titleId}-table`} variant="body2" sx={{ fontWeight: 600, color: PALETTE.inkPrimary }}>
                {table.title}
              </Typography>
              <CsvButton block={table} />
            </Stack>
          )}
          <TableView block={table} labelledBy={toggleable ? titleId : `${titleId}-table`} />
          <Caption text={table.caption || table.note} />
        </Box>
      )}
    </Paper>
  );
};

export const BlockRenderer: React.FC<{ block: SectionBlock; anchorId?: string; table?: TableBlock; tableFirst?: boolean }> = ({ block, anchorId, table, tableFirst }) => {
  switch (block.type) {
    case "kpis":
      return <KpisRenderer block={block} />;
    case "callout":
      return <CalloutRenderer block={block} />;
    case "markdown":
      return <MarkdownRenderer block={block} anchorId={anchorId} />;
    case "chart":
      return <ChartCard block={block} anchorId={anchorId} table={table} tableFirst={tableFirst} />;
    case "table":
      return <TableCard block={block} anchorId={anchorId} />;
    default:
      return null;
  }
};

/**
 * "More analysis": the section's secondary blocks in a native `<details>` (closed by default, keyboard and screen
 * reader ready, and find in page opens it). Its charts are drawn on first open.
 */
export const MoreAnalysis: React.FC<{ id?: string; count: number; label?: string; children: React.ReactNode }> = ({ id, count, label = "More analysis", children }) => {
  const [opened, setOpened] = useState(false);
  return (
    <Box
      component="details"
      id={id}
      data-anchor=""
      onToggle={(e: React.SyntheticEvent<HTMLDetailsElement>) => {
        if (e.currentTarget.open) setOpened(true);
      }}
      sx={{
        mb: 3,
        borderTop: `1px solid ${PALETTE.borderSubtle}`,
        "&[open] > summary .more-chevron": { transform: "rotate(180deg)" },
        "& > summary": { listStyle: "none" },
        "& > summary::-webkit-details-marker": { display: "none" },
      }}
    >
      <Box
        component="summary"
        sx={{
          display: "flex",
          alignItems: "center",
          gap: 1,
          py: 1.5,
          cursor: "pointer",
          fontWeight: 600,
          fontSize: "0.92rem",
          color: PALETTE.inkPrimary,
          borderRadius: 1,
          "&:hover": { color: PALETTE.brandPrimary },
          "&:focus-visible": { outline: `3px solid ${PALETTE.brandPrimary}`, outlineOffset: 2 },
        }}
      >
        <ExpandMoreIcon className="more-chevron" fontSize="small" sx={{ transition: "transform 0.15s", "@media (prefers-reduced-motion: reduce)": { transition: "none" } }} />
        {label}
        <Box component="span" sx={{ fontWeight: 450, color: PALETTE.inkMuted, ...NUMERIC }}>
          {count} {count === 1 ? "item" : "items"}
        </Box>
      </Box>
      <DeferCharts.Provider value={!opened}>
        <Box sx={{ pt: 1 }}>{children}</Box>
      </DeferCharts.Provider>
    </Box>
  );
};

export const SectionSkeleton: React.FC<{ withTitle?: boolean }> = ({ withTitle = false }) => (
  <Box aria-busy="true" aria-label="Loading section">
    {withTitle && (
      <>
        <Skeleton variant="text" width="45%" height={44} />
        <Skeleton variant="text" width="70%" />
      </>
    )}
    <Skeleton variant="rounded" height={112} sx={{ mb: 3 }} />
    <Skeleton variant="rounded" height={360} />
  </Box>
);

export const EmptyState: React.FC<{ title: string; children?: React.ReactNode; action?: React.ReactNode; icon?: React.ReactNode }> = ({
  title,
  children,
  action,
  icon,
}) => (
  <Paper sx={{ p: { xs: 3, md: 5 }, textAlign: "center", borderStyle: "dashed", backgroundColor: PALETTE.bgSubtle }}>
    <Box aria-hidden="true" sx={{ color: PALETTE.brandPrimary, mb: 1, "& svg": { fontSize: 36 } }}>
      {icon ?? <HourglassEmptyOutlinedIcon />}
    </Box>
    <Typography variant="h3" component="h2" sx={{ mb: 0.75 }}>
      {title}
    </Typography>
    {children && (
      <Typography variant="body2" sx={{ maxWidth: 560, mx: "auto" }}>
        {children}
      </Typography>
    )}
    {action && <Box sx={{ mt: 2.5 }}>{action}</Box>}
  </Paper>
);

/** The section's own text (narrative, callouts, KPIs) that an explanation of one of its charts or tables may use. */
export function sectionNotes(blocks: SectionBlock[]): string {
  return blocks
    .flatMap((b) => {
      if (b.type === "markdown") return [[b.title, b.body].filter(Boolean).join("\n")];
      if (b.type === "callout") return [`${b.title}: ${b.body}`];
      if (b.type === "kpis") return [b.items.map((k) => `${k.label}: ${k.value}${k.delta ? ` (${k.delta})` : ""}`).join("; ")];
      return [];
    })
    .join("\n\n");
}

/** A section shows "On this page" once it has at least this many titled charts, tables or texts. */
const MIN_TOC_ENTRIES = 4;

const slug = (text: string) =>
  text
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-|-$/g, "")
    .slice(0, 48);

/** The id each titled block is linked by: its own chart/table id, made unique within the page. */
function blockAnchor(b: SectionBlock, idx: number, seen: Set<string>): string | undefined {
  if (b.type === "kpis" || b.type === "callout" || !b.title) return undefined;
  let id = b.type === "chart" ? b.chart_id : b.type === "table" ? b.table_id || b.id : `text-${slug(b.title)}`;
  if (!id || seen.has(id)) id = `block-${idx + 1}`;
  seen.add(id);
  return id;
}

/** Ready-to-render curated blocks with anchors. */
function useCurated(section: Section | null | undefined, curation?: SectionCuration | null) {
  return React.useMemo(() => {
    if (!section) return null;
    const { primary, details } = curateSection(section, curation);
    const seen = new Set<string>();
    let i = 0;
    const withAnchor = (c: CuratedBlock) => ({ ...c, anchor: blockAnchor(c.block, i++, seen) });
    return { primary: primary.map(withAnchor), details: details.map(withAnchor) };
  }, [section, curation]);
}

export const SectionRenderer: React.FC<{
  section?: Section | null;
  isLoading?: boolean;
  error?: Error | null;
  emptyMessage?: string;
  pendingMessage?: string;
  /** 1: the section is the page (the page header shows the sidebar label). 2: it sits under a page header of its own. 0: no heading (a tab panel). */
  headingLevel?: 0 | 1 | 2;
  /** Replace the section's own title / description in the header. */
  title?: string | null;
  subtitle?: string | null;
  /** Page-level actions shown in the header. */
  headerActions?: React.ReactNode;
  /** Shown right below the header, whatever state the section is in (e.g. a page's own controls). */
  afterHeader?: React.ReactNode;
  /** Offer "On this page" for long sections. */
  toc?: boolean;
  /** What the page shows of the section (see `curation.ts`); without it every block is shown. */
  curation?: SectionCuration | null;
  /** Label of the collapsed group of secondary blocks. */
  detailsLabel?: string;
  /** The tab that labels this section when it is a tab panel (headingLevel 0). */
  labelledBy?: string;
}> = ({
  section,
  isLoading,
  error,
  emptyMessage,
  pendingMessage,
  headingLevel = 1,
  title,
  subtitle,
  headerActions,
  afterHeader,
  toc = true,
  curation,
  detailsLabel,
  labelledBy,
}) => {
  const curated = useCurated(section, curation);
  const detailsId = section ? `${section.id}-more` : undefined;
  const tocEntries: TocEntry[] = React.useMemo(() => {
    if (!curated) return [];
    const entries = curated.primary.flatMap((c) => (c.anchor && "title" in c.block && c.block.title ? [{ id: c.anchor, label: c.block.title }] : []));
    if (curated.details.length > 0 && detailsId) entries.push({ id: detailsId, label: detailsLabel ?? "More analysis" });
    return entries;
  }, [curated, detailsId, detailsLabel]);
  const titleId = section ? `section-${section.id}-title` : undefined;
  const explainScope = React.useMemo(
    () => ({ sectionTitle: section?.title, sectionSubtitle: section?.subtitle, notes: section ? sectionNotes(section.blocks) : null }),
    [section]
  );
  const shownTitle = title === undefined ? section?.title : title;
  const shownSubtitle = subtitle === undefined ? section?.subtitle : subtitle;

  const header =
    headingLevel === 1 ? (
      // The page title is the sidebar label (one name per page everywhere); the section's description follows it
      <PageHeader title={title ?? undefined} description={shownSubtitle} actions={headerActions} headingId={titleId} />
    ) : headingLevel === 2 && section && shownTitle ? (
      <Box sx={{ mb: 2.5 }}>
        <Typography id={titleId} variant="h2">
          {shownTitle}
        </Typography>
        {shownSubtitle && (
          <Typography variant="body1" sx={{ mt: 0.5, color: PALETTE.inkSecondary, maxWidth: "75ch" }}>
            {shownSubtitle}
          </Typography>
        )}
      </Box>
    ) : headingLevel === 0 && section && shownSubtitle ? (
      <Typography variant="body1" sx={{ mb: 2.5, color: PALETTE.inkSecondary, maxWidth: "75ch" }}>
        {shownSubtitle}
      </Typography>
    ) : null;

  let body: React.ReactNode;
  if (isLoading) {
    body = <SectionSkeleton withTitle={headingLevel === 2} />;
  } else if (!section) {
    const notReady = error instanceof ApiError && error.status === 404;
    body =
      error && !notReady ? (
        <Alert severity="error" sx={{ my: 2 }}>
          <AlertTitle>Could not load this page</AlertTitle>
          {error.message}
        </Alert>
      ) : notReady ? (
        <EmptyState title="Not computed yet">
          {pendingMessage || "This page fills in automatically as soon as the pipeline stage that produces it has finished."}
        </EmptyState>
      ) : (
        <EmptyState
          title="No run selected"
          icon={<FolderOpenOutlinedIcon />}
          action={
            <Button variant="contained" component={RouterLink} to="/runs">
              Go to runs
            </Button>
          }
        >
          {emptyMessage || "Upload the workbook on the Runs page, or pick a run in the header."}
        </EmptyState>
      );
  }

  if (!section || isLoading || !curated) {
    return (
      <Box>
        {header}
        {afterHeader}
        {body}
      </Box>
    );
  }

  const showToc = toc && tocEntries.length >= MIN_TOC_ENTRIES;
  const render = (c: (typeof curated.primary)[number]) => (
    <BlockRenderer key={c.key} block={c.block} anchorId={c.anchor} table={c.table} tableFirst={c.tableFirst} />
  );
  const blocks = (
    <>
      {curated.primary.map(render)}
      {curated.details.length > 0 && (
        <MoreAnalysis id={detailsId} count={curated.details.length} label={detailsLabel}>
          {curated.details.map(render)}
        </MoreAnalysis>
      )}
    </>
  );

  return (
    <ExplainScope value={explainScope}>
      <Box component="article" aria-labelledby={headingLevel === 0 ? labelledBy : titleId}>
        {header}
        {afterHeader}
        {showToc ? (
          <Box sx={{ display: "grid", gridTemplateColumns: { xs: "minmax(0, 1fr)", xl: "minmax(0, 1fr) 216px" }, columnGap: 4, alignItems: "start" }}>
            <Box sx={{ minWidth: 0 }}>{blocks}</Box>
            <OnThisPage entries={tocEntries} />
          </Box>
        ) : (
          blocks
        )}
      </Box>
    </ExplainScope>
  );
};
