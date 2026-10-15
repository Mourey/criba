import { useEffect, useState } from "react";
import { Icon, type IconName } from "./Icon.tsx";
import { applyTheme, readStoredTheme, saveTheme, type ThemeChoice } from "./theme.ts";

const NEXT: Record<ThemeChoice, ThemeChoice> = { light: "dark", dark: "system", system: "light" };
const ICON: Record<ThemeChoice, IconName> = { light: "sun", dark: "moon", system: "system" };
const LABEL: Record<ThemeChoice, string> = { light: "Light", dark: "Dark", system: "System" };

// Cycles light, dark, system. The choice is saved when storage allows it; the switch itself never
// depends on storage.
export function ThemeToggle({ initialMode }: { initialMode?: ThemeChoice }) {
  const [mode, setMode] = useState<ThemeChoice>(() => initialMode ?? readStoredTheme());

  useEffect(() => {
    applyTheme(mode);
  }, [mode]);

  const next = NEXT[mode];
  return (
    <button
      type="button"
      className="ui-theme-toggle"
      aria-label={`Theme: ${mode}. Switch to ${next}.`}
      onClick={() => {
        saveTheme(next);
        setMode(next);
      }}
    >
      <Icon name={ICON[mode]} />
      <span aria-hidden="true">{LABEL[mode]}</span>
    </button>
  );
}
