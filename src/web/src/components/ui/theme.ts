// Theme interface shared with the pre-paint script in index.html (shell-app): the same key and values.
// Stored values are "light" and "dark"; no key means follow the system.
export const THEME_STORAGE_KEY = "criba.theme";

export type ThemeChoice = "light" | "dark" | "system";

// Writes data-theme only for "light" or "dark". Anything else, including a stale or tampered
// stored value, removes the attribute so the system preference decides.
export function applyTheme(value: string | null | undefined): void {
  const root = document.documentElement;
  if (value === "light" || value === "dark") {
    root.setAttribute("data-theme", value);
  } else {
    root.removeAttribute("data-theme");
  }
}

// The stored choice, or "system" when nothing valid is stored or storage is unavailable.
export function readStoredTheme(): ThemeChoice {
  try {
    const stored = window.localStorage.getItem(THEME_STORAGE_KEY);
    return stored === "light" || stored === "dark" ? stored : "system";
  } catch {
    return "system";
  }
}

// Persists the choice ("system" clears the key). Returns false when storage refused the write,
// so the caller can carry on with the in-memory choice.
export function saveTheme(choice: ThemeChoice): boolean {
  try {
    if (choice === "system") {
      window.localStorage.removeItem(THEME_STORAGE_KEY);
    } else {
      window.localStorage.setItem(THEME_STORAGE_KEY, choice);
    }
    return true;
  } catch {
    return false;
  }
}
