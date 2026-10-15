// The one place chart styling lives (spec 003 decision: Recharts, wrapped in one theme module).
// Colors are CSS custom properties from styles/tokens.css (light-dark() pairs), so light and dark
// follow the page theme. Nothing here defines a colour.
// Categorical slots 1-3 come from the validated dataviz reference palette; the ordinal ramp is a
// single blue hue for ordered stages.

export const seriesColor = ["var(--series-1)", "var(--series-2)", "var(--series-3)"] as const;
export const rampColor = ["var(--ramp-1)", "var(--ramp-2)", "var(--ramp-3)", "var(--ramp-4)", "var(--ramp-5)"] as const;

export const chartText = { fill: "var(--chart-label)", fontSize: 12 } as const;
export const gridStroke = "var(--chart-grid)";
export const axisStroke = "var(--chart-axis)";

export const tooltipStyle = {
  contentStyle: {
    background: "var(--panel)",
    border: "1px solid var(--seam)",
    borderRadius: "var(--radius-3)",
    color: "var(--ink-strong)",
    fontSize: 13,
  },
  labelStyle: { color: "var(--ink-strong)", fontWeight: 600 },
  itemStyle: { color: "var(--ink)" },
  cursor: { fill: "var(--live-soft)", opacity: 0.5 },
} as const;

/** Bars are thin with a rounded data end, 2px apart (marks spec). */
export const barRadius: [number, number, number, number] = [4, 4, 0, 0];
export const barRadiusHorizontal: [number, number, number, number] = [0, 4, 4, 0];

/** Entry animation: the length of --motion-slow, ease-out. Bars draw once, on first paint (BarCards.tsx). */
export const barAnimation = { animationDuration: 320, animationEasing: "ease-out" } as const;
