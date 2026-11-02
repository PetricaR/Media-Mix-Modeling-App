import React, { Suspense, useState } from "react";
import {
  Alert,
  AlertTitle,
  Box,
  Button,
  Chip,
  CircularProgress,
  Grid,
  Paper,
  Stack,
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableHead,
  TableRow,
  TableSortLabel,
  ToggleButton,
  ToggleButtonGroup,
  Tooltip,
  Typography,
} from "@mui/material";
import DownloadIcon from "@mui/icons-material/Download";
import HelpOutlineIcon from "@mui/icons-material/HelpOutline";
import type {
  CalloutBlock,
  ChartBlock,
  KpisBlock,
  MarkdownBlock,
  Section,
  SectionBlock,
  TableBlock,
} from "../../api/types";
import { PALETTE } from "../../theme";

const LazyPlotlyChart = React.lazy(() => import("../Charts/PlotlyChart"));

const KpisRenderer: React.FC<{ block: KpisBlock }> = ({ block }) => {
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
        return PALETTE.brandPrimary;
    }
  };

  return (
    <Grid container spacing={2} sx={{ mb: 3 }}>
      {block.items.map((kpi, idx) => {
        const effectiveTone = kpi.tone || kpi.status || "neutral";
        const effectiveHelp = kpi.help || kpi.note;
        return (
          <Grid item xs={12} sm={6} md={3} key={`${kpi.label}-${idx}`}>
            <Paper
              sx={{
                p: 2.25,
                height: "100%",
                borderTop: `3px solid ${toneColor(effectiveTone)}`,
                display: "flex",
                flexDirection: "column",
                justifyContent: "space-between",
              }}
            >
              <Stack direction="row" alignItems="center" justifyContent="space-between" spacing={0.5}>
                <Typography
                  variant="caption"
                  sx={{
                    fontWeight: 600,
                    textTransform: "uppercase",
                    letterSpacing: "0.04em",
                    color: PALETTE.inkSecondary,
                  }}
                >
                  {kpi.label}
                </Typography>
                {effectiveHelp && (
                  <Tooltip title={effectiveHelp}>
                    <HelpOutlineIcon sx={{ fontSize: 15, color: PALETTE.inkMuted, cursor: "help" }} />
                  </Tooltip>
                )}
              </Stack>
              <Typography
                sx={{
                  fontFamily: '"JetBrains Mono", monospace',
                  fontSize: "1.45rem",
                  fontWeight: 600,
                  color: PALETTE.inkPrimary,
                  mt: 1,
                  mb: 0.5,
                  fontVariantNumeric: "tabular-nums",
                }}
              >
                {kpi.value}
              </Typography>
              {kpi.delta && (
                <Typography variant="caption" sx={{ color: toneColor(effectiveTone), fontWeight: 500 }}>
                  {kpi.delta}
                </Typography>
              )}
            </Paper>
          </Grid>
        );
      })}
    </Grid>
  );
};

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
      sx={{
        mb: 3,
        backgroundColor: "#FFFFFF",
        borderLeftWidth: 4,
      }}
    >
      <AlertTitle sx={{ fontWeight: 600 }}>{block.title}</AlertTitle>
      <Typography variant="body2" sx={{ color: PALETTE.inkPrimary, whiteSpace: "pre-line" }}>
        {block.body}
      </Typography>
    </Alert>
  );
};

const MarkdownRenderer: React.FC<{ block: MarkdownBlock }> = ({ block }) => {
  return (
    <Paper sx={{ p: 3, mb: 3 }}>
      {block.title && (
        <Typography variant="h3" sx={{ mb: 1.5 }}>
          {block.title}
        </Typography>
      )}
      <Box sx={{ display: "flex", flexDirection: "column", gap: 1.25 }}>
        {block.body.split("\n\n").map((para, idx) => (
          <Typography
            key={idx}
            variant="body1"
            sx={{ color: PALETTE.inkPrimary, whiteSpace: "pre-line", lineHeight: 1.65 }}
          >
            {para}
          </Typography>
        ))}
      </Box>
    </Paper>
  );
};

const ChartRenderer: React.FC<{ block: ChartBlock }> = ({ block }) => {
  const variantKeys = block.figure.variants ? Object.keys(block.figure.variants) : [];
  const [selectedVariant, setSelectedVariant] = useState<string>(variantKeys[0] || "default");

  const activeSpec =
    variantKeys.length > 0 && block.figure.variants && block.figure.variants[selectedVariant]
      ? block.figure.variants[selectedVariant]
      : { data: block.figure.data, layout: block.figure.layout };

  return (
    <Paper sx={{ p: 2.5, mb: 3 }} id={block.chart_id}>
      <Stack
        direction={{ xs: "column", sm: "row" }}
        justifyContent="space-between"
        alignItems={{ xs: "flex-start", sm: "center" }}
        spacing={1}
        sx={{ mb: 1.5 }}
      >
        <Box>
          <Typography variant="h3">{block.title}</Typography>
          {block.subtitle && (
            <Typography variant="body2" sx={{ mt: 0.25 }}>
              {block.subtitle}
            </Typography>
          )}
        </Box>
        {variantKeys.length > 0 && (
          <ToggleButtonGroup
            size="small"
            exclusive
            value={selectedVariant}
            onChange={(_, val) => val && setSelectedVariant(val)}
          >
            {variantKeys.map((vk) => (
              <ToggleButton key={vk} value={vk} sx={{ textTransform: "capitalize", px: 1.5 }}>
                {vk}
              </ToggleButton>
            ))}
          </ToggleButtonGroup>
        )}
      </Stack>

      <Suspense
        fallback={
          <Box sx={{ display: "flex", justifyContent: "center", py: 8 }}>
            <CircularProgress size={28} />
          </Box>
        }
      >
        <LazyPlotlyChart
          data={activeSpec.data}
          layout={activeSpec.layout}
          height={block.height || 420}
        />
      </Suspense>

      {(block.caption || block.note) && (
        <Typography
          variant="caption"
          sx={{
            display: "block",
            mt: 1.5,
            pt: 1,
            borderTop: `1px solid ${PALETTE.borderSubtle}`,
            color: PALETTE.inkSecondary,
          }}
        >
          {block.caption || block.note}
        </Typography>
      )}
    </Paper>
  );
};

const TableRenderer: React.FC<{ block: TableBlock }> = ({ block }) => {
  const [orderBy, setOrderBy] = useState<string | null>(null);
  const [orderDir, setOrderDir] = useState<"asc" | "desc">("asc");

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
      const va = a[orderBy];
      const vb = b[orderBy];
      if (typeof va === "number" && typeof vb === "number") {
        return orderDir === "asc" ? va - vb : vb - va;
      }
      const sa = String(va ?? "");
      const sb = String(vb ?? "");
      return orderDir === "asc" ? sa.localeCompare(sb) : sb.localeCompare(sa);
    });
  }, [block.rows, orderBy, orderDir]);

  const exportCsv = () => {
    const headers = block.columns.map((c) => `"${c.label.replace(/"/g, '""')}"`).join(",");
    const lines = sortedRows.map((r) =>
      block.columns
        .map((c) => {
          const val = r[c.key];
          return `"${String(val ?? "").replace(/"/g, '""')}"`;
        })
        .join(",")
    );
    const csv = [headers, ...lines].join("\n");
    const blob = new Blob([csv], { type: "text/csv;charset=utf-8;" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `${block.table_id || block.id || "table"}.csv`;
    a.click();
    URL.revokeObjectURL(url);
  };

  const formatCell = (val: unknown, fmt?: string | null) => {
    if (val === null || val === undefined) return "—";
    if (typeof val === "number") {
      switch (fmt) {
        case "eur":
        case "eur_0":
          return `€${val.toLocaleString(undefined, { maximumFractionDigits: 0 })}`;
        case "pct":
        case "pct_1":
          return `${val.toLocaleString(undefined, { minimumFractionDigits: 1, maximumFractionDigits: 1 })}%`;
        case "roas":
        case "ratio_2":
          return `${val.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
        case "int":
          return Math.round(val).toLocaleString();
        case "float_1":
          return val.toLocaleString(undefined, { minimumFractionDigits: 1, maximumFractionDigits: 1 });
        case "float_2":
          return val.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
        case "float_3":
          return val.toLocaleString(undefined, { minimumFractionDigits: 3, maximumFractionDigits: 3 });
        default:
          if (Number.isInteger(val)) return val.toLocaleString();
          return val.toLocaleString(undefined, { maximumFractionDigits: 3 });
      }
    }
    return String(val);
  };

  return (
    <Paper sx={{ p: 2.5, mb: 3 }} id={block.table_id || block.id}>
      <Stack
        direction="row"
        justifyContent="space-between"
        alignItems="center"
        spacing={2}
        sx={{ mb: 1.5 }}
      >
        <Box>
          <Typography variant="h3">{block.title}</Typography>
          {block.subtitle && <Typography variant="body2">{block.subtitle}</Typography>}
        </Box>
        <Button
          size="small"
          variant="outlined"
          startIcon={<DownloadIcon fontSize="small" />}
          onClick={exportCsv}
        >
          CSV
        </Button>
      </Stack>

      <TableContainer sx={{ maxHeight: 480 }}>
        <Table size="small" stickyHeader>
          <TableHead>
            <TableRow>
              {block.columns.map((col) => (
                <TableCell key={col.key} align={col.align || "left"}>
                  <TableSortLabel
                    active={orderBy === col.key}
                    direction={orderBy === col.key ? orderDir : "asc"}
                    onClick={() => handleSort(col.key)}
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
                {block.columns.map((col) => (
                  <TableCell
                    key={col.key}
                    align={col.align || (typeof row[col.key] === "number" ? "right" : "left")}
                    sx={{
                      fontFamily:
                        typeof row[col.key] === "number"
                          ? '"JetBrains Mono", monospace'
                          : "inherit",
                    }}
                  >
                    {formatCell(row[col.key], col.format)}
                  </TableCell>
                ))}
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </TableContainer>

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

export const SectionRenderer: React.FC<{
  section?: Section | null;
  isLoading?: boolean;
  error?: Error | null;
  emptyMessage?: string;
}> = ({ section, isLoading, error, emptyMessage }) => {
  if (isLoading) {
    return (
      <Box sx={{ display: "flex", alignItems: "center", gap: 2, py: 6 }}>
        <CircularProgress size={24} />
        <Typography variant="body2">Loading section artifacts…</Typography>
      </Box>
    );
  }

  if (error || !section) {
    return (
      <Alert severity="info" sx={{ my: 2 }}>
        {emptyMessage ||
          (error ? error.message : "Section has not been computed yet for the selected run.")}
      </Alert>
    );
  }

  const chartCount = section.blocks.filter((b) => b.type === "chart").length;
  const tableCount = section.blocks.filter((b) => b.type === "table").length;

  return (
    <Box>
      <Stack
        direction={{ xs: "column", md: "row" }}
        justifyContent="space-between"
        alignItems={{ xs: "flex-start", md: "center" }}
        spacing={1.5}
        sx={{ mb: 3 }}
      >
        <Box>
          <Typography variant="h1">{section.title}</Typography>
          {section.subtitle && (
            <Typography variant="body1" sx={{ color: PALETTE.inkSecondary, mt: 0.5 }}>
              {section.subtitle}
            </Typography>
          )}
        </Box>
        <Stack direction="row" spacing={1}>
          {chartCount > 0 && (
            <Chip size="small" label={`${chartCount} chart${chartCount > 1 ? "s" : ""}`} />
          )}
          {tableCount > 0 && (
            <Chip size="small" label={`${tableCount} table${tableCount > 1 ? "s" : ""}`} />
          )}
        </Stack>
      </Stack>

      {section.blocks.map((block, idx) => (
        <BlockRenderer key={idx} block={block} />
      ))}
    </Box>
  );
};
