import React, { useEffect, useMemo, useState } from "react";
import { Box, Button, Chip, IconButton, Paper, Stack, Tab, Table, TableBody, TableCell, TableContainer, TableHead, TableRow, Tabs, Tooltip, Typography } from "@mui/material";
import OpenInNewIcon from "@mui/icons-material/OpenInNew";
import DownloadIcon from "@mui/icons-material/Download";
import { useArtifacts, useSection } from "../api/hooks";
import type { TableBlock } from "../api/types";
import { PageHeader } from "../components/Layout/PageHeader";
import { PageTabs, panelId, tabId, usePageTab } from "../components/Layout/PageTabs";
import { EmptyState, SectionRenderer } from "../components/Sections/SectionRenderer";
import { CURATIONS } from "../components/Sections/curations";
import { ExplainButton } from "../components/Explain/ExplainButton";
import { FONT_MONO, PALETTE } from "../theme";

const REPORT_ORDER = [
  "reports/meridian_eda.html",
  "reports/meridian_summary.html",
  "reports/meridian_health_card.html",
  "reports/meridian_optimization.html",
];
const REPORT_LABEL: Record<string, string> = {
  "reports/meridian_eda.html": "Exploratory data analysis",
  "reports/meridian_summary.html": "Model summary",
  "reports/meridian_health_card.html": "Health card",
  "reports/meridian_optimization.html": "Budget optimisation",
};
const CATEGORY_ORDER = ["report", "data", "model", "meta", "input", "section"];

const TABS = [
  { slug: "brief", label: "Answers to the brief" },
  { slug: "meridian", label: "Meridian reports" },
  { slug: "files", label: "Downloads" },
];

/**
 * The deliverables of a run: the executive brief answers, Meridian's own HTML reports, and every file. The `reports`
 * section's inventory and the artifact list described the same files twice; they are one table now, with the
 * section's descriptions next to each download.
 */
export const ReportsPage: React.FC<{ runId: string | null }> = ({ runId }) => {
  const [tab, setTab] = usePageTab(TABS);
  const { data: brief, isLoading: briefLoading, error: briefError } = useSection(runId, "brief");
  const { data: inventory } = useSection(runId, "reports");
  const { data: artifactsData } = useArtifacts(runId);
  const artifacts = useMemo(() => artifactsData?.artifacts ?? [], [artifactsData]);

  // Only offer the Meridian reports this run actually produced
  const reports = useMemo(
    () => artifacts.filter((a) => a.path.startsWith("reports/") && a.path.endsWith(".html")).sort((a, b) => REPORT_ORDER.indexOf(a.path) - REPORT_ORDER.indexOf(b.path)),
    [artifacts]
  );
  const [reportPath, setReportPath] = useState<string | null>(null);
  useEffect(() => {
    if (!reports.some((r) => r.path === reportPath)) setReportPath(reports[0]?.path ?? null);
  }, [reports, reportPath]);
  const active = reports.find((r) => r.path === reportPath);

  const descriptions = useMemo(() => {
    const table = inventory?.blocks.find((b): b is TableBlock => b.type === "table");
    return new Map((table?.rows ?? []).map((r) => [String(r.path ?? ""), { name: String(r.name ?? ""), description: String(r.description ?? "") }]));
  }, [inventory]);
  const downloads = useMemo(
    () =>
      artifacts
        .filter((a) => a.category !== "section")
        .sort((a, b) => CATEGORY_ORDER.indexOf(a.category) - CATEGORY_ORDER.indexOf(b.category) || a.path.localeCompare(b.path)),
    [artifacts]
  );

  const tabs = TABS.map((t) =>
    t.slug === "meridian" && reports.length ? { ...t, label: `${t.label} (${reports.length})` } : t.slug === "files" && downloads.length ? { ...t, label: `${t.label} (${downloads.length})` } : t
  );

  return (
    <Box>
      <PageHeader description="What the run delivers: the executive brief's questions answered with this run's numbers, Google Meridian's own reports, and every dataset, model and metric file." />
      <PageTabs scope="reports" tabs={tabs} active={tab.slug} onChange={setTab} label="Report sections" />
      <Box id={panelId("reports")} role="tabpanel" aria-labelledby={tabId("reports", tab.slug)}>
        {tab.slug === "brief" && (
          <SectionRenderer
            section={brief}
            isLoading={briefLoading}
            error={briefError as Error | null}
            headingLevel={0}
            title={null}
            labelledBy={tabId("reports", "brief")}
            curation={CURATIONS.brief}
            pendingMessage="The answers to the brief appear when the analysis stages have finished and are updated with the Meridian results."
          />
        )}

        {tab.slug === "meridian" &&
          (reports.length === 0 ? (
            <EmptyState title="No Meridian reports yet">
              They are written when the Meridian stages finish (they are not produced for runs that skip Meridian).
            </EmptyState>
          ) : (
            <Paper sx={{ p: { xs: 1.5, md: 2.5 } }}>
              <Stack direction={{ xs: "column", sm: "row" }} justifyContent="space-between" alignItems={{ xs: "flex-start", sm: "center" }} spacing={1.5} sx={{ mb: 1.5 }}>
                <Tabs
                  value={reportPath ?? false}
                  onChange={(_, v) => setReportPath(v)}
                  variant="scrollable"
                  allowScrollButtonsMobile
                  aria-label="Meridian reports"
                  sx={{ minHeight: 40, "& .MuiTab-root": { minHeight: 40, textTransform: "none" } }}
                >
                  {reports.map((r) => (
                    <Tab key={r.path} value={r.path} label={REPORT_LABEL[r.path] ?? r.filename} />
                  ))}
                </Tabs>
                {active && (
                  <Button size="small" variant="outlined" endIcon={<OpenInNewIcon fontSize="small" />} href={active.url} target="_blank" rel="noopener" sx={{ flexShrink: 0 }}>
                    Open in a new tab
                  </Button>
                )}
              </Stack>
              {active && (
                <>
                  {descriptions.get(active.path)?.description && (
                    <Typography variant="body2" sx={{ mb: 1.5 }}>
                      {descriptions.get(active.path)?.description}
                    </Typography>
                  )}
                  <Box sx={{ width: "100%", height: "min(800px, 82vh)", border: `1px solid ${PALETTE.borderSubtle}`, borderRadius: 1, backgroundColor: PALETTE.bgCard }}>
                    <iframe
                      key={active.path}
                      title={`Meridian ${REPORT_LABEL[active.path] ?? active.filename}`}
                      src={active.url}
                      sandbox="allow-scripts allow-popups allow-popups-to-escape-sandbox allow-downloads"
                      loading="lazy"
                      style={{ width: "100%", height: "100%", border: "none", borderRadius: 8 }}
                    />
                  </Box>
                </>
              )}
            </Paper>
          ))}

        {tab.slug === "files" &&
          (downloads.length === 0 ? (
            <EmptyState title="No files yet">The run's datasets, models and reports appear here as its stages finish.</EmptyState>
          ) : (
            <Paper component="section" aria-labelledby="downloads-title" sx={{ p: { xs: 1.5, md: 2.5 } }}>
              <Stack direction="row" justifyContent="space-between" alignItems="flex-start" spacing={1} sx={{ mb: 1.5 }}>
                <Box>
                  <Typography id="downloads-title" variant="h3" component="h2">
                    All files of this run
                  </Typography>
                  <Typography variant="body2">Datasets (CSV, Parquet), the fitted Meridian models (.binpb), metrics (JSON) and reports.</Typography>
                </Box>
                <ExplainButton kind="table" title="All files of this run" />
              </Stack>
              <TableContainer role="region" aria-label="File list" tabIndex={0} sx={{ maxHeight: "min(640px, 75vh)", border: `1px solid ${PALETTE.borderSubtle}`, borderRadius: 1 }}>
                <Table size="small" stickyHeader>
                  <TableHead>
                    <TableRow>
                      <TableCell scope="col">File</TableCell>
                      <TableCell scope="col">What it is</TableCell>
                      <TableCell scope="col">Type</TableCell>
                      <TableCell scope="col" align="right">
                        <span className="visually-hidden">Download</span>
                      </TableCell>
                    </TableRow>
                  </TableHead>
                  <TableBody>
                    {downloads.map((art) => {
                      const meta = descriptions.get(art.path);
                      return (
                        <TableRow key={art.path} hover>
                          <TableCell component="th" scope="row" sx={{ fontWeight: 600, verticalAlign: "top" }}>
                            {meta?.name || art.filename}
                            <Box component="span" sx={{ display: "block", fontFamily: FONT_MONO, fontSize: "0.74rem", fontWeight: 400, color: PALETTE.inkMuted }}>
                              {art.path}
                            </Box>
                          </TableCell>
                          <TableCell sx={{ verticalAlign: "top", color: PALETTE.inkSecondary, minWidth: 240 }}>{meta?.description || "—"}</TableCell>
                          <TableCell sx={{ verticalAlign: "top" }}>
                            <Chip size="small" label={art.category} />
                          </TableCell>
                          <TableCell align="right" sx={{ verticalAlign: "top" }}>
                            <Tooltip title="Download">
                              <IconButton size="small" href={`${art.url}?download=true`} aria-label={`Download ${art.filename}`}>
                                <DownloadIcon fontSize="small" />
                              </IconButton>
                            </Tooltip>
                          </TableCell>
                        </TableRow>
                      );
                    })}
                  </TableBody>
                </Table>
              </TableContainer>
            </Paper>
          ))}
      </Box>
    </Box>
  );
};
