import React, { useState } from "react";
import {
  Box,
  Button,
  Chip,
  Paper,
  Stack,
  Tab,
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableHead,
  TableRow,
  Tabs,
  Typography,
} from "@mui/material";
import OpenInNewIcon from "@mui/icons-material/OpenInNew";
import DownloadIcon from "@mui/icons-material/Download";
import { useArtifacts, useSection } from "../api/hooks";
import { SectionRenderer } from "../components/Sections/SectionRenderer";
import { PALETTE } from "../theme";

const MERIDIAN_REPORTS = [
  {
    key: "reports/meridian_summary.html",
    label: "1. Meridian Model Summary (2-Page Report)",
  },
  {
    key: "reports/meridian_health_card.html",
    label: "2. MCMC Model Health Card",
  },
  {
    key: "reports/meridian_optimization.html",
    label: "3. Budget Optimization Report",
  },
];

export const ReportsPage: React.FC<{ runId: string | null }> = ({ runId }) => {
  const { data: section, isLoading, error } = useSection(runId, "reports");
  const { data: artifactsData } = useArtifacts(runId);
  const [reportTab, setReportTab] = useState(0);

  const activeReport = MERIDIAN_REPORTS[reportTab] || MERIDIAN_REPORTS[0];
  const iframeUrl = runId ? `/api/v1/runs/${runId}/artifacts/${activeReport.key}` : "";

  return (
    <Box>
      <SectionRenderer section={section} isLoading={isLoading} error={error as Error | null} />

      {runId && (
        <Paper sx={{ p: 3, mb: 4 }}>
          <Stack
            direction={{ xs: "column", sm: "row" }}
            justifyContent="space-between"
            alignItems={{ xs: "flex-start", sm: "center" }}
            spacing={1.5}
            sx={{ mb: 2 }}
          >
            <Box>
              <Typography variant="h2">Embedded Google Meridian HTML Reports</Typography>
              <Typography variant="body2">
                Rendered inside a sandboxed iframe (`sandbox="allow-scripts"`).
              </Typography>
            </Box>
            <Button
              size="small"
              variant="outlined"
              endIcon={<OpenInNewIcon fontSize="small" />}
              href={iframeUrl}
              target="_blank"
            >
              Open Full Screen
            </Button>
          </Stack>

          <Tabs
            value={reportTab}
            onChange={(_, v) => setReportTab(v)}
            sx={{ mb: 2, borderBottom: `1px solid ${PALETTE.borderSubtle}` }}
          >
            {MERIDIAN_REPORTS.map((r) => (
              <Tab key={r.key} label={r.label} sx={{ textTransform: "none", fontWeight: 600 }} />
            ))}
          </Tabs>

          <Box
            sx={{
              width: "100%",
              height: 680,
              border: `1px solid ${PALETTE.borderSubtle}`,
              borderRadius: 1,
              overflow: "hidden",
              backgroundColor: "#FFFFFF",
            }}
          >
            <iframe
              title={activeReport.label}
              src={iframeUrl}
              sandbox="allow-scripts"
              style={{ width: "100%", height: "100%", border: "none" }}
            />
          </Box>
        </Paper>
      )}

      {artifactsData?.artifacts && artifactsData.artifacts.length > 0 && (
        <Paper sx={{ p: 3 }}>
          <Typography variant="h3" sx={{ mb: 2 }}>
            Run Artifact Manifest ({artifactsData.artifacts.length} files)
          </Typography>
          <TableContainer sx={{ maxHeight: 420 }}>
            <Table size="small" stickyHeader>
              <TableHead>
                <TableRow>
                  <TableCell>Category</TableCell>
                  <TableCell>Filename</TableCell>
                  <TableCell>Relative Path</TableCell>
                  <TableCell align="right">Download</TableCell>
                </TableRow>
              </TableHead>
              <TableBody>
                {artifactsData.artifacts.map((art) => (
                  <TableRow key={art.path} hover>
                    <TableCell>
                      <Chip size="small" label={art.category} />
                    </TableCell>
                    <TableCell sx={{ fontWeight: 600 }}>{art.filename}</TableCell>
                    <TableCell sx={{ fontFamily: '"JetBrains Mono", monospace' }}>
                      {art.path}
                    </TableCell>
                    <TableCell align="right">
                      <Button
                        size="small"
                        startIcon={<DownloadIcon fontSize="small" />}
                        href={art.url}
                        target="_blank"
                      >
                        Download
                      </Button>
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </TableContainer>
        </Paper>
      )}
    </Box>
  );
};
