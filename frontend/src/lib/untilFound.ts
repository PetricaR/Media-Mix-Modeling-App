import { useEffect, useLayoutEffect, type RefObject } from "react";

/** `hidden="until-found"`: the browser's find in page reaches hidden content and fires `beforematch` to reveal it. */
export const supportsUntilFound = typeof HTMLElement !== "undefined" && "onbeforematch" in HTMLElement.prototype;

/**
 * Keep a panel in the DOM while it is hidden: `hidden="until-found"` (React only writes boolean `hidden`, so the
 * attribute is set here), and `onReveal` runs when find in page matches text inside it. Callers fall back to showing
 * the panel when the browser does not support it, so its text stays searchable everywhere.
 */
export function useHiddenUntilFound(ref: RefObject<HTMLElement | null>, hidden: boolean, onReveal: () => void): void {
  useLayoutEffect(() => {
    const el = ref.current;
    if (!el) return;
    if (hidden) el.setAttribute("hidden", "until-found");
    else el.removeAttribute("hidden");
  }, [ref, hidden]);
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    el.addEventListener("beforematch", onReveal);
    return () => el.removeEventListener("beforematch", onReveal);
  }, [ref, onReveal]);
}
