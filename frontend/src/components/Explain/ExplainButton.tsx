import React, { useCallback, useContext, useEffect, useId, useLayoutEffect, useMemo, useRef, useState } from "react";
import { Alert, Box, Button, IconButton, Skeleton, Stack, Typography } from "@mui/material";
import AutoAwesomeIcon from "@mui/icons-material/AutoAwesome";
import CloseIcon from "@mui/icons-material/Close";
import { useLocation } from "react-router-dom";
import { useExplanation, useHealth } from "../../api/hooks";
import type { ExplainRequest } from "../../api/types";
import { PALETTE } from "../../theme";
import { findNavItem } from "../Layout/nav";
import { Markdown } from "../Sections/Markdown";
import { clampTable, clampText, contentKey, figurePayload, tableFromElement } from "./payload";

/** The page text an explanation may use: the section's title, description, narrative, callouts and KPIs. */
export interface ExplainScopeValue {
  sectionTitle?: string | null;
  sectionSubtitle?: string | null;
  notes?: string | null;
}

interface ScopeContextValue extends ExplainScopeValue {
  /** Figures a page builds itself (e.g. the Optimizer's KPI tiles), registered with `useExplainPageNotes`. */
  pageNotes?: React.MutableRefObject<string | null>;
}

const ExplainScopeContext = React.createContext<ScopeContextValue>({});

export const ExplainScope: React.FC<{ value: ExplainScopeValue; children: React.ReactNode }> = ({ value, children }) => {
  const pageNotes = useRef<string | null>(null);
  const ctx = useMemo(() => ({ ...value, pageNotes }), [value]);
  return <ExplainScopeContext.Provider value={ctx}>{children}</ExplainScopeContext.Provider>;
};

/** Hand the page's own headline figures to every explanation on the page (read when a panel opens). */
export function useExplainPageNotes(text: string | null): void {
  const { pageNotes } = useContext(ExplainScopeContext);
  useEffect(() => {
    if (pageNotes) pageNotes.current = text;
  }, [pageNotes, text]);
}

/** The selected run: the server adds its facts (dates, channel roles, validation) to every explanation. */
const ExplainRunContext = React.createContext<string | null>(null);
export const ExplainRunProvider = ExplainRunContext.Provider;

const NOT_CONFIGURED =
  "Gemini is not switched on for this server yet. Set MMM_GEMINI_API_KEY (a Google AI Studio key), or MMM_GEMINI_VERTEX=true with MMM_GCP_PROJECT, and restart the API.";

type TableData = NonNullable<ExplainRequest["table"]>;

interface ExplainButtonProps {
  kind: "chart" | "table";
  title: string;
  subtitle?: string | null;
  caption?: string | null;
  /** Chart: the figure as given to Plotly. The drawn figure in the same card is preferred (legend toggles, variant). */
  figure?: { data: unknown[]; layout: unknown } | null;
  /** Table: the displayed cells. Omitted: read from the first table in the same card. */
  table?: TableData | (() => TableData) | null;
  /** Read the chart or table from this element instead of the button's own card (several tables in one card). */
  targetId?: string;
}

const supportsPopover = typeof HTMLElement !== "undefined" && "popover" in HTMLElement.prototype;
const GUTTER = 16;
const GAP = 8;
const WIDTH = 520;

/** The card the button belongs to: an explicit `data-explain-scope`, else the closest MUI Paper. */
const scopeOf = (el: Element | null) => el?.closest("[data-explain-scope]") ?? el?.closest(".MuiPaper-root") ?? null;

/**
 * "Explain" on a chart or table: Gemini describes what it shows from the numbers on screen and the page's own text.
 *
 * The panel is a non-modal `<dialog popover="auto">` (top layer, light dismiss, Esc, focus back to the button), so the
 * viewer can keep hovering the chart while reading. It is placed under the button (above when there is no room) and
 * follows it on scroll and resize; without the Popover API it opens with `dialog.show()`. When the server has no
 * Gemini configured, the panel says how to switch it on.
 */
export const ExplainButton: React.FC<ExplainButtonProps> = ({ kind, title, subtitle, caption, figure, table, targetId }) => {
  const { data: health } = useHealth();
  const scope = useContext(ExplainScopeContext);
  const runId = useContext(ExplainRunContext);
  const available = Boolean(health?.explain_available);
  const page = findNavItem(useLocation().pathname)?.item.label ?? null;
  const panelId = useId();
  const headingId = `${panelId}-title`;
  const buttonRef = useRef<HTMLButtonElement>(null);
  const panelRef = useRef<HTMLDialogElement>(null);
  const [open, setOpen] = useState(false);
  const [request, setRequest] = useState<{ key: string; payload: ExplainRequest } | null>(null);
  const [buildError, setBuildError] = useState<string | null>(null);
  const query = useExplanation(request);

  // Latest props for the toggle handler, which is attached once
  const latest = useRef({ kind, title, subtitle, caption, figure, table, targetId, scope, page, runId, available });
  latest.current = { kind, title, subtitle, caption, figure, table, targetId, scope, page, runId, available };

  const buildRequest = useCallback(() => {
    const p = latest.current;
    const cardEl = p.targetId ? document.getElementById(p.targetId) : scopeOf(buttonRef.current);
    const base: ExplainRequest = {
      kind: p.kind,
      title: p.title,
      subtitle: p.subtitle,
      caption: p.caption,
      page: p.page,
      section_title: p.scope.sectionTitle,
      section_subtitle: p.scope.sectionSubtitle,
      notes: [p.scope.notes, p.scope.pageNotes?.current].filter(Boolean).join("\n\n") || null,
      run_id: p.runId,
    };
    if (p.kind === "chart") {
      const fig = figurePayload(cardEl, p.figure);
      if (!fig) return null;
      base.figure = fig;
    } else {
      const rows = typeof p.table === "function" ? p.table() : p.table ?? tableFromElement(cardEl);
      if (!rows) return null;
      base.table = clampTable(rows);
    }
    const payload = clampText(base);
    return { key: contentKey(payload), payload };
  }, []);

  const place = useCallback(() => {
    const btn = buttonRef.current;
    const panel = panelRef.current;
    if (!btn || !panel) return;
    const r = btn.getBoundingClientRect();
    const vw = document.documentElement.clientWidth;
    const vh = window.innerHeight;
    const width = Math.min(WIDTH, vw - 2 * GUTTER);
    const left = Math.max(GUTTER, Math.min(r.right - width, vw - width - GUTTER));
    const below = vh - r.bottom - GAP - GUTTER;
    const above = r.top - GAP - GUTTER;
    const natural = panel.scrollHeight;
    const downward = below >= Math.min(natural, 280) || below >= above;
    const maxHeight = Math.max(180, downward ? below : above);
    const height = Math.min(natural, maxHeight);
    Object.assign(panel.style, {
      width: `${width}px`,
      maxHeight: `${maxHeight}px`,
      left: `${left}px`,
      top: `${downward ? r.bottom + GAP : Math.max(GUTTER, r.top - GAP - height)}px`,
    });
  }, []);

  // Open/close come from the browser (invoker button, light dismiss, Esc); fetch on open
  useEffect(() => {
    const panel = panelRef.current;
    if (!panel) return;
    const onToggle = (event: Event) => {
      const isOpen = (event as ToggleEvent).newState === "open";
      setOpen(isOpen);
      if (!isOpen) return;
      if (!latest.current.available) {
        setBuildError(NOT_CONFIGURED);
        return;
      }
      const next = buildRequest();
      setBuildError(next ? null : "This item has no data to explain yet.");
      if (next) setRequest((prev) => (prev?.key === next.key ? prev : next));
    };
    // Place it before it first paints (its height is known only once open, so `toggle` places it again)
    const onBeforeToggle = (event: Event) => {
      if ((event as ToggleEvent).newState === "open") place();
    };
    panel.addEventListener("beforetoggle", onBeforeToggle);
    panel.addEventListener("toggle", onToggle);
    return () => {
      panel.removeEventListener("beforetoggle", onBeforeToggle);
      panel.removeEventListener("toggle", onToggle);
    };
  }, [buildRequest, place, health]);

  // While open, follow the button and the panel's own size (loading → answer)
  useLayoutEffect(() => {
    if (!open) return;
    place();
    const panel = panelRef.current;
    const observer = typeof ResizeObserver !== "undefined" ? new ResizeObserver(() => place()) : null;
    if (panel?.firstElementChild) observer?.observe(panel.firstElementChild);
    window.addEventListener("resize", place);
    window.addEventListener("scroll", place, { capture: true, passive: true });
    return () => {
      observer?.disconnect();
      window.removeEventListener("resize", place);
      window.removeEventListener("scroll", place, { capture: true });
    };
  }, [open, place]);

  if (!health) return null;

  const close = () => {
    const panel = panelRef.current;
    if (!panel) return;
    if (supportsPopover) panel.hidePopover();
    else {
      panel.close();
      setOpen(false);
      buttonRef.current?.focus();
    }
  };

  // Fallback without the Popover API: a non-modal dialog toggled by the button, closed with Esc or the close button
  const fallbackToggle = () => {
    const panel = panelRef.current;
    if (!panel || supportsPopover) return;
    if (panel.open) return close();
    panel.show();
    panel.dispatchEvent(Object.assign(new Event("toggle"), { newState: "open" }));
  };

  const result = query.data;
  const loading = open && !buildError && (query.isPending || query.isFetching) && !result;
  const noun = kind === "chart" ? "chart" : "table";
  const invoker = supportsPopover ? { popovertarget: panelId } : {};
  const popoverAttr = supportsPopover ? { popover: "auto" } : {};

  return (
    <>
      <Button
        ref={buttonRef}
        size="small"
        variant="text"
        startIcon={<AutoAwesomeIcon fontSize="small" />}
        aria-label={`Explain the ${noun} "${title}" with Gemini`}
        aria-expanded={open}
        aria-controls={panelId}
        onClick={fallbackToggle}
        sx={{ flexShrink: 0, whiteSpace: "nowrap" }}
        {...invoker}
      >
        Explain
      </Button>
      <Box
        component="dialog"
        ref={panelRef}
        id={panelId}
        aria-labelledby={headingId}
        onKeyDown={(e: React.KeyboardEvent) => {
          if (!supportsPopover && e.key === "Escape") close();
        }}
        {...popoverAttr}
        sx={{
          position: "fixed",
          inset: "auto",
          m: 0,
          p: 0,
          border: `1px solid ${PALETTE.borderSubtle}`,
          borderRadius: 1,
          backgroundColor: "#FFFFFF",
          color: PALETTE.inkPrimary,
          boxShadow: "0 12px 32px rgba(7, 15, 31, 0.18), 0 2px 6px rgba(7, 15, 31, 0.08)",
          overflow: "auto",
          overscrollBehavior: "contain",
          zIndex: 1500, // only matters for the dialog.show() fallback; popovers sit in the top layer
          textAlign: "left",
          // Own rule: a browser without :popover-open drops only this one (it opens the [open] fallback instead)
          "&:popover-open": { display: "block" },
        }}
      >
        <Box>
          <Stack
            direction="row"
            spacing={1.25}
            alignItems="flex-start"
            sx={{ position: "sticky", top: 0, zIndex: 1, px: 2, pt: 1.75, pb: 1.25, backgroundColor: "#FFFFFF", borderBottom: `1px solid ${PALETTE.borderSubtle}` }}
          >
            <Box
              aria-hidden="true"
              sx={{
                flexShrink: 0,
                width: 28,
                height: 28,
                borderRadius: "50%",
                display: "grid",
                placeItems: "center",
                color: "#FFFFFF",
                background: `linear-gradient(135deg, ${PALETTE.brandPrimary}, ${PALETTE.brandNavy})`,
              }}
            >
              <AutoAwesomeIcon sx={{ fontSize: 16 }} />
            </Box>
            <Box sx={{ minWidth: 0, flex: 1 }}>
              <Typography id={headingId} component="h2" sx={{ fontSize: "0.95rem", fontWeight: 750, lineHeight: 1.3 }}>
                Gemini explains this {noun}
              </Typography>
              <Typography variant="caption" sx={{ display: "block", color: PALETTE.inkSecondary, overflowWrap: "anywhere" }}>
                {title}
              </Typography>
            </Box>
            <IconButton size="small" aria-label="Close the explanation" onClick={close} sx={{ mt: -0.5, mr: -0.75 }}>
              <CloseIcon fontSize="small" />
            </IconButton>
          </Stack>

          <Box aria-live="polite" aria-busy={loading} sx={{ px: 2, py: 1.75 }}>
            {buildError ? (
              <Alert severity="info" variant="outlined">
                {buildError}
              </Alert>
            ) : query.isError && !result ? (
              <Alert
                severity="warning"
                variant="outlined"
                action={
                  <Button color="inherit" size="small" onClick={() => query.refetch()}>
                    Try again
                  </Button>
                }
              >
                {(query.error as Error).message}
              </Alert>
            ) : loading || !result ? (
              <Box>
                <Typography variant="body2" sx={{ color: PALETTE.inkSecondary, mb: 1 }}>
                  Reading the {noun}…
                </Typography>
                {[92, 100, 84, 96, 60].map((w, i) => (
                  <Skeleton key={i} variant="text" width={`${w}%`} sx={{ fontSize: "0.9rem" }} />
                ))}
              </Box>
            ) : (
              <>
                <Markdown source={result.explanation} dense />
                {result.unverified_numbers.length > 0 && (
                  <Alert severity="warning" variant="outlined" sx={{ mt: 1.5, py: 0, "& .MuiAlert-message": { fontSize: "0.8rem" } }}>
                    Not found in the data shown: {result.unverified_numbers.join(", ")}. Check these against the {noun}.
                  </Alert>
                )}
              </>
            )}
          </Box>

          {available && (
            <Typography
              variant="caption"
              component="p"
              sx={{ m: 0, px: 2, pb: 1.5, color: PALETTE.inkMuted, lineHeight: 1.4 }}
            >
              AI-generated by {result?.model ?? health.explain_model ?? "Gemini"} from this page's numbers and text. It can be wrong.
            </Typography>
          )}
        </Box>
      </Box>
    </>
  );
};
