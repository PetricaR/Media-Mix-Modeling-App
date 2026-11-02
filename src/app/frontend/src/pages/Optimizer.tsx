import React, { useState } from "react";
import {
  Alert,
  Box,
  Button,
  Chip,
  CircularProgress,
  FormControlLabel,
  Grid,
  Paper,
  Slider,
  Stack,
  Switch,
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableHead,
  TableRow,
  TextField,
  Typography,
} from "@mui/material";
import PlayArrowIcon from "@mui/icons-material/PlayArrow";
import { api } from "../api/client";
import { useOptimisation, useSection } from "../api/hooks";
import { SectionRenderer } from "../components/Sections/SectionRenderer";
import { PALETTE } from "../theme";

export const OptimizerPage: React.FC<{ runId: string | null }> = ({ runId }) => {
  const { data: section, isLoading, error } = useSection(runId, "optimiser");

  const [bounds, setBounds] = useState<number[]>([0.7, 1.3]);
  const [includeCatalog, setIncludeCatalog] = useState(true);
  const [customBudget, setCustomBudget] = useState<string>("");
  const [activeOptId, setActiveOptId] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [submitErr, setSubmitErr] = useState<string | null>(null);

  const { data: optDetail } = useOptimisation(activeOptId);

  const handleRunOptimiser = async () => {
    if (!runId) return;
    setSubmitting(true);
    setSubmitErr(null);
    try {
      const budgetVal = customBudget.trim() ? parseFloat(customBudget) : null;
      const resp = await api.createOptimisation(runId, {
        name: `Bounds [${bounds[0]}x, ${bounds[1]}x]`,
        total_budget: budgetVal,
        min_multiplier: bounds[0],
        max_multiplier: bounds[1],
        include_catalog: includeCatalog,
      });
      setActiveOptId(resp.id);
    } catch (err) {
      setSubmitErr(err instanceof Error ? err.message : "Failed to queue optimisation");
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <Box>
      <Paper sx={{ p: 3, mb: 4, borderTop: `3px solid ${PALETTE.brandSecondary}` }}>
        <Typography variant="h2" sx={{ mb: 0.5 }}>
          Interactive Constrained Budget Optimiser
        </Typography>
        <Typography variant="body2" sx={{ mb: 2.5 }}>
          Simulate constrained budget reallocation along fitted Hill saturation curves on the{" "}
          <code>interactive</code> worker queue.
        </Typography>

        {submitErr && (
          <Alert severity="error" sx={{ mb: 2 }} onClose={() => setSubmitErr(null)}>
            {submitErr}
          </Alert>
        )}

        <Grid container spacing={3} alignItems="center">
          <Grid item xs={12} md={4}>
            <Typography variant="caption" sx={{ fontWeight: 600 }}>
              Channel Spend Bounds ({bounds[0].toFixed(2)}× – {bounds[1].toFixed(2)}× Historical)
            </Typography>
            <Slider
              value={bounds}
              min={0.3}
              max={2.0}
              step={0.05}
              onChange={(_, val) => setBounds(val as number[])}
              valueLabelDisplay="auto"
              sx={{ mt: 1 }}
            />
          </Grid>
          <Grid item xs={12} sm={6} md={3}>
            <TextField
              size="small"
              label="Total Budget Override (€ optional)"
              placeholder="Default: 100% Historical"
              value={customBudget}
              onChange={(e) => setCustomBudget(e.target.value)}
              fullWidth
            />
          </Grid>
          <Grid item xs={12} sm={6} md={3}>
            <FormControlLabel
              control={
                <Switch
                  checked={includeCatalog}
                  onChange={(e) => setIncludeCatalog(e.target.checked)}
                />
              }
              label="Allow Catalog Reallocation"
            />
          </Grid>
          <Grid item xs={12} md={2}>
            <Button
              variant="contained"
              fullWidth
              startIcon={
                submitting || optDetail?.status === "running" || optDetail?.status === "queued" ? (
                  <CircularProgress size={16} color="inherit" />
                ) : (
                  <PlayArrowIcon />
                )
              }
              disabled={!runId || submitting}
              onClick={handleRunOptimiser}
            >
              Optimise
            </Button>
          </Grid>
        </Grid>

        {optDetail && (
          <Box sx={{ mt: 3, pt: 2.5, borderTop: `1px solid ${PALETTE.borderSubtle}` }}>
            <Stack direction="row" spacing={1.5} alignItems="center" sx={{ mb: 2 }}>
              <Chip size="small" color="info" label={`Status: ${optDetail.status.toUpperCase()}`} />
              {optDetail.result && (
                <>
                  <Chip
                    size="small"
                    color="success"
                    label={`Expected Uplift: €${optDetail.result.uplift_eur.toLocaleString(undefined, {
                      maximumFractionDigits: 0,
                    })} (+${optDetail.result.uplift_pct.toFixed(1)}%)`}
                  />
                  <Chip
                    size="small"
                    variant="outlined"
                    label={`Total Budget: €${optDetail.result.total_budget.toLocaleString(undefined, {
                      maximumFractionDigits: 0,
                    })}`}
                  />
                </>
              )}
            </Stack>

            {optDetail.result && (
              <TableContainer>
                <Table size="small">
                  <TableHead>
                    <TableRow>
                      <TableCell>Channel</TableCell>
                      <TableCell align="right">Historical Spend (€)</TableCell>
                      <TableCell align="right">Optimised Spend (€)</TableCell>
                      <TableCell align="right">Multiplier</TableCell>
                      <TableCell align="right">Spend Δ (%)</TableCell>
                      <TableCell align="right">Hist. Incremental (€)</TableCell>
                      <TableCell align="right">Opt. Incremental (€)</TableCell>
                    </TableRow>
                  </TableHead>
                  <TableBody>
                    {(optDetail.result.channels || optDetail.result.allocations || []).map((ch) => {
                      const histSpend = ch.historical_spend ?? ch.current_spend ?? 0;
                      const histInc = ch.historical_incremental ?? ch.current_incremental ?? 0;
                      return (
                        <TableRow key={ch.channel} hover>
                          <TableCell sx={{ fontWeight: 600 }}>{ch.label || ch.channel}</TableCell>
                          <TableCell align="right">
                            {histSpend.toLocaleString(undefined, { maximumFractionDigits: 0 })}
                          </TableCell>
                          <TableCell align="right">
                            {ch.optimised_spend.toLocaleString(undefined, { maximumFractionDigits: 0 })}
                          </TableCell>
                          <TableCell align="right">{ch.multiplier.toFixed(2)}×</TableCell>
                          <TableCell
                            align="right"
                            sx={{
                              color:
                                ch.delta_pct > 1
                                  ? PALETTE.semanticGood
                                  : ch.delta_pct < -1
                                  ? PALETTE.semanticBad
                                  : "inherit",
                              fontWeight: 600,
                            }}
                          >
                            {ch.delta_pct >= 0 ? `+${ch.delta_pct.toFixed(1)}%` : `${ch.delta_pct.toFixed(1)}%`}
                          </TableCell>
                          <TableCell align="right">
                            {histInc.toLocaleString(undefined, {
                              maximumFractionDigits: 0,
                            })}
                          </TableCell>
                          <TableCell align="right">
                            {ch.optimised_incremental.toLocaleString(undefined, {
                              maximumFractionDigits: 0,
                            })}
                          </TableCell>
                        </TableRow>
                      );
                    })}
                  </TableBody>
                </Table>
              </TableContainer>
            )}
          </Box>
        )}
      </Paper>

      <SectionRenderer section={section} isLoading={isLoading} error={error as Error | null} />
    </Box>
  );
};
