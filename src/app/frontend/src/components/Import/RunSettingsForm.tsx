import React, { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { Alert, Box, Button, CircularProgress, Skeleton, Tab, Tabs, TextField, Typography, alpha } from "@mui/material";
import PlayArrowIcon from "@mui/icons-material/PlayArrow";
import ReplayIcon from "@mui/icons-material/Replay";
import RestartAltIcon from "@mui/icons-material/RestartAlt";
import { useSearchParams } from "react-router-dom";
import { useSettingsDefaults } from "../../api/hooks";
import type { DataProfile, DataRoleConfig, Profile, RunDetail, SettingsDefaults } from "../../api/types";
import { PALETTE } from "../../theme";
import { PROFILES } from "./format";
import { ChannelsTab } from "./settings/ChannelsTab";
import { DataTab } from "./settings/DataTab";
import { ModelTab } from "./settings/ModelTab";
import { PriorsTab } from "./settings/PriorsTab";
import { SamplingTab } from "./settings/SamplingTab";
import { TABS, type TabKey, changesByTab, initialDraft, resetDraft } from "./settings/draft";
import { GUTTER, SwitchField } from "./settings/fields";

export interface RunSettingsValues {
  name: string;
  profile: Profile;
  skipMeridian: boolean;
  seed: number;
  roleConfig: DataRoleConfig;
}

interface FormProps {
  run: RunDetail;
  dataProfile?: DataProfile | null;
  mode: "draft" | "rerun";
  locked: boolean;
  submitting: boolean;
  error: string | null;
  onClearError: () => void;
  onSubmit: (values: RunSettingsValues) => void;
}

const supportsUntilFound = typeof HTMLElement !== "undefined" && "onbeforematch" in HTMLElement.prototype;

/**
 * A tab panel that stays in the DOM. Inactive panels are `hidden="until-found"` where supported, so the browser's find
 * in page still reaches a setting in another tab and `beforematch` switches to it; elsewhere they are plain `hidden`.
 */
const TabPanel: React.FC<{ tab: TabKey; active: boolean; onReveal: (tab: TabKey) => void; children: React.ReactNode }> = ({ tab, active, onReveal, children }) => {
  const ref = useRef<HTMLDivElement>(null);
  useLayoutEffect(() => {
    const el = ref.current;
    if (!el) return;
    if (active) el.removeAttribute("hidden");
    else el.setAttribute("hidden", supportsUntilFound ? "until-found" : "");
  }, [active]);
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const reveal = () => onReveal(tab);
    el.addEventListener("beforematch", reveal);
    return () => el.removeEventListener("beforematch", reveal);
  }, [tab, onReveal]);
  return (
    <div ref={ref} role="tabpanel" id={`settings-panel-${tab}`} aria-labelledby={`settings-tab-${tab}`} data-tab={tab}>
      {children}
    </div>
  );
};

const SettingsForm: React.FC<FormProps & { defaults: SettingsDefaults }> = ({ run, dataProfile, mode, locked, submitting, error, onClearError, onSubmit, defaults }) => {
  const [draft, setDraft] = useState(() => initialDraft(run, defaults));
  const [params, setParams] = useSearchParams();
  const requested = params.get("tab");
  const tab: TabKey = TABS.some((t) => t.key === requested) ? (requested as TabKey) : "data";
  const setTab = useCallback(
    (next: TabKey) =>
      setParams(
        (p) => {
          const out = new URLSearchParams(p);
          out.set("tab", next);
          return out;
        },
        { replace: true }
      ),
    [setParams]
  );

  const changes = useMemo(() => changesByTab(draft, defaults), [draft, defaults]);
  const total = TABS.reduce((n, t) => n + changes[t.key].length, 0);

  // An invalid field in a hidden tab cannot show the browser's message: open its tab, then report again
  const formRef = useRef<HTMLFormElement>(null);
  const reportAfterSwitch = useRef(false);
  useEffect(() => {
    if (!reportAfterSwitch.current) return;
    reportAfterSwitch.current = false;
    requestAnimationFrame(() => formRef.current?.reportValidity());
  }, [tab]);
  const onInvalid = (e: React.FormEvent<HTMLFormElement>) => {
    const key = (e.target as HTMLElement).closest<HTMLElement>("[data-tab]")?.dataset.tab as TabKey | undefined;
    if (key && key !== tab && !reportAfterSwitch.current) {
      reportAfterSwitch.current = true;
      setTab(key);
    }
  };

  const submit = (e: React.FormEvent) => {
    e.preventDefault();
    onSubmit({ name: draft.name.trim(), profile: draft.profile, skipMeridian: !draft.includeMeridian, seed: draft.seed, roleConfig: draft.roleConfig });
  };

  const tabProps = { draft, setDraft, defaults, locked };
  const plan = draft.includeMeridian ? `${PROFILES[draft.profile].label} sampling · ${PROFILES[draft.profile].duration}` : "EDA and OLS benchmark only";

  return (
    <Box component="form" ref={formRef} onSubmit={submit} onInvalidCapture={onInvalid} aria-label={mode === "draft" ? "Run settings" : "Re-run settings"}>
      <Box sx={{ px: GUTTER, pb: 2.5, display: "flex", flexWrap: "wrap", alignItems: "flex-start", columnGap: 4, rowGap: 2 }}>
        {mode === "draft" && (
          <TextField
            label="Run name"
            value={draft.name}
            onChange={(e) => setDraft((d) => ({ ...d, name: e.target.value }))}
            required
            size="small"
            autoComplete="off"
            disabled={locked}
            helperText="Shown in the run switcher and the run list."
            inputProps={{ maxLength: 120 }}
            sx={{ width: 400, maxWidth: "100%" }}
          />
        )}
        <Box sx={{ maxWidth: 460 }}>
          <SwitchField
            label="Fit the Google Meridian MMM"
            checked={draft.includeMeridian}
            disabled={locked}
            onChange={(includeMeridian) => setDraft((d) => ({ ...d, includeMeridian }))}
            help={
              draft.includeMeridian
                ? "The Bayesian model behind drivers & ROI, the budget optimiser and the forecast."
                : "Off: the run covers the data audit, EDA, preparation and the OLS benchmark; the model, drivers, optimiser and forecast pages stay empty."
            }
          />
        </Box>
      </Box>

      <Box sx={{ borderTop: `1px solid ${PALETTE.borderSubtle}`, borderBottom: `1px solid ${PALETTE.borderSubtle}`, px: { xs: 1, sm: 1.5 } }}>
        <Tabs value={tab} onChange={(_, v: TabKey) => setTab(v)} aria-label="Setting groups" variant="scrollable" scrollButtons="auto" allowScrollButtonsMobile>
          {TABS.map((t) => {
            const n = changes[t.key].length;
            return (
              <Tab
                key={t.key}
                value={t.key}
                id={`settings-tab-${t.key}`}
                aria-controls={`settings-panel-${t.key}`}
                sx={{ textTransform: "none", fontWeight: 650, fontSize: "0.9rem", minHeight: 48 }}
                label={
                  <Box component="span" sx={{ display: "inline-flex", alignItems: "center", gap: 0.75 }}>
                    {t.label}
                    {n > 0 && (
                      <Box component="span" sx={{ minWidth: 20, px: 0.625, borderRadius: 999, fontSize: "0.72rem", fontWeight: 800, lineHeight: "20px", color: "#FFFFFF", backgroundColor: PALETTE.brandPrimary }}>
                        {n}
                        <span className="visually-hidden"> {n === 1 ? "change" : "changes"} from the notebook defaults</span>
                      </Box>
                    )}
                  </Box>
                }
              />
            );
          })}
        </Tabs>
      </Box>

      <TabPanel tab="data" active={tab === "data"} onReveal={setTab}>
        <DataTab {...tabProps} dataProfile={dataProfile} />
      </TabPanel>
      <TabPanel tab="channels" active={tab === "channels"} onReveal={setTab}>
        <ChannelsTab {...tabProps} />
      </TabPanel>
      <TabPanel tab="model" active={tab === "model"} onReveal={setTab}>
        <ModelTab {...tabProps} />
      </TabPanel>
      <TabPanel tab="priors" active={tab === "priors"} onReveal={setTab}>
        <PriorsTab {...tabProps} />
      </TabPanel>
      <TabPanel tab="sampling" active={tab === "sampling"} onReveal={setTab}>
        <SamplingTab {...tabProps} />
      </TabPanel>

      {error && (
        <Box sx={{ px: GUTTER, pb: 2 }}>
          <Alert severity="error" onClose={onClearError}>
            {error}
          </Alert>
        </Box>
      )}

      {/* Sticky so the start button stays in reach in a long tab */}
      <Box
        sx={{
          position: "sticky",
          bottom: 0,
          zIndex: 3,
          display: "flex",
          alignItems: "center",
          flexWrap: "wrap",
          gap: 1.5,
          px: GUTTER,
          py: 1.75,
          borderTop: `1px solid ${PALETTE.borderSubtle}`,
          backgroundColor: alpha("#FFFFFF", 0.96),
          backdropFilter: "blur(6px)",
          borderBottomLeftRadius: 10,
          borderBottomRightRadius: 10,
          boxShadow: "0 -6px 16px rgba(33, 41, 47, 0.05)",
        }}
      >
        <Box sx={{ flexGrow: 1, minWidth: 220 }}>
          <Typography variant="body2" sx={{ fontWeight: 650, color: PALETTE.inkPrimary }}>
            {plan} · {total === 0 ? "notebook defaults" : `${total} change${total === 1 ? "" : "s"} from the notebook defaults`}
          </Typography>
          <Typography variant="caption" sx={{ color: PALETTE.inkMuted }}>
            {locked
              ? "Settings are locked while the run is active."
              : mode === "rerun"
              ? "A re-run replaces this run's results; the uploaded workbook is kept."
              : "The run status page shows the progress once the run starts."}
          </Typography>
        </Box>
        {total > 0 && !locked && (
          <Button startIcon={<RestartAltIcon />} onClick={() => setDraft((d) => resetDraft(d, defaults))}>
            Reset to notebook defaults
          </Button>
        )}
        <Button
          type="submit"
          variant="contained"
          disabled={locked || submitting}
          startIcon={submitting ? <CircularProgress size={16} color="inherit" /> : mode === "draft" ? <PlayArrowIcon /> : <ReplayIcon />}
          sx={{ minHeight: 44, px: 3 }}
        >
          {mode === "draft" ? "Start run" : "Re-run with these settings"}
        </Button>
      </Box>
    </Box>
  );
};

/**
 * Every setting a run starts with, in tabs: data and validation, channel roles and media transforms, the Meridian
 * specification, priors, and sampling. Defaults and limits come from the API; the parent keys this form by run id.
 */
export const RunSettingsForm: React.FC<FormProps> = (props) => {
  const { data: defaults, error } = useSettingsDefaults();
  if (error) {
    return (
      <Box sx={{ px: GUTTER, pb: 2.5 }}>
        <Alert severity="error">The settings could not be loaded: {error instanceof Error ? error.message : "unknown error"}.</Alert>
      </Box>
    );
  }
  if (!defaults) {
    return (
      <Box sx={{ px: GUTTER, pb: 2.5 }} aria-busy="true">
        <Skeleton width="40%" height={36} />
        <Skeleton width="70%" />
        <Skeleton width="55%" />
      </Box>
    );
  }
  return <SettingsForm {...props} defaults={defaults} />;
};
