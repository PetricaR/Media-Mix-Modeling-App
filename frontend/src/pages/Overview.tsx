import React, { useMemo } from "react";
import { Box, Paper, Stack, Typography } from "@mui/material";
import ArrowForwardIcon from "@mui/icons-material/ArrowForward";
import { Link as RouterLink } from "react-router-dom";
import { useSection } from "../api/hooks";
import type { CalloutBlock, Section, TableBlock } from "../api/types";
import { ExplainButton, ExplainScope } from "../components/Explain/ExplainButton";
import { PageHeader } from "../components/Layout/PageHeader";
import { CalloutRenderer, KpiStrip, SectionRenderer, formatCell, sectionNotes, type KpiItem } from "../components/Sections/SectionRenderer";
import { findKpi } from "../components/Sections/curation";
import { PALETTE } from "../theme";

/**
 * The executive page: the answer, the four numbers behind it and the decisions, each linked to the page that is its
 * home. It repeats no chart or table of another page; the headline KPIs are quoted from the pages that compute them.
 */

/** Where the evidence for a finding lives (matched on the finding's area, which the backend computes). */
const EVIDENCE: { match: RegExp; to: string; label: string }[] = [
  { match: /accura|forecast/i, to: "/model", label: "Fit & validation" },
  { match: /catalog/i, to: "/explore?tab=catalog", label: "Catalog deep-dive" },
  { match: /reverse|causal/i, to: "/explore?tab=efficiency", label: "Efficiency & regimes" },
  { match: /driver|roi/i, to: "/drivers", label: "Drivers & ROI" },
  { match: /data|quality|issue/i, to: "/data-quality", label: "Data audit" },
];

function headlineKpis(overview?: Section | null, drivers?: Section | null, optimiser?: Section | null): KpiItem[] {
  const picks: [KpiItem | null, string, string][] = [
    [findKpi(overview, /hold-out MAE/i), "/model", "Fit & validation"],
    [findKpi(drivers, /media share/i), "/drivers", "Drivers & ROI"],
    [findKpi(optimiser, /incremental sales, optimised/i), "/optimizer", "Budget optimiser"],
    [findKpi(overview, /paid media spend/i), "/explore?tab=spend", "Spend"],
  ];
  return picks.flatMap(([kpi, to, linkLabel]) => (kpi ? [{ ...kpi, to, linkLabel }] : []));
}

const Findings: React.FC<{ table: TableBlock }> = ({ table }) => {
  const [areaKey, findingKey, decisionKey] = table.columns.map((c) => c.key);
  const decisionLabel = table.columns[2]?.label ?? "Decision";
  return (
    <Paper component="section" aria-labelledby="summary-findings" sx={{ p: { xs: 2, md: 3 }, mb: 3 }}>
      <Stack direction="row" justifyContent="space-between" alignItems="flex-start" spacing={1} sx={{ mb: 1 }}>
        <Typography id="summary-findings" variant="h2" sx={{ fontSize: "1.15rem" }}>
          {table.title}
        </Typography>
        <ExplainButton
          kind="table"
          title={table.title}
          table={() => ({ columns: table.columns.map((c) => c.label), rows: table.rows.map((r) => table.columns.map((c) => formatCell(r[c.key], c.format))) })}
        />
      </Stack>
      <Box component="ol" sx={{ listStyle: "none", p: 0, m: 0 }}>
        {table.rows.map((row, idx) => {
          const area = String(row[areaKey] ?? "");
          const evidence = EVIDENCE.find((e) => e.match.test(area));
          return (
            <Box
              component="li"
              key={idx}
              sx={{
                display: "grid",
                gridTemplateColumns: { xs: "minmax(0, 1fr)", md: "minmax(180px, 0.32fr) minmax(0, 1fr)" },
                columnGap: 3,
                rowGap: 0.75,
                py: 2,
                borderTop: idx === 0 ? "none" : `1px solid ${PALETTE.borderSubtle}`,
              }}
            >
              <Box>
                <Typography variant="h3" component="h3" sx={{ fontSize: "0.98rem" }}>
                  {area}
                </Typography>
                {evidence && (
                  <Box
                    component={RouterLink}
                    to={evidence.to}
                    sx={{ mt: 0.5, display: "inline-flex", alignItems: "center", gap: 0.5, fontSize: "0.8rem", fontWeight: 600, color: PALETTE.brandPrimary, textDecoration: "none", "&:hover": { textDecoration: "underline" } }}
                  >
                    {evidence.label}
                    <ArrowForwardIcon sx={{ fontSize: 15 }} aria-hidden="true" />
                  </Box>
                )}
              </Box>
              <Box>
                <Typography variant="body1" sx={{ color: PALETTE.inkPrimary }}>
                  {formatCell(row[findingKey])}
                </Typography>
                {decisionKey && row[decisionKey] != null && (
                  <Typography variant="body2" sx={{ mt: 0.75 }}>
                    <Box component="span" sx={{ fontWeight: 650, color: PALETTE.inkPrimary }}>
                      {decisionLabel}:{" "}
                    </Box>
                    {formatCell(row[decisionKey])}
                  </Typography>
                )}
              </Box>
            </Box>
          );
        })}
      </Box>
    </Paper>
  );
};

export const OverviewPage: React.FC<{ runId: string | null }> = ({ runId }) => {
  const { data: overview, isLoading, error } = useSection(runId, "overview");
  const { data: drivers } = useSection(runId, "drivers_roi");
  const { data: optimiser } = useSection(runId, "optimiser");

  const kpis = useMemo(() => headlineKpis(overview, drivers, optimiser), [overview, drivers, optimiser]);
  const summary = overview?.blocks.find((b): b is CalloutBlock => b.type === "callout");
  const findings = overview?.blocks.find((b): b is TableBlock => b.type === "table");
  const scope = useMemo(
    () => ({ sectionTitle: overview?.title, sectionSubtitle: overview?.subtitle, notes: overview ? sectionNotes(overview.blocks) : null }),
    [overview]
  );

  if (!overview) {
    return (
      <SectionRenderer
        section={overview}
        isLoading={isLoading}
        error={error as Error | null}
        emptyMessage="Upload a marketing workbook on the Runs page to start. The summary fills in as the pipeline runs."
      />
    );
  }

  return (
    <ExplainScope value={scope}>
      <Box component="article" aria-labelledby="summary-title">
        <PageHeader eyebrow={null} headingId="summary-title" description={overview.subtitle} />
        {kpis.length > 0 && <KpiStrip items={kpis} label="Headline results" />}
        {summary && <CalloutRenderer block={summary} />}
        {findings && <Findings table={findings} />}
        <Typography variant="body2" sx={{ mb: 1 }}>
          The executive brief questions are answered one by one in{" "}
          <Box component={RouterLink} to="/reports?tab=brief" sx={{ color: PALETTE.brandPrimary, fontWeight: 600 }}>
            Report › Answers to the brief
          </Box>
          .
        </Typography>
      </Box>
    </ExplainScope>
  );
};
