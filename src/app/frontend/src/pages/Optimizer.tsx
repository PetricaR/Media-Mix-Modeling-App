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
      const budgetVal = customBudget.trim() ? Number(customBudget.replace(/[^0-9.]/g, "")) : null;
      if (budgetVal !== null && !(budgetVal > 0)) {
        setSubmitErr("Enter the total budget in euros, e.g. 121800000.");
        return;
      }
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
      <Paper component="section" aria-labelledby="optimiser-title" sx={{ p: { xs: 2, md: 3 }, mb: 4, borderTop: `3px solid ${PALETTE.brandGold}` }}>
        <Typography id="optimiser-title" variant="h2" sx={{ mb: 0.5 }}>
          Try your own constraints
        </Typography>
        <Typography variant="body2" sx={{ mb: 2.5, maxWidth: "75ch" }}>
          Re-allocate the budget along the fitted response curves with your own limits: how far each channel may move from
          its historical spend, a different total budget, or the catalog held fixed. The model's ROIs are taken at face value,
          so SEA's upward-biased ROI pulls budget towards it.
        </Typography>

        {submitErr && (
          <Alert severity="error" sx={{ mb: 2 }} onClose={() => setSubmitErr(null)}>
            {submitErr}
          </Alert>
        )}

        <Grid container spacing={3} alignItems="center">
          <Grid item xs={12} md={4}>
            <Typography id="bounds-label" variant="caption" sx={{ fontWeight: 700 }}>
              Each channel between {bounds[0].toFixed(2)}× and {bounds[1].toFixed(2)}× its historical spend
            </Typography>
            <Slider
              value={bounds}
              min={0.3}
              max={2.0}
              step={0.05}
              color="secondary"
              onChange={(_, val) => setBounds(val as number[])}
              valueLabelDisplay="auto"
              getAriaLabel={(i) => (i === 0 ? "Lower bound" : "Upper bound")}
              getAriaValueText={(v) => `${v.toFixed(2)} times historical spend`}
              sx={{ mt: 1 }}
            />
          </Grid>
          <Grid item xs={12} sm={6} md={3}>
            <TextField
              size="small"
              label="Total budget (€, optional)"
              placeholder="Historical total"
              inputMode="numeric"
              value={customBudget}
              onChange={(e) => setCustomBudget(e.target.value)}
              helperText="Leave empty to keep the historical total"
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
              label="Let the catalog budget move"
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
            <Stack direction="row" spacing={1.5} alignItems="center" flexWrap="wrap" useFlexGap sx={{ mb: 2 }}>
              <Chip size="small" color={optDetail.status === "failed" ? "error" : optDetail.status === "succeeded" ? "success" : "info"} label={optDetail.status} />
              {optDetail.result && (
                <>
                  <Chip
                    size="small"
                    color={optDetail.result.uplift_eur >= 0 ? "success" : "warning"}
                    label={`Incremental sales ${optDetail.result.uplift_eur >= 0 ? "+" : "−"}€${Math.abs(optDetail.result.uplift_eur).toLocaleString("en-GB", {
                      maximumFractionDigits: 0,
                    })} (${optDetail.result.uplift_pct >= 0 ? "+" : ""}${optDetail.result.uplift_pct.toFixed(1)}%)`}
                  />
                  <Chip
                    size="small"
                    variant="outlined"
                    label={`Total budget €${optDetail.result.total_budget.toLocaleString("en-GB", {
                      maximumFractionDigits: 0,
                    })}`}
                  />
                </>
              )}
            </Stack>
            {optDetail.status === "failed" && (
              <Alert severity="error" sx={{ mb: 2 }}>
                {optDetail.error || "The optimisation failed."}
              </Alert>
            )}

            {optDetail.result && (
              <TableContainer>
                <Table size="small">
                  <TableHead>
                    <TableRow>
                      <TableCell scope="col">Channel</TableCell>
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
                            {histSpend.toLocaleString("en-GB", { maximumFractionDigits: 0 })}
                          </TableCell>
                          <TableCell align="right">
                            {ch.optimised_spend.toLocaleString("en-GB", { maximumFractionDigits: 0 })}
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

      <SectionRenderer
        section={section}
        isLoading={isLoading}
        error={error as Error | null}
        pendingMessage="The budget optimiser needs the Meridian response curves; it appears when the Meridian stages have finished."
      />
    </Box>
  );
};
