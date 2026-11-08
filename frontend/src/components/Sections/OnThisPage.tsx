import React, { useEffect, useRef, useState } from "react";
import { Box, Button, Divider, ListItemIcon, Menu, MenuItem, Typography, useMediaQuery, useScrollTrigger, useTheme } from "@mui/material";
import TocOutlinedIcon from "@mui/icons-material/TocOutlined";
import NorthOutlinedIcon from "@mui/icons-material/NorthOutlined";
import { LAYOUT, PALETTE } from "../../theme";

export interface TocEntry {
  id: string;
  label: string;
}

const supportsNativeScrollspy = (): boolean => {
  try {
    return CSS.supports("scroll-target-group: auto") && CSS.supports("selector(:target-current)");
  } catch {
    return false;
  }
};

/** Where a block becomes "current": its top has reached the upper 40 % of the viewport. */
const SPY_LINE = 0.4;

/**
 * The last block whose top has passed the spy line (at the very bottom of the page, the last block on screen). This is
 * the scrollspy fallback for browsers without `scroll-target-group`, and the source for the floating menu on narrower
 * screens. The observer's band ends at the spy line, so it fires exactly when a block's top crosses it.
 */
function useObservedSection(ids: string[], enabled: boolean): string | null {
  const [current, setCurrent] = useState<string | null>(null);
  const key = ids.join("|");
  useEffect(() => {
    if (!enabled || ids.length === 0) return;
    const elements = ids.map((id) => document.getElementById(id)).filter((el): el is HTMLElement => el !== null);
    const update = () => {
      const line = window.innerHeight * SPY_LINE;
      const atBottom = window.innerHeight + window.scrollY >= document.documentElement.scrollHeight - 4;
      const passed = elements.filter((el) => el.getBoundingClientRect().top <= (atBottom ? window.innerHeight - 80 : line));
      setCurrent(passed.length ? passed[passed.length - 1].id : null);
    };
    const observer = new IntersectionObserver(update, { rootMargin: `-${LAYOUT.headerHeight}px 0px -${100 - SPY_LINE * 100}% 0px` });
    elements.forEach((el) => observer.observe(el));
    document.addEventListener("scrollend", update);
    return () => {
      observer.disconnect();
      document.removeEventListener("scrollend", update);
    };
  }, [key, enabled]); // `key` stands for `ids`
  return current;
}

// Long chart titles are clamped to two lines on an inner span, so the hidden lines cannot show through the padding
const clampSx = { display: "-webkit-box", WebkitLineClamp: 2, WebkitBoxOrient: "vertical", overflow: "hidden" } as const;

const linkSx = {
  display: "block",
  py: 0.625,
  pl: 1.5,
  pr: 0.5,
  ml: "-1px",
  borderLeft: "2px solid transparent",
  fontSize: "0.8rem",
  lineHeight: 1.4,
  color: PALETTE.inkSecondary,
  textDecoration: "none",
  borderRadius: "0 6px 6px 0",
  "&:hover": { color: PALETTE.inkPrimary, backgroundColor: PALETTE.bgSubtle },
} as const;

const activeLinkSx = { color: PALETTE.brandNavy, fontWeight: 700, borderLeftColor: PALETTE.brandPrimary };

/**
 * "On this page" for long result pages: a sticky table of contents beside the content on wide screens (xl), and a
 * floating button with the same list below that, shown once the reader has scrolled past the page header.
 */
export const OnThisPage: React.FC<{ entries: TocEntry[] }> = ({ entries }) => {
  const wide = useMediaQuery(useTheme().breakpoints.up("xl"), { noSsr: true });
  const ids = entries.map((e) => e.id);
  const native = wide && supportsNativeScrollspy();
  const observed = useObservedSection(ids, !native);
  const [nativeCurrent, setNativeCurrent] = useState<string | null>(null);
  const current = native ? nativeCurrent : observed;
  const railRef = useRef<HTMLElement>(null);

  // Native scrollspy styles the link with :target-current; mirror it in aria-current once scrolling settles
  useEffect(() => {
    if (!native) return;
    const sync = () => {
      const link = railRef.current?.querySelector<HTMLAnchorElement>("a:target-current");
      setNativeCurrent(link ? decodeURIComponent(link.hash.slice(1)) : null);
    };
    sync();
    document.addEventListener("scrollend", sync);
    return () => document.removeEventListener("scrollend", sync);
  }, [native, ids.join("|")]);

  // Keep the current entry visible inside a long rail without scrolling the page itself
  useEffect(() => {
    const rail = railRef.current;
    const link = current ? rail?.querySelector<HTMLElement>(`a[href="#${CSS.escape(current)}"]`) : null;
    if (!rail || !link) return;
    if (link.offsetTop < rail.scrollTop + 32) rail.scrollTop = Math.max(0, link.offsetTop - 32);
    else if (link.offsetTop + link.offsetHeight > rail.scrollTop + rail.clientHeight - 32) rail.scrollTop = link.offsetTop + link.offsetHeight - rail.clientHeight + 32;
  }, [current]);

  // A link such as /explore#target_stl can only scroll once the section's blocks exist
  const jumped = useRef(false);
  useEffect(() => {
    if (jumped.current || !window.location.hash) return;
    const target = document.getElementById(decodeURIComponent(window.location.hash.slice(1)));
    if (target && ids.includes(target.id)) {
      jumped.current = true;
      target.scrollIntoView({ block: "start" });
    }
  }, [ids.join("|")]);

  const scrolled = useScrollTrigger({ disableHysteresis: true, threshold: 360 });
  const [menuAnchor, setMenuAnchor] = useState<HTMLElement | null>(null);

  const backToTop = () => {
    window.scrollTo({ top: 0 });
    document.getElementById("main-content")?.focus({ preventScroll: true });
  };

  return (
    <>
      <Box
        component="nav"
        ref={railRef}
        aria-label="On this page"
        sx={{
          display: { xs: "none", xl: "block" },
          position: "sticky",
          top: LAYOUT.stickyTop,
          maxHeight: `calc(100dvh - ${LAYOUT.stickyTop + 24}px)`,
          overflowY: "auto",
          scrollbarWidth: "thin",
          pb: 1,
          // Native scrollspy (Chrome 140+): the browser tracks which target is current
          ...(native ? { scrollTargetGroup: "auto" } : {}),
        }}
      >
        <Typography component="p" sx={{ mb: 1, fontSize: "0.7rem", fontWeight: 800, letterSpacing: "0.08em", textTransform: "uppercase", color: PALETTE.inkSecondary }}>
          On this page
        </Typography>
        <Box component="ul" sx={{ listStyle: "none", m: 0, p: 0, borderLeft: `1px solid ${PALETTE.borderSubtle}` }}>
          {entries.map((e) => (
            <li key={e.id}>
              <Box
                component="a"
                href={`#${e.id}`}
                aria-current={current === e.id ? "true" : undefined}
                sx={{
                  ...linkSx,
                  ...(native ? { "&:target-current": activeLinkSx } : { '&[aria-current="true"]': activeLinkSx }),
                }}
              >
                <Box component="span" sx={clampSx}>
                  {e.label}
                </Box>
              </Box>
            </li>
          ))}
        </Box>
        <Button size="small" startIcon={<NorthOutlinedIcon fontSize="small" />} onClick={backToTop} sx={{ mt: 1.5, ml: 0.5, color: PALETTE.inkSecondary }}>
          Back to top
        </Button>
      </Box>

      {!wide && scrolled && (
        <>
          <Button
            variant="contained"
            color="inherit"
            startIcon={<TocOutlinedIcon fontSize="small" />}
            aria-haspopup="true"
            aria-expanded={menuAnchor ? "true" : undefined}
            aria-controls={menuAnchor ? "on-this-page-menu" : undefined}
            onClick={(ev) => setMenuAnchor(ev.currentTarget)}
            sx={{
              position: "fixed",
              insetInlineEnd: { xs: 16, md: 24 },
              insetBlockEnd: { xs: 16, md: 24 },
              zIndex: (t) => t.zIndex.speedDial,
              backgroundColor: PALETTE.brandNavy,
              color: "#FFFFFF",
              boxShadow: "0 6px 20px rgba(7, 15, 31, 0.28)",
              "&:hover": { backgroundColor: PALETTE.brandPrimaryDark },
            }}
          >
            On this page
          </Button>
          <Menu
            id="on-this-page-menu"
            anchorEl={menuAnchor}
            open={Boolean(menuAnchor)}
            onClose={() => setMenuAnchor(null)}
            // The chosen link scrolls the page, so the page must stay scrollable and focus must not jump back to the button
            disableScrollLock
            disableRestoreFocus
            anchorOrigin={{ vertical: "top", horizontal: "right" }}
            transformOrigin={{ vertical: "bottom", horizontal: "right" }}
            slotProps={{ paper: { sx: { maxHeight: "min(70dvh, 560px)", width: "min(92vw, 380px)", mt: -1 } } }}
            MenuListProps={{ "aria-label": "On this page", dense: true }}
          >
            <MenuItem
              onClick={() => {
                setMenuAnchor(null);
                backToTop();
              }}
            >
              <ListItemIcon>
                <NorthOutlinedIcon fontSize="small" />
              </ListItemIcon>
              Back to top
            </MenuItem>
            <Divider />
            {entries.map((e) => (
              <MenuItem
                key={e.id}
                component="a"
                href={`#${e.id}`}
                selected={current === e.id}
                aria-current={current === e.id ? "true" : undefined}
                onClick={() => setMenuAnchor(null)}
                sx={{ whiteSpace: "normal", fontSize: "0.86rem", lineHeight: 1.4, py: 0.875 }}
              >
                {e.label}
              </MenuItem>
            ))}
          </Menu>
        </>
      )}
    </>
  );
};
