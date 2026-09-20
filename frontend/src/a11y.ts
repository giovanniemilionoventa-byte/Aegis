import { useEffect, useRef } from "react";
import { useLocation } from "react-router-dom";

/**
 * After a client-side navigation nothing tells a screen reader, or a keyboard
 * user, that the page changed. This moves focus to the new page's heading, which
 * is announced, and puts that heading in the tab title, so every page gets both
 * without each one having to remember.
 */
export function useRouteA11y() {
  const { pathname } = useLocation();
  const first = useRef(true);

  useEffect(() => {
    const initialLoad = first.current; // the skip link must stay the first stop on load
    first.current = false;
    // After React has committed the new page; a timer, not requestAnimationFrame,
    // because a tab opened in the background never gets a frame and would keep
    // the bare "Aegis" title until someone looked at it.
    const handle = window.setTimeout(() => {
      const heading = document.querySelector<HTMLElement>("main h1, main h2");
      if (!heading) return;
      document.title = `${heading.textContent} · Aegis`;
      if (initialLoad) return;
      heading.tabIndex = -1;
      heading.focus({ preventScroll: true });
    }, 0);
    return () => window.clearTimeout(handle);
  }, [pathname]);
}
