import "../styles/surfaces/operator.css";

/**
 * One figure with its label and, where there is one, a hint. The class `stat` stays (the Economics
 * tests find a tile by it); the figure is a tabular number and the tile keeps a fixed minimum height,
 * so a refreshed value never moves its neighbours.
 */
export function StatTile({ label, value, hint }: { label: string; value: string; hint?: string }) {
  return (
    <div className="stat stat-tile">
      <strong className="stat-tile__value">{value}</strong>
      <div className="stat-tile__label">{label}</div>
      {hint && <div className="stat-tile__hint">{hint}</div>}
    </div>
  );
}
