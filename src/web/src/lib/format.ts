export function formatDuration(seconds: number | null | undefined): string {
  if (seconds === null || seconds === undefined) return "-";
  const s = Math.max(0, Math.round(seconds));
  if (s < 60) return `${s}s`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m}m ${s % 60}s`.replace(/ 0s$/, "");
  const h = Math.floor(m / 60);
  if (h < 48) return `${h}h ${m % 60}m`.replace(/ 0m$/, "");
  const d = Math.floor(h / 24);
  return `${d}d ${h % 24}h`.replace(/ 0h$/, "");
}

export function formatDateTime(iso: string | null | undefined): string {
  if (!iso) return "-";
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return iso;
  return date.toLocaleString("en-GB", {
    day: "2-digit",
    month: "short",
    hour: "2-digit",
    minute: "2-digit",
    timeZone: "UTC",
  }) + " UTC";
}

export function formatDate(iso: string | null | undefined): string {
  if (!iso) return "-";
  return iso.slice(0, 10);
}

export function formatNumber(value: number | null | undefined): string {
  if (value === null || value === undefined) return "-";
  return value.toLocaleString("en-US");
}

export function formatPercent(value: number | null | undefined, digits = 1): string {
  if (value === null || value === undefined) return "-";
  return `${(value * 100).toFixed(digits)}%`;
}

/** USD with enough precision for sub-cent per-conversation costs. */
export function formatUsd(value: number | null | undefined): string {
  if (value === null || value === undefined) return "-";
  const abs = Math.abs(value);
  const digits = abs === 0 ? 2 : abs < 1 ? 4 : 2;
  return `$${value.toFixed(digits)}`;
}

export function formatMs(value: number | null | undefined): string {
  if (value === null || value === undefined) return "-";
  return value >= 1000 ? `${(value / 1000).toFixed(1)} s` : `${Math.round(value)} ms`;
}

export function candidateName(c: { full_name: string | null; phone?: string | null }): string {
  return c.full_name ?? c.phone ?? "Unknown Candidate";
}

export function newMessageId(): string {
  if (typeof crypto !== "undefined" && typeof crypto.randomUUID === "function") return crypto.randomUUID();
  return `m-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`;
}

/** Compact USD for chart axes: no trailing zeros ($0.8, $0.007). */
export function formatUsdAxis(value: number): string {
  return `$${Number(value.toFixed(4))}`;
}
