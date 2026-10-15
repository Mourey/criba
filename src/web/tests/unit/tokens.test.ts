/// <reference types="node" />
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

// Read from disk: Vitest swaps every CSS import, `?raw` included, for an empty string.
const read = (file: string) => readFileSync(join(import.meta.dirname, "../../src/styles", file), "utf8");
const appCss = read("app.css");
const tokensCss = read("tokens.css");

// Legacy token names with their light and dark values as `app.css` carried them on `main`
// (app.css:2-100 before this phase). A name the dark block never overrode repeats its light value.
// Changing a row here means changing how an existing page looks.
const LEGACY: readonly (readonly [name: string, light: string, dark: string])[] = [
  ["--bg", "#f6f3ec", "#161b12"],
  ["--surface", "#ffffff", "#20271a"],
  ["--surface-2", "#f5ffee", "#273020"],
  ["--ink", "#343f2b", "#e4ecdb"],
  ["--ink-strong", "#20271a", "#f5ffee"],
  ["--muted", "rgba(52, 63, 43, 0.62)", "rgba(228, 236, 219, 0.62)"],
  ["--line", "#dae3d3", "#343f2b"],
  ["--accent", "#97ff48", "#97ff48"],
  ["--accent-ink", "#20271a", "#20271a"],
  ["--accent-soft", "#e7fad7", "#2c3a22"],
  ["--warn", "#fff4ed", "#3a2418"],
  ["--warn-ink", "#8a3b12", "#ffc9a8"],
  ["--danger", "#b3261e", "#ff9a92"],
  ["--bubble-in", "#ffffff", "#273020"],
  ["--bubble-out", "#d9fdd3", "#2f4a24"],
  ["--bubble-human", "#e3efff", "#1f3350"],
  ["--chat-bg", "#efeae2", "#1b2116"],
  ["--focus", "#20271a", "#97ff48"],
  ["--series-1", "#2a78d6", "#3987e5"],
  ["--series-2", "#eb6834", "#d95926"],
  ["--series-3", "#1baf7a", "#199e70"],
  ["--ramp-1", "#86b6ef", "#6da7ec"],
  ["--ramp-2", "#5598e7", "#4a91e9"],
  ["--ramp-3", "#2a78d6", "#307cd7"],
  ["--ramp-4", "#1c5cab", "#2469ba"],
  ["--ramp-5", "#104281", "#18579e"],
  ["--chart-grid", "#e1e0d9", "#2c2c2a"],
  ["--chart-axis", "#c3c2b7", "#383835"],
  ["--chart-label", "#52514e", "#c3c2b7"],
  ["--radius", "16px", "16px"],
  ["--radius-lg", "24px", "24px"],
  ["--font", 'Inter, system-ui, -apple-system, "Segoe UI", sans-serif', 'Inter, system-ui, -apple-system, "Segoe UI", sans-serif'],
  ["--mono", '"Azeret Mono", ui-monospace, monospace', '"Azeret Mono", ui-monospace, monospace'],
];

const PHONE = [
  "--phone-wallpaper",
  "--phone-header",
  "--phone-bubble-in",
  "--phone-bubble-out",
  "--phone-tick",
  "--phone-meta",
  "--phone-reply",
] as const;

const COLOUR_LITERAL = /#[0-9a-fA-F]{3,8}\b|\b(?:rgba?|hsla?|oklch|oklab|lch|lab|hwb|color)\(/;

const stripComments = (css: string) => css.replace(/\/\*[\s\S]*?\*\//g, "");
const squash = (value: string) => value.replace(/\s+/g, "").toLowerCase();

interface Declaration {
  name: string;
  value: string;
}

/** Every `--name: value;` in a stylesheet, in source order. */
function declarations(css: string): Declaration[] {
  return [...stripComments(css).matchAll(/(?:^|[;{\s])(--[\w-]+)\s*:\s*([^;}]+)/g)].map((m) => ({
    name: m[1]!,
    value: m[2]!.trim(),
  }));
}

/** Split `light-dark(a, b)` on its top-level comma (colour values contain commas of their own). */
function lightDark(value: string): [string, string] | null {
  const m = /^light-dark\(([\s\S]*)\)$/.exec(value.trim());
  if (!m) return null;
  const body = m[1]!;
  let depth = 0;
  let quote = "";
  for (let i = 0; i < body.length; i += 1) {
    const ch = body[i]!;
    if (quote) {
      if (ch === quote) quote = "";
    } else if (ch === '"' || ch === "'") quote = ch;
    else if (ch === "(") depth += 1;
    else if (ch === ")") depth -= 1;
    else if (ch === "," && depth === 0) return [body.slice(0, i).trim(), body.slice(i + 1).trim()];
  }
  return null;
}

const tokens = declarations(tokensCss);
const byName = new Map(tokens.map((d) => [d.name, d.value]));

/** `{ selector: declaration block }` for the `:root` colour-scheme rules. */
function rootRules(css: string): Map<string, string> {
  const rules = new Map<string, string>();
  for (const m of stripComments(css).matchAll(/(:root(?:\[data-theme="(?:light|dark)"\])?)\s*\{([^}]*)\}/g)) {
    rules.set(m[1]!, `${rules.get(m[1]!) ?? ""}${m[2]!}`);
  }
  return rules;
}

describe("tokens.css", () => {
  it("declares each custom property once", () => {
    const seen = new Set<string>();
    const repeated = tokens.filter((d) => seen.has(d.name) || !seen.add(d.name)).map((d) => d.name);
    expect(repeated).toEqual([]);
  });

  describe("legacy names keep their current values", () => {
    it.each(LEGACY)("%s", (name, light, dark) => {
      const value = byName.get(name);
      expect(value, `${name} is declared`).toBeDefined();
      const isColour = COLOUR_LITERAL.test(light);
      if (isColour) {
        const pair = lightDark(value!);
        expect(pair, `${name} uses light-dark()`).not.toBeNull();
        expect(squash(pair![0])).toBe(squash(light));
        expect(squash(pair![1])).toBe(squash(dark));
      } else {
        // Not a colour (radius, font stacks): one value for both themes.
        expect(squash(value!)).toBe(squash(light));
        expect(light).toBe(dark);
      }
    });
  });

  it("declares the Phase 1 seed palette as the light-theme ground, ink and live accent", () => {
    const light = (name: string) => squash(lightDark(byName.get(name) ?? "")?.[0] ?? "");
    expect(light("--ground")).toBe("#f6f3ec");
    expect(light("--ink")).toBe("#343f2b");
    expect(light("--live")).toBe("#97ff48");
  });

  it("declares the phone skin in both themes", () => {
    for (const name of PHONE) {
      const pair = lightDark(byName.get(name) ?? "");
      expect(pair, `${name} is a light-dark() pair`).not.toBeNull();
    }
  });

  it("keeps the chat colours today's tokens carry in the phone skin", () => {
    const same = (phone: string, legacy: string) => {
      expect(squash(byName.get(phone) ?? ""), phone).toBe(squash(byName.get(legacy) ?? "?"));
    };
    same("--phone-wallpaper", "--chat-bg");
    same("--phone-bubble-in", "--bubble-in");
    same("--phone-bubble-out", "--bubble-out");
  });

  describe("ordinal ramp (dataviz validate_palette.js --ordinal, per theme)", () => {
    // The validator's thresholds: adjacent OKLCH L steps of at least 0.06, and the end step
    // (the one nearest the surface in lightness) at 2:1 or better against the surface it sits on.
    const MIN_STEP = 0.06;
    const MIN_END_CONTRAST = 2;
    const channel = (hex: string, at: number) => {
      const c = parseInt(hex.slice(at, at + 2), 16) / 255;
      return c <= 0.04045 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4;
    };
    const linear = (hex: string) => [1, 3, 5].map((at) => channel(hex, at)) as [number, number, number];
    const oklabL = (hex: string) => {
      const [r, g, b] = linear(hex);
      const l = Math.cbrt(0.4122214708 * r + 0.5363325363 * g + 0.0514459929 * b);
      const m = Math.cbrt(0.2119034982 * r + 0.6806995451 * g + 0.1073969566 * b);
      const s = Math.cbrt(0.0883024619 * r + 0.2817188376 * g + 0.6299787005 * b);
      return 0.2104542553 * l + 0.793617785 * m - 0.0040720468 * s;
    };
    const luminance = (hex: string) => {
      const [r, g, b] = linear(hex);
      return 0.2126 * r + 0.7152 * g + 0.0722 * b;
    };
    const contrast = (a: string, b: string) => {
      const [hi, lo] = [luminance(a), luminance(b)].sort((x, y) => y - x);
      return (hi! + 0.05) / (lo! + 0.05);
    };
    const side = (name: string, theme: 0 | 1) => {
      const pair = lightDark(byName.get(name) ?? "");
      expect(pair, `${name} is a light-dark() pair`).not.toBeNull();
      return pair![theme].toLowerCase();
    };
    const ramp = (theme: 0 | 1) => [1, 2, 3, 4, 5].map((n) => side(`--ramp-${n}`, theme));

    it.each([
      ["light", 0],
      ["dark", 1],
    ] as const)("steps %s ramp monotonically, at least 0.06 apart in OKLCH L", (_theme, index) => {
      const lightness = ramp(index).map(oklabL);
      const gaps = lightness.slice(1).map((l, i) => lightness[i]! - l);
      expect(gaps.every((g) => g >= MIN_STEP), `gaps ${gaps.map((g) => g.toFixed(3)).join(", ")}`).toBe(true);
    });

    it.each([
      ["light", 0, "--panel"],
      ["dark", 1, "--panel"],
    ] as const)("keeps the %s ramp end step at 2:1 or better on the chart surface", (_theme, index, surface) => {
      const steps = ramp(index);
      const end = [...steps].sort((a, b) => (index === 0 ? oklabL(b) - oklabL(a) : oklabL(a) - oklabL(b)))[0]!;
      expect(contrast(end, side(surface, index))).toBeGreaterThanOrEqual(MIN_END_CONTRAST);
    });

    it("pins the dark ramp to the validated set", () => {
      expect(ramp(1)).toEqual(["#6da7ec", "#4a91e9", "#307cd7", "#2469ba", "#18579e"]);
    });
  });

  it("is imported before app.css", () => {
    const main = readFileSync(join(import.meta.dirname, "../../src/main.tsx"), "utf8");
    const tokensAt = main.indexOf('"./styles/tokens.css"');
    expect(tokensAt).toBeGreaterThan(-1);
    expect(tokensAt).toBeLessThan(main.indexOf('"./styles/app.css"'));
  });

  it("declares spacing, radius, type and motion tokens", () => {
    for (const prefix of ["--space-", "--radius-", "--text-", "--motion-"]) {
      expect(tokens.some((d) => d.name.startsWith(prefix)), `${prefix}* is declared`).toBe(true);
    }
    expect(byName.has("--ease-out")).toBe(true);
  });

  it("declares every colour through light-dark()", () => {
    const bare = tokens.filter((d) => COLOUR_LITERAL.test(d.value) && !lightDark(d.value)).map((d) => d.name);
    expect(bare).toEqual([]);
    for (const d of tokens) {
      const pair = lightDark(d.value);
      if (pair) expect(pair.every((side) => side.length > 0), `${d.name} has two sides`).toBe(true);
    }
  });

  it("switches theme through color-scheme", () => {
    const rules = rootRules(tokensCss);
    const scheme = (selector: string) => /color-scheme:\s*([^;]+)/.exec(rules.get(selector) ?? "")?.[1]?.trim();
    expect(scheme(":root")).toBe("light dark");
    expect(scheme(':root[data-theme="light"]')).toBe("light");
    expect(scheme(':root[data-theme="dark"]')).toBe("dark");
  });

  it("resolves every var() reference, with no cycle", () => {
    const refs = (value: string) => [...value.matchAll(/var\(\s*(--[\w-]+)/g)].map((m) => m[1]!);
    const unresolved = [
      ...tokens.flatMap((d) => refs(d.value)),
      ...refs(stripComments(appCss)),
    ].filter((name) => !byName.has(name));
    expect([...new Set(unresolved)]).toEqual([]);

    const visiting = new Set<string>();
    const done = new Set<string>();
    const visit = (name: string): void => {
      if (done.has(name)) return;
      expect(visiting.has(name), `cycle through ${name}`).toBe(false);
      visiting.add(name);
      for (const next of refs(byName.get(name) ?? "")) visit(next);
      visiting.delete(name);
      done.add(name);
    };
    for (const name of byName.keys()) visit(name);
  });
});

describe("app.css", () => {
  it("defines no custom property", () => {
    expect(declarations(appCss).map((d) => d.name)).toEqual([]);
  });

  it("keeps no prefers-color-scheme block", () => {
    expect(appCss).not.toContain("prefers-color-scheme");
    expect(tokensCss).not.toContain("prefers-color-scheme");
  });

  it("keeps no colour literal", () => {
    expect(stripComments(appCss).match(COLOUR_LITERAL)).toBeNull();
  });
});
