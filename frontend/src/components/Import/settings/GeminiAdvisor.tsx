import React, { useCallback, useId, useMemo, useState } from "react";
import { Alert, Box, Button, Checkbox, Chip, CircularProgress, LinearProgress, ToggleButton, ToggleButtonGroup, Typography, alpha } from "@mui/material";
import AutoAwesomeIcon from "@mui/icons-material/AutoAwesome";
import CheckIcon from "@mui/icons-material/Check";
import { useHealth, useSettingsRecommendation } from "../../../api/hooks";
import type { KeptSetting, RecommendationGoal, RecommendedChange, SettingTab, SettingsDefaults, SettingsRecommendation } from "../../../api/types";
import { PALETTE } from "../../../theme";
import { Markdown } from "../../Sections/Markdown";
import { TABS, type SettingsDraft, type TabKey, type TabProps, applyRecommendation } from "./draft";
import { GUTTER } from "./fields";

const GOALS: { value: RecommendationGoal; label: string; help: string }[] = [
  { value: "balanced", label: "Best overall", help: "Maximum accuracy and identification first, then run time: every extra fit must earn its time" },
  { value: "accuracy", label: "Max accuracy", help: "The most accurate and best-validated model, whatever the run time" },
  { value: "speed", label: "Fastest", help: "The shortest run whose results can still be trusted" },
];

const CONFIDENCE: Record<RecommendedChange["confidence"], { label: string; color: "success" | "primary" | "default" }> = {
  high: { label: "High confidence", color: "success" },
  medium: { label: "Medium confidence", color: "primary" },
  low: { label: "Low confidence", color: "default" },
};

const TAB_ORDER: SettingTab[] = ["run", "data", "channels", "model", "priors", "sampling"];
const tabLabel = (tab: SettingTab) => (tab === "run" ? "Meridian switch" : TABS.find((t) => t.key === tab)?.label ?? tab);

const NOT_CONFIGURED = "Gemini is not switched on for this server. Set MMM_GEMINI_API_KEY, or MMM_GEMINI_VERTEX=true with MMM_GCP_PROJECT, and restart the API.";

function evidenceText(r: SettingsRecommendation): string {
  const pages = r.evidence.sections.length;
  if (r.evidence.kind === "this_run") return `Based on the workbook and this run's results (${pages} result pages).`;
  if (r.evidence.kind === "same_workbook")
    return `Based on the workbook and the results of “${r.evidence.run_name ?? r.evidence.run_id}”, an earlier run on the same workbook (${pages} result pages).`;
  return "Based on the workbook profile only. After a first run, ask again for advice grounded in the model's validation results.";
}

interface AdvisorData {
  result: SettingsRecommendation;
  selected: Set<string>;
  applied: Set<string>;
}

/** The recommendation shared by the panel above the tabs and the advice inside each tab. */
export function useAdvisor(setDraft: React.Dispatch<React.SetStateAction<SettingsDraft>>, defaults: SettingsDefaults) {
  const [data, setData] = useState<AdvisorData | null>(null);

  const receive = useCallback(
    (result: SettingsRecommendation) => setData({ result, selected: new Set(result.changes.map((c) => c.setting)), applied: new Set() }),
    []
  );
  const toggle = useCallback(
    (key: string, on: boolean) =>
      setData((prev) => {
        if (!prev) return prev;
        const selected = new Set(prev.selected);
        if (on) selected.add(key);
        else selected.delete(key);
        return { ...prev, selected };
      }),
    []
  );
  /** Apply these changes (in the server's order) unless they were applied already. */
  const apply = useCallback(
    (keys: string[]) => {
      if (!data) return;
      const chosen = data.result.changes.filter((c) => keys.includes(c.setting) && !data.applied.has(c.setting));
      if (chosen.length === 0) return;
      setDraft((d) => applyRecommendation(d, chosen, defaults));
      setData((prev) => (prev ? { ...prev, applied: new Set([...prev.applied, ...chosen.map((c) => c.setting)]) } : prev));
    },
    [data, setDraft, defaults]
  );
  const dismiss = useCallback(() => setData(null), []);
  return { data, receive, toggle, apply, dismiss };
}

export type Advisor = ReturnType<typeof useAdvisor>;

const ConfidenceChip: React.FC<{ value: RecommendedChange["confidence"] }> = ({ value }) => (
  <Chip size="small" variant="outlined" color={CONFIDENCE[value].color} label={CONFIDENCE[value].label} sx={{ height: 20, fontSize: "0.7rem" }} />
);

/** One recommended change: label, current → recommended, the reason; `control` is a checkbox or an Apply button. */
const ChangeRow: React.FC<{ change: RecommendedChange; applied: boolean; control: (ids: { label: string; detail: string }) => React.ReactNode; extra?: React.ReactNode }> = ({
  change: c,
  applied,
  control,
  extra,
}) => {
  const id = useId();
  const ids = { label: `${id}-label`, detail: `${id}-detail` };
  return (
    <Box
      component="li"
      sx={{
        display: "grid",
        gridTemplateColumns: "auto minmax(0, 1fr)",
        columnGap: 0.75,
        alignItems: "start",
        p: 1.25,
        pl: 0.75,
        borderRadius: 1,
        border: `1px solid ${applied ? alpha(PALETTE.semanticGood, 0.4) : alpha(PALETTE.brandPrimary, 0.3)}`,
        backgroundColor: applied ? alpha(PALETTE.semanticGood, 0.04) : "#FFFFFF",
      }}
    >
      {control(ids)}
      <Box sx={{ minWidth: 0 }}>
        <Box sx={{ display: "flex", alignItems: "center", flexWrap: "wrap", columnGap: 1, rowGap: 0.5 }}>
          <Typography id={ids.label} component="span" sx={{ fontWeight: 700, fontSize: "0.9rem", color: PALETTE.inkPrimary }}>
            {c.label}
          </Typography>
          {extra}
          <Box sx={{ ml: "auto" }}>
            <ConfidenceChip value={c.confidence} />
          </Box>
        </Box>
        <Box id={ids.detail}>
          <Typography variant="body2" sx={{ color: PALETTE.inkPrimary, overflowWrap: "anywhere" }}>
            {c.current} <span aria-hidden="true">→</span>
            <span className="visually-hidden"> becomes </span> <strong>{c.recommended}</strong>
          </Typography>
          {c.reason && (
            <Typography variant="caption" component="p" sx={{ color: PALETTE.inkSecondary, mt: 0.25 }}>
              {c.reason}
            </Typography>
          )}
        </Box>
      </Box>
    </Box>
  );
};

const keptList = (kept: KeptSetting[]) => kept.map((k) => `${k.label} (${k.current})`).join(" · ");

const AppliedMark: React.FC = () => (
  <Box component="span" sx={{ display: "inline-flex", alignItems: "center", gap: 0.25, mt: 0.25, color: PALETTE.semanticGood, fontSize: "0.75rem", fontWeight: 700 }}>
    <CheckIcon sx={{ fontSize: 16 }} /> Applied
  </Box>
);

const Sparkle: React.FC<{ size?: number }> = ({ size = 32 }) => (
  <Box
    aria-hidden="true"
    sx={{
      flexShrink: 0,
      width: size,
      height: size,
      borderRadius: "50%",
      display: "grid",
      placeItems: "center",
      color: "#FFFFFF",
      background: `linear-gradient(135deg, ${PALETTE.brandPrimary}, ${PALETTE.brandNavy})`,
    }}
  >
    <AutoAwesomeIcon sx={{ fontSize: size * 0.56 }} />
  </Box>
);

/**
 * "Gemini recommendation" above the settings tabs. Gemini reviews every setting the form offers (run mode, data,
 * channels, model, priors, sampling) against the workbook profile and the run's analysis and validation results (or
 * those of an earlier run on the same workbook) and returns its best value for each one for the chosen goal. The
 * server checks every change against the settings' limits and the run-start rules; the user picks which changes to
 * apply here or inside each tab, and nothing starts until they press Start / Re-run.
 */
export const GeminiAdvisor: React.FC<TabProps & { runId: string; mode: "draft" | "rerun"; advisor: Advisor; onOpenTab: (tab: TabKey) => void }> = ({
  runId,
  draft,
  locked,
  mode,
  advisor,
  onOpenTab,
}) => {
  const { data: health } = useHealth();
  const available = Boolean(health?.explain_available);
  const headingId = useId();
  const [goal, setGoal] = useState<RecommendationGoal>("balanced");
  const mutation = useSettingsRecommendation(runId);
  const data = advisor.data;
  const result = data?.result ?? null;

  const ask = () =>
    mutation.mutate(
      { goal, profile: draft.profile, skip_meridian: !draft.includeMeridian, seed: draft.seed, role_config: draft.roleConfig },
      { onSuccess: advisor.receive }
    );

  const pending = mutation.isPending;
  const intro = !health
    ? ""
    : !available
    ? NOT_CONFIGURED
    : locked
    ? "Available again once the run has finished or been cancelled."
    : "Gemini reviews every setting in every tab against the workbook and this run's analysis and validation results, then picks the best value for your goal. You choose which changes to apply.";

  const keptByTab = useMemo(() => {
    const out = new Map<SettingTab, KeptSetting[]>();
    for (const k of result?.kept ?? []) out.set(k.tab, [...(out.get(k.tab) ?? []), k]);
    return out;
  }, [result]);

  const pendingKeys = result ? result.changes.filter((c) => data!.selected.has(c.setting) && !data!.applied.has(c.setting)).map((c) => c.setting) : [];
  const appliedCount = data?.applied.size ?? 0;

  return (
    <Box
      component="section"
      aria-labelledby={headingId}
      sx={{
        border: `1px solid ${alpha(PALETTE.brandPrimary, 0.28)}`,
        borderRadius: 1,
        overflow: "hidden",
        background: `linear-gradient(180deg, ${alpha(PALETTE.brandPrimary, 0.06)}, ${alpha(PALETTE.brandPrimary, 0.015)})`,
      }}
    >
      <Box sx={{ display: "flex", alignItems: "center", flexWrap: "wrap", gap: 1.5, px: 2, py: 1.5 }}>
        <Sparkle />
        <Box sx={{ flex: "1 1 260px", minWidth: 0 }}>
          <Typography id={headingId} component="h3" sx={{ fontWeight: 750, fontSize: "0.95rem", color: PALETTE.inkPrimary }}>
            Gemini recommendation
          </Typography>
          <Typography variant="caption" component="p" sx={{ color: PALETTE.inkSecondary, maxWidth: "80ch" }}>
            {intro}
          </Typography>
        </Box>
        <ToggleButtonGroup
          size="small"
          exclusive
          value={goal}
          onChange={(_, v: RecommendationGoal | null) => v && setGoal(v)}
          aria-label="What the recommendation optimises for"
          disabled={!available || locked || pending}
          sx={{ backgroundColor: "#FFFFFF", "& .MuiToggleButton-root": { textTransform: "none", fontWeight: 650, px: 1.25, py: 0.5 } }}
        >
          {GOALS.map((g) => (
            <ToggleButton key={g.value} value={g.value} title={g.help}>
              {g.label}
            </ToggleButton>
          ))}
        </ToggleButtonGroup>
        <Button
          variant={result ? "outlined" : "contained"}
          onClick={ask}
          disabled={!available || locked || pending}
          startIcon={pending ? <CircularProgress size={16} color="inherit" /> : <AutoAwesomeIcon />}
          sx={{ minHeight: 40, whiteSpace: "nowrap" }}
        >
          {pending ? "Reviewing every setting…" : result ? "Ask again" : "Recommend best settings"}
        </Button>
      </Box>

      {pending && (
        <Box sx={{ px: 2, pb: 1.25 }}>
          <LinearProgress aria-label="Gemini is reviewing the settings" sx={{ height: 3, borderRadius: 2 }} />
          <Typography variant="caption" sx={{ color: PALETTE.inkMuted }}>
            Gemini is weighing the results for each setting; this takes about a minute.
          </Typography>
        </Box>
      )}

      <Box aria-live="polite">
        {mutation.isError && !pending && (
          <Box sx={{ px: 2, pb: 1.75 }}>
            <Alert
              severity="warning"
              variant="outlined"
              sx={{ backgroundColor: "#FFFFFF" }}
              action={
                <Button color="inherit" size="small" onClick={ask}>
                  Try again
                </Button>
              }
            >
              {mutation.error instanceof Error ? mutation.error.message : "The recommendation could not be loaded."}
            </Alert>
          </Box>
        )}

        {result && data && !pending && (
          <Box sx={{ px: 2, pt: 1.5, pb: 1.75, borderTop: `1px solid ${alpha(PALETTE.brandPrimary, 0.18)}`, backgroundColor: "#FFFFFF" }}>
            <Box sx={{ display: "flex", flexWrap: "wrap", gap: 0.75, mb: 1 }}>
              <Chip size="small" color="primary" label={`${result.coverage.reviewed} of ${result.coverage.active} active settings reviewed`} />
              <Chip size="small" variant="outlined" color="primary" label={`${result.changes.length} ${result.changes.length === 1 ? "change" : "changes"}`} />
              <Chip size="small" variant="outlined" label={`${result.kept.length} kept`} />
              {GOALS.find((g) => g.value === result.goal) && <Chip size="small" variant="outlined" label={`Goal: ${GOALS.find((g) => g.value === result.goal)!.label}`} />}
            </Box>
            <Typography variant="caption" component="p" sx={{ color: PALETTE.inkMuted, mb: 0.75 }}>
              {evidenceText(result)}
              {result.coverage.missing.length > 0 && ` Not reviewed: ${result.coverage.missing.join(", ")}.`}
            </Typography>
            <Markdown source={result.summary} dense />

            {result.changes.length === 0 ? (
              <Alert severity="info" variant="outlined" sx={{ mt: 1.5 }}>
                Gemini recommends keeping every current setting.
              </Alert>
            ) : (
              <Box component="ul" aria-label="Recommended changes" sx={{ listStyle: "none", p: 0, m: 0, mt: 1.5, display: "grid", gap: 1 }}>
                {result.changes.map((c) => {
                  const applied = data.applied.has(c.setting);
                  return (
                    <ChangeRow
                      key={c.setting}
                      change={c}
                      applied={applied}
                      control={(ids) =>
                        applied ? (
                          <Box sx={{ width: 34, display: "grid", placeItems: "center", pt: 0.25, color: PALETTE.semanticGood }}>
                            <CheckIcon fontSize="small" titleAccess="Applied" />
                          </Box>
                        ) : (
                          <Checkbox
                            size="small"
                            checked={data.selected.has(c.setting)}
                            onChange={(e) => advisor.toggle(c.setting, e.target.checked)}
                            inputProps={{ "aria-labelledby": ids.label, "aria-describedby": ids.detail }}
                            sx={{ mt: -0.5 }}
                          />
                        )
                      }
                      extra={
                        c.tab === "run" ? (
                          <Typography component="span" variant="caption" sx={{ color: PALETTE.inkMuted }}>
                            {tabLabel(c.tab)}
                          </Typography>
                        ) : (
                          <Button size="small" variant="text" onClick={() => onOpenTab(c.tab as TabKey)} sx={{ minWidth: 0, px: 0.75, py: 0, fontSize: "0.75rem" }}>
                            {tabLabel(c.tab)} tab
                          </Button>
                        )
                      }
                    />
                  );
                })}
              </Box>
            )}

            {result.kept.length > 0 && (
              <Box component="details" sx={{ mt: 1.25, "& > summary": { cursor: "pointer", fontSize: "0.8rem", fontWeight: 650, color: PALETTE.inkSecondary } }}>
                <summary>Why the other {result.kept.length} settings stay</summary>
                <Box component="ul" sx={{ listStyle: "none", m: 0, mt: 0.75, p: 0, display: "grid", gap: 0.75 }}>
                  {TAB_ORDER.filter((t) => keptByTab.has(t) || result.tab_notes[t]).map((t) => (
                    <Box component="li" key={t}>
                      <Typography variant="caption" component="p" sx={{ color: PALETTE.inkPrimary, fontWeight: 700 }}>
                        {tabLabel(t)}
                      </Typography>
                      {result.tab_notes[t] && (
                        <Typography variant="caption" component="p" sx={{ color: PALETTE.inkSecondary }}>
                          {result.tab_notes[t]}
                        </Typography>
                      )}
                      {keptByTab.has(t) && (
                        <Typography variant="caption" component="p" sx={{ color: PALETTE.inkMuted }}>
                          Kept: {keptList(keptByTab.get(t)!)}
                        </Typography>
                      )}
                    </Box>
                  ))}
                </Box>
              </Box>
            )}

            {result.rejected.length > 0 && (
              <Box component="details" sx={{ mt: 1, "& > summary": { cursor: "pointer", fontSize: "0.8rem", fontWeight: 650, color: PALETTE.inkSecondary } }}>
                <summary>
                  {result.rejected.length} {result.rejected.length === 1 ? "suggestion was" : "suggestions were"} left out by the app's checks
                </summary>
                <Box component="ul" sx={{ m: 0, mt: 0.75, pl: 2.5 }}>
                  {result.rejected.map((r, i) => (
                    <Typography component="li" variant="caption" key={`${r.setting}-${i}`} sx={{ color: PALETTE.inkSecondary }}>
                      <strong>{r.label}</strong> = {r.value}: {r.problem}
                    </Typography>
                  ))}
                </Box>
              </Box>
            )}

            {result.unverified_numbers.length > 0 && (
              <Alert severity="warning" variant="outlined" sx={{ mt: 1.25, py: 0, "& .MuiAlert-message": { fontSize: "0.8rem" } }}>
                Not found in the data and results Gemini was given: {result.unverified_numbers.join(", ")}. Check these before relying on them.
              </Alert>
            )}

            {appliedCount > 0 && (
              <Alert severity="success" variant="outlined" sx={{ mt: 1.25 }}>
                Applied {appliedCount} {appliedCount === 1 ? "change" : "changes"}. The tabs count them as changes; review them, then{" "}
                {mode === "draft" ? "start the run" : "re-run with these settings"}.
              </Alert>
            )}

            <Box sx={{ display: "flex", alignItems: "center", flexWrap: "wrap", gap: 1, mt: 1.5 }}>
              {result.changes.length > 0 && (
                <Button variant="contained" onClick={() => advisor.apply(pendingKeys)} disabled={pendingKeys.length === 0 || locked}>
                  {pendingKeys.length === 0 && appliedCount > 0 ? "All selected changes applied" : `Apply ${pendingKeys.length} selected ${pendingKeys.length === 1 ? "change" : "changes"}`}
                </Button>
              )}
              <Button
                onClick={() => {
                  advisor.dismiss();
                  mutation.reset();
                }}
              >
                Dismiss
              </Button>
              <Typography variant="caption" sx={{ color: PALETTE.inkMuted, flex: "1 1 260px", lineHeight: 1.4 }}>
                AI-generated by {result.model}. The app checked every change against the settings' limits and run rules, but
                the reasoning can be wrong; nothing runs until you start it.
              </Typography>
            </Box>
          </Box>
        )}
      </Box>
    </Box>
  );
};

/**
 * Gemini's advice for one settings tab, at the top of that tab: its recommended changes there (each with Apply) and
 * why the tab's other settings stay. Shown only after a recommendation was asked for.
 */
export const TabAdvice: React.FC<{ tab: TabKey; advisor: Advisor; locked: boolean }> = ({ tab, advisor, locked }) => {
  const data = advisor.data;
  if (!data) return null;
  const { result, applied } = data;
  const changes = result.changes.filter((c) => c.tab === tab);
  const kept = result.kept.filter((k) => k.tab === tab);
  const note = result.tab_notes[tab];
  if (changes.length === 0 && kept.length === 0 && !note) return null;
  const open = changes.filter((c) => !applied.has(c.setting));

  return (
    <Box sx={{ px: GUTTER, pt: 2.5 }}>
      <Box
        component="aside"
        aria-label={`Gemini's advice for the ${tabLabel(tab)} tab`}
        sx={{ border: `1px solid ${alpha(PALETTE.brandPrimary, 0.28)}`, borderRadius: 1, p: 1.5, backgroundColor: alpha(PALETTE.brandPrimary, 0.03) }}
      >
        <Box sx={{ display: "flex", alignItems: "center", flexWrap: "wrap", gap: 1 }}>
          <Sparkle size={24} />
          <Typography component="p" sx={{ fontWeight: 700, fontSize: "0.88rem", color: PALETTE.inkPrimary, flex: "1 1 220px" }}>
            Gemini for this tab:{" "}
            <Box component="span" sx={{ fontWeight: 500, color: PALETTE.inkSecondary }}>
              {changes.length === 0 ? "keep every setting" : `${changes.length} ${changes.length === 1 ? "change" : "changes"}`}
              {kept.length > 0 && `, ${kept.length} kept`}
            </Box>
          </Typography>
          {open.length > 1 && (
            <Button size="small" variant="outlined" disabled={locked} onClick={() => advisor.apply(open.map((c) => c.setting))} sx={{ backgroundColor: "#FFFFFF" }}>
              Apply all {open.length}
            </Button>
          )}
        </Box>

        {changes.length > 0 && (
          <Box component="ul" aria-label="Recommended changes in this tab" sx={{ listStyle: "none", p: 0, m: 0, mt: 1.25, display: "grid", gap: 0.75 }}>
            {changes.map((c) => {
              const done = applied.has(c.setting);
              return (
                <ChangeRow
                  key={c.setting}
                  change={c}
                  applied={done}
                  control={(ids) =>
                    done ? (
                      <Box sx={{ minWidth: 64, pt: 0.25 }}>
                        <AppliedMark />
                      </Box>
                    ) : (
                      <Button
                        size="small"
                        variant="contained"
                        disabled={locked}
                        onClick={() => advisor.apply([c.setting])}
                        aria-describedby={`${ids.label} ${ids.detail}`}
                        sx={{ minWidth: 64, py: 0.25 }}
                      >
                        Apply
                      </Button>
                    )
                  }
                />
              );
            })}
          </Box>
        )}

        {(note || kept.length > 0) && (
          <Box sx={{ mt: 1 }}>
            {note && (
              <Typography variant="caption" component="p" sx={{ color: PALETTE.inkSecondary }}>
                {note}
              </Typography>
            )}
            {kept.length > 0 && (
              <Typography variant="caption" component="p" sx={{ color: PALETTE.inkMuted, mt: 0.25 }}>
                Kept: {keptList(kept)}
              </Typography>
            )}
          </Box>
        )}
      </Box>
    </Box>
  );
};
