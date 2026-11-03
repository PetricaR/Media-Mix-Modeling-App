import React from "react";
import { Box, MenuItem, Select, Typography, alpha } from "@mui/material";
import type { AdstockShape, ChannelConfig, ChannelRole } from "../../../api/types";
import { FONT_MONO, PALETTE } from "../../../theme";
import { ROLE_OPTIONS, roleOption } from "../format";
import { type TabProps, bound, channelChanged, defaultShape, inferredRole, isMedia, patchRoleConfig, withRole } from "./draft";
import { ChangedTag, Dot, Group, NumberField } from "./fields";

const COLS = "minmax(120px, 1fr) minmax(0, 2.1fr) minmax(130px, 1fr) minmax(120px, 0.9fr) minmax(120px, 0.9fr)";

const SHAPES: { value: AdstockShape; label: string }[] = [
  { value: "geometric", label: "Geometric" },
  { value: "binomial", label: "Binomial" },
];

/** A column caption that only shows when the row is stacked (narrow screens); wide screens have the header row. */
const StackedLabel: React.FC<{ children: React.ReactNode }> = ({ children }) => (
  <Typography aria-hidden="true" variant="caption" sx={{ display: { xs: "block", md: "none" }, fontWeight: 700, color: PALETTE.inkSecondary, mb: 0.5 }}>
    {children}
  </Typography>
);

const Dash: React.FC<{ label: string }> = ({ label }) => (
  <Typography sx={{ color: PALETTE.inkMuted, pt: { md: 1 } }}>
    <span aria-hidden="true">—</span>
    <span className="visually-hidden">{label}</span>
  </Typography>
);

export const ChannelsTab: React.FC<TabProps> = ({ draft, setDraft, defaults, locked }) => {
  const channels = draft.roleConfig.channels;
  const counts = ROLE_OPTIONS.map((o) => ({ ...o, n: channels.filter((c) => c.role === o.value).length })).filter((o) => o.n > 0);
  const changedCount = channels.filter((c) => channelChanged(c, defaults)).length;

  const update = (idx: number, fn: (c: ChannelConfig) => ChannelConfig) =>
    patchRoleConfig(setDraft, { channels: channels.map((c, i) => (i === idx ? fn(c) : c)) });

  return (
    <Group
      first
      title="Channel roles and media transforms"
      description="Roles are inferred from the data; the note under each role says why. Adstock, saturation and the in-home shift apply to the media channels in Meridian."
      changed={changedCount > 0}
    >
      <Box component="ul" aria-label="Channels per role" sx={{ listStyle: "none", p: 0, m: 0, mb: 1.5, display: "flex", flexWrap: "wrap", gap: 0.75 }}>
        {counts.map((o) => (
          <Box
            component="li"
            key={o.value}
            sx={{ display: "inline-flex", alignItems: "center", gap: 0.75, px: 1.25, py: 0.375, borderRadius: 999, border: `1px solid ${PALETTE.borderSubtle}`, fontSize: "0.78rem", fontWeight: 600, color: PALETTE.inkPrimary }}
          >
            <Dot colour={o.colour} hollow={o.hollow} />
            {o.n} {o.short.toLowerCase()}
          </Box>
        ))}
      </Box>

      <Box sx={{ border: `1px solid ${PALETTE.borderSubtle}`, borderRadius: 2, overflow: "hidden" }}>
        <Box
          aria-hidden="true"
          sx={{
            display: { xs: "none", md: "grid" },
            gridTemplateColumns: COLS,
            columnGap: 2,
            px: 2,
            py: 0.875,
            backgroundColor: PALETTE.bgSubtle,
            fontSize: "0.72rem",
            fontWeight: 700,
            textTransform: "uppercase",
            letterSpacing: "0.04em",
            color: PALETTE.inkPrimary,
          }}
        >
          <span>Channel</span>
          <span>Role in the model</span>
          <span>Adstock</span>
          <span>Saturation</span>
          <span>In-home shift</span>
        </Box>
        {channels.map((ch, idx) => {
          const changed = channelChanged(ch, defaults);
          const options = ROLE_OPTIONS.some((o) => o.value === ch.role) ? ROLE_OPTIONS : [...ROLE_OPTIONS, roleOption(ch.role)];
          const inferred = inferredRole(ch);
          const noteId = `role-note-${idx}`;
          const media = isMedia(ch.role);
          return (
            <Box
              key={ch.name}
              role="group"
              aria-label={ch.name}
              sx={{
                display: "grid",
                gridTemplateColumns: { xs: "minmax(0, 1fr)", sm: "repeat(2, minmax(0, 1fr))", md: COLS },
                columnGap: 2,
                rowGap: 1.25,
                alignItems: "start",
                px: 2,
                py: 1.5,
                borderTop: { xs: idx ? `1px solid ${PALETTE.borderSubtle}` : "none", md: `1px solid ${PALETTE.borderSubtle}` },
                backgroundColor: changed ? alpha(PALETTE.brandPrimary, 0.04) : "transparent",
              }}
            >
              <Box sx={{ gridColumn: { sm: "1 / -1", md: "auto" }, pt: { md: 0.5 } }}>
                <Box sx={{ display: "flex", alignItems: "center", flexWrap: "wrap", gap: 0.75 }}>
                  <Typography sx={{ fontWeight: 700, fontSize: "0.88rem", color: PALETTE.inkPrimary }}>{ch.name}</Typography>
                  {changed && <ChangedTag />}
                </Box>
                <Typography sx={{ fontFamily: FONT_MONO, fontSize: "0.72rem", color: PALETTE.inkSecondary, overflowWrap: "anywhere" }}>
                  <span className="visually-hidden">Spend column: </span>
                  {ch.cost_column || "no spend"}
                </Typography>
              </Box>

              <Box sx={{ minWidth: 0, gridColumn: { sm: "1 / -1", md: "auto" } }}>
                <StackedLabel>Role in the model</StackedLabel>
                <Select
                  size="small"
                  fullWidth
                  value={ch.role}
                  disabled={locked}
                  onChange={(e) => update(idx, (c) => withRole(c, e.target.value as ChannelRole, defaults))}
                  inputProps={{ "aria-label": `Role of ${ch.name}`, "aria-describedby": ch.rationale ? noteId : undefined }}
                  renderValue={(v) => {
                    const o = roleOption(v as ChannelRole);
                    return (
                      <Box component="span" sx={{ display: "inline-flex", alignItems: "center", gap: 1 }}>
                        <Dot colour={o.colour} hollow={o.hollow} />
                        {o.label}
                      </Box>
                    );
                  }}
                  sx={{ fontSize: "0.84rem", backgroundColor: "#FFFFFF", maxWidth: 320 }}
                >
                  {options.map((o) => (
                    <MenuItem key={o.value} value={o.value} sx={{ gap: 1.25, fontSize: "0.86rem" }}>
                      <Dot colour={o.colour} hollow={o.hollow} />
                      {o.label}
                    </MenuItem>
                  ))}
                </Select>
                {ch.rationale && (
                  <Typography id={noteId} variant="caption" component="p" sx={{ mt: 0.625, color: PALETTE.inkSecondary }}>
                    {ch.role === inferred ? ch.rationale : `Inferred as ${roleOption(inferred).short.toLowerCase()}: ${ch.rationale}`}
                  </Typography>
                )}
              </Box>

              <Box sx={{ minWidth: 0 }}>
                <StackedLabel>Adstock</StackedLabel>
                {media ? (
                  <Select
                    size="small"
                    fullWidth
                    value={ch.adstock_shape ?? defaultShape(ch.role)}
                    disabled={locked}
                    onChange={(e) => {
                      const shape = e.target.value as AdstockShape;
                      update(idx, (c) => ({ ...c, adstock_shape: shape === defaultShape(c.role) ? null : shape }));
                    }}
                    inputProps={{ "aria-label": `Adstock shape of ${ch.name}` }}
                    sx={{ fontSize: "0.84rem", backgroundColor: "#FFFFFF" }}
                  >
                    {SHAPES.map((s) => (
                      <MenuItem key={s.value} value={s.value} sx={{ fontSize: "0.86rem" }}>
                        {s.label}
                        {s.value === defaultShape(ch.role) ? " (default)" : ""}
                      </MenuItem>
                    ))}
                  </Select>
                ) : (
                  <Dash label={`No adstock: ${ch.name} is not a media channel`} />
                )}
              </Box>

              <Box sx={{ minWidth: 0 }}>
                <StackedLabel>Saturation</StackedLabel>
                {media ? (
                  <Select
                    size="small"
                    fullWidth
                    value={ch.saturation ?? "hill"}
                    disabled={locked}
                    onChange={(e) => update(idx, (c) => ({ ...c, saturation: e.target.value as "hill" | "none" }))}
                    inputProps={{ "aria-label": `Saturation of ${ch.name}` }}
                    sx={{ fontSize: "0.84rem", backgroundColor: "#FFFFFF" }}
                  >
                    <MenuItem value="hill" sx={{ fontSize: "0.86rem" }}>
                      Hill (default)
                    </MenuItem>
                    <MenuItem value="none" sx={{ fontSize: "0.86rem" }}>
                      None (linear)
                    </MenuItem>
                  </Select>
                ) : (
                  <Dash label={`No saturation: ${ch.name} is not a media channel`} />
                )}
              </Box>

              <Box sx={{ minWidth: 0 }}>
                <StackedLabel>In-home shift</StackedLabel>
                {ch.role === "print_event" ? (
                  <NumberField
                    label="Shift"
                    inputLabel={`In-home shift of ${ch.name} in days`}
                    unit="days"
                    value={ch.in_home_shift_days ?? defaults.in_home_shift_days}
                    {...bound(defaults, "in_home_shift_days")}
                    disabled={locked}
                    onChange={(v) => update(idx, (c) => ({ ...c, in_home_shift_days: v }))}
                    width="100%"
                  />
                ) : (
                  <Dash label={`No in-home shift: ${ch.name} is not a print event`} />
                )}
              </Box>
            </Box>
          );
        })}
      </Box>
      <Typography variant="caption" component="p" sx={{ mt: 1.25, color: PALETTE.inkSecondary, maxWidth: "80ch" }}>
        Geometric adstock decays from the first day; binomial can peak after a delay (as a catalog does once it is in homes).
        Without saturation a channel's effect grows linearly with spend. The in-home shift moves print spend from the send-out
        date to the day the catalog arrives.
      </Typography>
    </Group>
  );
};
