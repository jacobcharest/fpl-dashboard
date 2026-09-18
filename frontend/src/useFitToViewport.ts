import { useLayoutEffect, useState } from "react";

// Matches .app's bottom padding, so a fitted table ends exactly where the page does.
const PAGE_BOTTOM = 40;
// Below this there's no useful table left (a phone, where the toolbar fills the first screen);
// the stylesheet's own max-height applies instead and the page scrolls.
const MIN_HEIGHT = 320;

/** For a page whose table is the last thing on it: sizes the table to reach the bottom of the
    window, so the table scrolls and the page doesn't. Put the ref on a wrapper around the
    DataTable; it publishes `--fit-height` for the stylesheet to use as the scroll area's
    max-height. Re-measures when the window or anything above the table changes size. */
export function useFitToViewport<T extends HTMLElement>() {
  // A callback ref held in state, not useRef: these pages render the table only once their
  // data arrives, and the effect has to run when the element finally appears.
  const [el, setEl] = useState<T | null>(null);

  useLayoutEffect(() => {
    if (!el) return;
    const measure = () => {
      const top = el.getBoundingClientRect().top + window.scrollY;
      const height = Math.floor(window.innerHeight - top - PAGE_BOTTOM);
      if (height >= MIN_HEIGHT) el.style.setProperty("--fit-height", `${height}px`);
      else el.style.removeProperty("--fit-height");
    };
    measure();
    // The body resizes whenever a note appears, the toolbar wraps or a filter bar grows - all
    // of which move the table's top edge.
    const observer = new ResizeObserver(measure);
    observer.observe(document.body);
    window.addEventListener("resize", measure);
    return () => {
      observer.disconnect();
      window.removeEventListener("resize", measure);
    };
  }, [el]);

  return setEl;
}
