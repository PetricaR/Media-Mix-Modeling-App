import React from "react";
import { Box, MenuItem, TextField, Typography } from "@mui/material";
import type { ChannelConfig, ChannelRole, DataProfile, MappingRecommendation } from "../../../api/types";
import { NUMERIC, PALETTE, channelColor } from "../../../theme";
import { CHANNEL_ROLES } from "./mapping";

const eur = (v?: number) => (typeof v === "number" ? `€${Math.round(v).toLocaleString("en-GB")}` : "—");

/** Step 3: each channel's role, with the app's inference, its reason and Gemini's view next to it. */
export const ChannelsStep: React.FC<{
  preview: DataProfile;
  roles: Record<string, ChannelRole>;
  onChange: (name: string, role: ChannelRole) => void;
  gemini: MappingRecommendation | null;
}> = ({ preview, roles, onChange, gemini }) => {
  const days = preview.calendar_days ?? 0;
  const wide = preview.layout === "wide";
  const options = CHANNEL_ROLES.filter((r) => !(wide && r.value === "unpaid"));
  const advice = new Map((gemini?.channel_roles ?? []).map((c) => [c.name, c]));
  const channels: ChannelConfig[] = preview.role_config.channels;
  return (
    <Box>
      <Typography component="h3" sx={{ fontWeight: 700 }}>
        {channels.length} channels
      </Typography>
      <Typography variant="body2" sx={{ mb: 1.5, maxWidth: "80ch" }}>
        Each channel's role decides how it enters the model. The app infers it from the spend pattern; change it where you know better.
        Adstock, saturation and priors per channel are set on the next page (Run setup).
      </Typography>
      <Box role="region" aria-label="Channel roles" tabIndex={0} sx={{ overflowX: "auto", border: `1px solid ${PALETTE.borderSubtle}`, borderRadius: 1 }}>
        <Box component="table" sx={{ width: "100%", borderCollapse: "collapse", "& th, & td": { textAlign: "left", verticalAlign: "top", px: 1.5, py: 1.1, borderBottom: `1px solid ${PALETTE.borderSubtle}` }, "& thead th": { fontSize: "0.78rem", fontWeight: 650, color: PALETTE.inkSecondary, backgroundColor: PALETTE.bgSubtle }, "@media (max-width: 599.95px)": { "& thead": { display: "none" }, "& tbody tr": { display: "block", borderBottom: `1px solid ${PALETTE.borderSubtle}`, py: 1 }, "& tbody th, & tbody td": { display: "block", borderBottom: "none", py: 0.5, textAlign: "left !important" } } }}>
          <thead>
            <tr>
              <th scope="col">Channel</th>
              <th scope="col" style={{ textAlign: "right" }}>Spend</th>
              <th scope="col" style={{ textAlign: "right" }}>Days with spend</th>
              <th scope="col">Role</th>
              <th scope="col">Why</th>
            </tr>
          </thead>
          <tbody>
            {channels.map((ch, i) => {
              const role = roles[ch.name] ?? ch.role;
              const g = advice.get(ch.name);
              return (
                <tr key={ch.name}>
                  <th scope="row" style={{ fontWeight: 600 }}>
                    <Box sx={{ display: "flex", alignItems: "center", gap: 1 }}>
                      <Box component="span" aria-hidden="true" sx={{ width: 10, height: 10, borderRadius: "2px", flexShrink: 0, backgroundColor: channelColor(ch.name, i) }} />
                      {ch.name}
                    </Box>
                  </th>
                  <td style={{ textAlign: "right" }}>
                    <Box component="span" sx={NUMERIC}>
                      {eur(ch.total_cost)}
                    </Box>
                  </td>
                  <td style={{ textAlign: "right" }}>
                    <Box component="span" sx={NUMERIC}>
                      {ch.cost_days ?? 0} / {days}
                    </Box>
                  </td>
                  <td>
                    <TextField
                      select
                      size="small"
                      value={role}
                      onChange={(e) => onChange(ch.name, e.target.value as ChannelRole)}
                      inputProps={{ "aria-label": `Role of ${ch.name}` }}
                      sx={{ minWidth: 160, "& .MuiInputBase-root": { backgroundColor: "#FFFFFF" } }}
                    >
                      {options.map((o) => (
                        <MenuItem key={o.value} value={o.value}>
                          {o.label}
                        </MenuItem>
                      ))}
                    </TextField>
                  </td>
                  <td>
                    <Typography variant="caption" component="p" sx={{ color: PALETTE.inkSecondary, maxWidth: "52ch" }}>
                      {role === ch.role ? ch.rationale : CHANNEL_ROLES.find((o) => o.value === role)?.help}
                    </Typography>
                    {g && (
                      <Typography variant="caption" component="p" sx={{ mt: 0.5, color: g.role === role ? PALETTE.inkMuted : PALETTE.semanticWarn, maxWidth: "52ch" }}>
                        Gemini{g.role !== role ? ` suggests ${CHANNEL_ROLES.find((o) => o.value === g.role)?.label ?? g.role}` : ""}: {g.reason}
                      </Typography>
                    )}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </Box>
      </Box>
    </Box>
  );
};
