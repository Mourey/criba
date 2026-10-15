import { useCallback, useEffect, useState } from "react";

/** localStorage key of the viewer's navigation choice; its value is exactly "open" or "collapsed". */
export const NAV_STORAGE_KEY = "criba.nav";
/** The navigation starts open at this width and wider, collapsed below. */
export const NAV_WIDE_QUERY = "(min-width: 1440px)";

export type NavChoice = "open" | "collapsed";

/** The stored choice, or null when nothing is stored, the value is anything else, or storage throws. */
export function readNav(): NavChoice | null {
  try {
    const value = window.localStorage.getItem(NAV_STORAGE_KEY);
    return value === "open" || value === "collapsed" ? value : null;
  } catch {
    return null;
  }
}

/** Remember the choice. A storage failure is ignored: the in-memory state still changes. */
export function saveNav(value: NavChoice): void {
  try {
    window.localStorage.setItem(NAV_STORAGE_KEY, value);
  } catch {
    // Private mode or blocked storage: the choice lasts until the page is closed.
  }
}

function wideQuery(): MediaQueryList | null {
  try {
    return typeof window.matchMedia === "function" ? window.matchMedia(NAV_WIDE_QUERY) : null;
  } catch {
    return null;
  }
}

/**
 * Whether the navigation is open, and a toggle. With a stored choice that wins; otherwise open when
 * the window is 1440px wide or more (collapsed when matchMedia is missing), following the window's
 * width until the viewer chooses.
 */
export function useNavOpen(): [boolean, () => void] {
  const [stored] = useState(readNav);
  const [open, setOpen] = useState(() => (stored !== null ? stored === "open" : (wideQuery()?.matches ?? false)));
  const [chosen, setChosen] = useState(stored !== null);

  useEffect(() => {
    if (chosen) return undefined;
    const query = wideQuery();
    if (!query) return undefined;
    const onChange = (event: MediaQueryListEvent) => setOpen(event.matches);
    query.addEventListener("change", onChange);
    return () => query.removeEventListener("change", onChange);
  }, [chosen]);

  const toggle = useCallback(() => {
    const next = !open;
    setOpen(next);
    setChosen(true);
    saveNav(next ? "open" : "collapsed");
  }, [open]);

  return [open, toggle];
}
