/// <reference types="node" />
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

// DS7: runtime dependencies stay on an allow-list and every version in package.json is an exact pin.
const pkg: Record<string, unknown> = JSON.parse(readFileSync(join(import.meta.dirname, "../../package.json"), "utf8"));

const EXISTING_RUNTIME = [
  "@fontsource/azeret-mono",
  "@fontsource/inter",
  "@tanstack/react-query",
  "react",
  "react-dom",
  "react-is",
  "react-router",
  "recharts",
] as const;
// The voice call's SDK (spec-voice-agent.web-call WV1), allowed by exact name only. Its client and
// livekit-client come in transitively through the lockfile, never as direct entries.
const VOICE_RUNTIME = ["@elevenlabs/react"] as const;
const VOICE_SDK_VERSION = "1.15.2";
const ALLOWED_PATTERNS: readonly RegExp[] = [/^@radix-ui\/react-[\w-]+$/, /^motion$/, /^@fontsource\/[\w-]+$/, /^@fontsource-variable\/[\w-]+$/];
const EXACT = /^\d+\.\d+\.\d+(-[\w.]+)?$/;

type Pin = { path: string; version: unknown };

// Flatten a dependency block. `overrides` may nest: { "pkg": { ".": "1.0.0", "child": "2.0.0" } }.
function pins(block: unknown, prefix: string): Pin[] {
  if (block === undefined) return [];
  if (typeof block !== "object" || block === null) return [{ path: prefix, version: block }];
  return Object.entries(block).flatMap(([name, value]) =>
    typeof value === "object" && value !== null ? pins(value, `${prefix}.${name}`) : [{ path: `${prefix}.${name}`, version: value }],
  );
}

describe("package.json dependencies", () => {
  it("lists runtime dependencies only from the allow-list", () => {
    const runtime = Object.keys((pkg.dependencies ?? {}) as Record<string, string>);
    const named: readonly string[] = [...EXISTING_RUNTIME, ...VOICE_RUNTIME];
    const outside = runtime.filter((name) => !named.includes(name) && !ALLOWED_PATTERNS.some((p) => p.test(name)));
    expect(outside).toEqual([]);
  });

  it("keeps every existing runtime dependency", () => {
    const runtime = Object.keys((pkg.dependencies ?? {}) as Record<string, string>);
    for (const name of EXISTING_RUNTIME) expect(runtime).toContain(name);
  });

  it("pins every version exactly in dependencies, devDependencies, peerDependencies and overrides", () => {
    const all = ["dependencies", "devDependencies", "peerDependencies", "overrides"].flatMap((block) => pins(pkg[block], block));
    expect(all.length).toBeGreaterThan(0);
    const loose = all.filter((p) => typeof p.version !== "string" || !EXACT.test(p.version)).map((p) => `${p.path}: ${String(p.version)}`);
    expect(loose).toEqual([]);
  });

  it("accepts exact versions and rejects ranges, tags and urls", () => {
    for (const ok of ["1.2.3", "13.4.4", "1.0.0-rc.1", "2.0.0-beta"]) expect(EXACT.test(ok)).toBe(true);
    for (const bad of ["^1.2.3", "~1.2.3", "1.2", "latest", "*", "file:../x", "git+https://x/y.git", "npm:foo@1.0.0", ">=1.0.0", "1.x"]) expect(EXACT.test(bad)).toBe(false);
  });

  it("flattens nested overrides so a loose nested pin is reported", () => {
    const flat = pins({ foo: { ".": "1.0.0", bar: "^2.0.0" }, baz: "3.0.0" }, "overrides");
    expect(flat).toEqual([
      { path: "overrides.foo..", version: "1.0.0" },
      { path: "overrides.foo.bar", version: "^2.0.0" },
      { path: "overrides.baz", version: "3.0.0" },
    ]);
  });

  it("allows @elevenlabs/react by exact name and no other SDK package", () => {
    const runtime = (pkg.dependencies ?? {}) as Record<string, string>;
    expect(runtime["@elevenlabs/react"]).toBe(VOICE_SDK_VERSION);
    expect(Object.keys(runtime)).not.toContain("@elevenlabs/client");
    expect(Object.keys(runtime)).not.toContain("livekit-client");
    expect(ALLOWED_PATTERNS.some((p) => p.test("@elevenlabs/client") || p.test("@elevenlabs/react"))).toBe(false);
  });
});
