// Accessibility check for the e2e specs (spec SA7, master R21): axe-core against WCAG 2.2 AA, in
// both themes. No rule is ever disabled here; fix the page instead.
import AxeBuilder from "@axe-core/playwright";
import type { Page } from "@playwright/test";

const TAGS = ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa"];

/** Runs axe once with data-theme="light" and once with "dark", then restores the page's theme.
 * Reduced motion is emulated first, so no element is caught mid-fade. Throws with every rule id and
 * node target found. */
export async function expectNoA11yViolations(page: Page): Promise<void> {
  await page.emulateMedia({ reducedMotion: "reduce" });
  const original = await page.evaluate(() => document.documentElement.getAttribute("data-theme"));
  const found: string[] = [];
  try {
    for (const theme of ["light", "dark"] as const) {
      await page.evaluate((value) => document.documentElement.setAttribute("data-theme", value), theme);
      const { violations } = await new AxeBuilder({ page }).withTags(TAGS).analyze();
      for (const v of violations) {
        found.push(`[${theme}] ${v.id}: ${v.nodes.map((n) => n.target.join(" ")).join(", ")}`);
      }
    }
  } finally {
    await page.evaluate((value) => {
      if (value === null) document.documentElement.removeAttribute("data-theme");
      else document.documentElement.setAttribute("data-theme", value);
    }, original);
  }
  if (found.length > 0) {
    throw new Error(`axe found ${found.length} violation(s) on ${page.url()}:\n${found.join("\n")}`);
  }
}
