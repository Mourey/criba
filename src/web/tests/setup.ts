import "@testing-library/jest-dom/vitest";
import { cleanup } from "@testing-library/react";
import { afterEach, vi } from "vitest";

// jsdom has no matchMedia. Default to "no match" (light, motion allowed) so theme and reduced-motion
// code runs; a test that needs a match stubs it with vi.stubGlobal, which afterEach undoes.
const noMatch = (query: string): MediaQueryList => ({
  matches: false,
  media: query,
  onchange: null,
  addListener: () => undefined,
  removeListener: () => undefined,
  addEventListener: () => undefined,
  removeEventListener: () => undefined,
  dispatchEvent: () => false,
});
if (typeof window.matchMedia !== "function") {
  Object.defineProperty(window, "matchMedia", { value: noMatch, writable: true, configurable: true });
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.useRealTimers();
  window.sessionStorage.clear();
  window.localStorage.clear();
  document.documentElement.removeAttribute("data-theme");
});
