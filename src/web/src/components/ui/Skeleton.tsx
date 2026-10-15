export type SkeletonShape = "line" | "block" | "circle";

// A loading placeholder. The shimmer stops under prefers-reduced-motion (ui.css).
export function Skeleton({ shape = "line", label = "Loading" }: { shape?: SkeletonShape; label?: string }) {
  return <span className={`ui-skeleton ui-skeleton--${shape}`} role="status" aria-label={label} />;
}
