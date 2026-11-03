import React from "react";
import { Box, Typography } from "@mui/material";
import InfoOutlinedIcon from "@mui/icons-material/InfoOutlined";
import type { DataProfile } from "../../api/types";
import { PALETTE } from "../../theme";
import { formatDay, formatEuroCompact } from "./format";

const Stat: React.FC<{ label: string; value: React.ReactNode; detail?: string }> = ({ label, value, detail }) => (
  <Box sx={{ minWidth: 0, p: 1.75, borderRadius: 2, backgroundColor: PALETTE.bgCanvas, border: `1px solid ${PALETTE.borderSubtle}` }}>
    <Typography component="dt" variant="caption" sx={{ display: "block", color: PALETTE.inkSecondary, fontWeight: 600 }}>
      {label}
    </Typography>
    <Typography component="dd" sx={{ m: 0, mt: 0.25, fontWeight: 700, fontSize: "1.05rem", lineHeight: 1.3, color: PALETTE.inkPrimary, fontVariantNumeric: "tabular-nums" }}>
      {value}
    </Typography>
    {detail && (
      <Typography component="dd" variant="caption" sx={{ m: 0, display: "block", color: PALETTE.inkSecondary }}>
        {detail}
      </Typography>
    )}
  </Box>
);

/** "file.xlsx · sheet “data” of 2 (data, info)" */
export const workbookSource = (profile: DataProfile): string => {
  const sheets = profile.sheet_names || [];
  return `${profile.filename}${profile.selected_sheet ? ` · sheet “${profile.selected_sheet}”` : ""}${sheets.length > 1 ? ` of ${sheets.length} (${sheets.join(", ")})` : ""}`;
};

/**
 * What the upload contains, as profiled by the API: every value here is read from the workbook. It sits in a settings
 * group, so its own sub-heading is an h4.
 */
export const DataProfileSummary: React.FC<{ profile: DataProfile; channelCount: number }> = ({ profile, channelCount }) => {
  const rows = profile.n_rows || profile.raw_row_count || 0;
  const start = profile.date_min || profile.start_date;
  const end = profile.date_max || profile.end_date;
  const warnings = profile.warnings || [];

  return (
    <>

      <Box component="dl" sx={{ m: 0, display: "grid", gap: 1.25, gridTemplateColumns: "repeat(auto-fit, minmax(min(100%, 118px), 1fr))" }}>
        <Stat label="Rows" value={rows.toLocaleString("en-GB")} detail={profile.n_columns ? `${profile.n_columns} columns` : undefined} />
        <Stat
          label="Period"
          value={profile.calendar_days ? `${profile.calendar_days.toLocaleString("en-GB")} days` : formatDay(start)}
          detail={`${formatDay(start)} – ${formatDay(end)}`}
        />
        <Stat label="Channels" value={channelCount.toLocaleString("en-GB")} detail="Daily × Channel (National)" />
        <Stat label="Spend granularity" value="Daily (€)" detail="1 row/day per channel" />
        <Stat label="Total sales" value={formatEuroCompact(profile.total_sales_sum)} />
        <Stat label="Media spend" value={formatEuroCompact(profile.total_media_spend_sum)} />
      </Box>

      {warnings.length > 0 && (
        <Box sx={{ mt: 2.5 }}>
          <Typography component="h4" sx={{ fontWeight: 700, fontSize: "0.88rem", color: PALETTE.inkPrimary, mb: 0.75 }}>
            Notes from profiling ({warnings.length})
          </Typography>
          <Box component="ul" sx={{ listStyle: "none", m: 0, p: 0, display: "grid", gap: 0.75 }}>
            {warnings.map((w, idx) => (
              <Box component="li" key={idx} sx={{ display: "grid", gridTemplateColumns: "18px minmax(0, 1fr)", columnGap: 1, alignItems: "start" }}>
                <InfoOutlinedIcon aria-hidden="true" sx={{ fontSize: 17, mt: "2px", color: PALETTE.semanticInfo }} />
                <Typography variant="body2" sx={{ color: PALETTE.inkSecondary }}>
                  {w}
                </Typography>
              </Box>
            ))}
          </Box>
        </Box>
      )}
    </>
  );
};
