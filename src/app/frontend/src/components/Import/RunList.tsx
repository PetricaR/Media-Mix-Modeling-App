import React from "react";
import { Box, ButtonBase, Chip, Paper, Skeleton, Typography } from "@mui/material";
import StarIcon from "@mui/icons-material/Star";
import type { RunSummaryItem } from "../../api/types";
import { PALETTE } from "../../theme";
import { PROFILES, RUN_STATUS_LABEL, formatDateTime, runStatusColor } from "./format";

/** Master list of runs. Selecting a run here selects it for the whole studio (the header switcher shows the same). */
export const RunList: React.FC<{
  runs: RunSummaryItem[];
  loading: boolean;
  selectedRunId: string | null;
  onSelect: (id: string) => void;
}> = ({ runs, loading, selectedRunId, onSelect }) => (
  <Paper component="section" aria-labelledby="runs-heading" sx={{ overflow: "hidden" }}>
    <Box sx={{ px: 2.5, pt: 2, pb: 1.5, display: "flex", alignItems: "baseline", gap: 1 }}>
      <Typography id="runs-heading" variant="h3" component="h2">
        Runs
      </Typography>
      {!loading && (
        <Typography variant="body2" component="span" sx={{ color: PALETTE.inkMuted }}>
          {runs.length}
        </Typography>
      )}
    </Box>

    <Box component="ul" sx={{ listStyle: "none", m: 0, p: 0 }}>
      {loading &&
        [0, 1].map((i) => (
          <Box component="li" key={i} sx={{ px: 2.5, py: 1.5, borderTop: `1px solid ${PALETTE.borderSubtle}` }}>
            <Skeleton width="60%" />
            <Skeleton width="40%" />
          </Box>
        ))}
      {runs.map((r) => {
        const selected = r.id === selectedRunId;
        return (
          <Box component="li" key={r.id}>
            <ButtonBase
              onClick={() => onSelect(r.id)}
              aria-current={selected ? "true" : undefined}
              sx={{
                width: "100%",
                display: "grid",
                gridTemplateColumns: "minmax(0, 1fr) auto",
                alignItems: "center",
                columnGap: 1.5,
                textAlign: "left",
                py: 1.5,
                pl: 2.25,
                pr: 2,
                borderTop: `1px solid ${PALETTE.borderSubtle}`,
                borderLeft: `3px solid ${selected ? PALETTE.brandGold : "transparent"}`,
                backgroundColor: selected ? PALETTE.bgGoldTint : "transparent",
                transition: "background-color 0.12s",
                "&:hover": { backgroundColor: selected ? PALETTE.bgGoldTint : PALETTE.bgSubtle },
                // The card clips overflow, so draw the focus ring inside the row
                "&:focus-visible": { outlineOffset: "-3px" },
              }}
            >
              <Box sx={{ minWidth: 0 }}>
                <Typography
                  component="span"
                  sx={{ display: "block", fontWeight: 700, fontSize: "0.9rem", color: PALETTE.inkPrimary, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}
                  title={r.name}
                >
                  {r.name}
                </Typography>
                <Typography component="span" variant="caption" sx={{ display: "flex", alignItems: "center", flexWrap: "wrap", columnGap: 0.75, color: PALETTE.inkMuted }}>
                  <span>{PROFILES[r.profile]?.label ?? r.profile}</span>
                  <span aria-hidden="true">·</span>
                  <span>{formatDateTime(r.created_at)}</span>
                  {r.is_active && (
                    <Box component="span" sx={{ display: "inline-flex", alignItems: "center", gap: 0.25, color: PALETTE.brandGoldDark, fontWeight: 700 }}>
                      <StarIcon aria-hidden="true" sx={{ fontSize: 13 }} />
                      Default
                    </Box>
                  )}
                </Typography>
              </Box>
              <Chip size="small" color={runStatusColor(r.status)} label={RUN_STATUS_LABEL[r.status] ?? r.status} />
            </ButtonBase>
          </Box>
        );
      })}
    </Box>

    {runs.some((r) => r.is_active) && (
      <Typography variant="caption" component="p" sx={{ px: 2.5, py: 1.25, color: PALETTE.inkSecondary, borderTop: `1px solid ${PALETTE.borderSubtle}`, backgroundColor: PALETTE.bgCanvas }}>
        The default run is what visitors see until they pick another one.
      </Typography>
    )}
  </Paper>
);
