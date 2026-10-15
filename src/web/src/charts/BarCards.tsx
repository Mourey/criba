import { useState } from "react";
import { Bar, BarChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import "../styles/surfaces/operator.css";
// Primitives by file, not through components/ui/index.ts (see Shell.tsx).
import { Card } from "../components/ui/Card.tsx";
import { Table, type TableColumn } from "../components/ui/Table.tsx";
import "../components/ui/ui.css";
import {
  axisStroke,
  barAnimation,
  barRadius,
  barRadiusHorizontal,
  chartText,
  gridStroke,
  rampColor,
  seriesColor,
  tooltipStyle,
} from "./theme.ts";

const EMPTY_CHART = "Nothing to chart in this period.";

/** True when there is nothing to draw: no values, or every value is 0. */
const noData = (values: number[]) => values.every((v) => !(v > 0));

export interface Datum {
  label: string;
  value: number;
}

/** Bars draw in once, on first paint: a refetch updates them in place. Never under reduced motion. */
function useDrawOnce() {
  const [drawn, setDrawn] = useState(false);
  const reduced = typeof window !== "undefined" && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  return { animate: !drawn && !reduced, onAnimationEnd: () => setDrawn(true) };
}

/**
 * A single-series bar chart in a card, with the same numbers in a table view (the chart is never
 * the only way to read them). `ordinal` shades the bars along one blue ramp for ordered stages.
 */
export function BarCard(props: {
  title: string;
  caption?: string;
  data: Datum[];
  format?: (value: number) => string;
  /** Shorter format for axis ticks, when the tooltip format is too precise to read there. */
  axisFormat?: (value: number) => string;
  ordinal?: boolean;
  horizontal?: boolean;
  height?: number;
  /** Heading level of the title; the chart sits directly under the h1 by default. */
  level?: 2 | 3 | 4;
  /** Header of the label column in the table view. */
  labelHeader?: string;
  /** Said in place of the plot when every value is 0 (or there is no data); the table view stays. */
  empty?: string;
}) {
  const { data, format = (v) => v.toLocaleString("en-US"), horizontal } = props;
  const axisFormat = props.axisFormat ?? format;
  // A chart with no format of its own counts things (Applications, Escalations): its value axis has whole ticks only.
  const allowDecimals = Boolean(props.format);
  const height = props.height ?? (horizontal ? Math.max(180, data.length * 34 + 40) : 260);
  const draw = useDrawOnce();
  const colorAt = (i: number) =>
    props.ordinal ? rampColor[Math.min(rampColor.length - 1, Math.floor((i / Math.max(1, data.length - 1)) * (rampColor.length - 1)))] : seriesColor[0];

  return (
    <Card title={props.title} level={props.level}>
      {props.caption && <p className="chart-card__caption">{props.caption}</p>}
      {noData(data.map((d) => d.value)) ? (
        <p className="chart-card__empty">{props.empty ?? EMPTY_CHART}</p>
      ) : (
      <div className="chart-card__plot" style={{ height }} /* geometry */ aria-hidden="true">
        <ResponsiveContainer width="100%" height="100%" initialDimension={{ width: 480, height }}>
          <BarChart
            data={data}
            layout={horizontal ? "vertical" : "horizontal"}
            margin={{ top: 8, right: 16, bottom: horizontal ? 0 : 24, left: horizontal ? 8 : 0 }}
            barCategoryGap={horizontal ? 4 : "20%"}
            accessibilityLayer={false}
          >
            <CartesianGrid stroke={gridStroke} horizontal={!horizontal} vertical={Boolean(horizontal)} />
            {horizontal ? (
              <>
                <XAxis type="number" tick={chartText} stroke={axisStroke} tickFormatter={axisFormat} allowDecimals={allowDecimals} />
                <YAxis type="category" dataKey="label" tick={chartText} stroke={axisStroke} width={130} interval={0} />
              </>
            ) : (
              <>
                <XAxis dataKey="label" tick={chartText} stroke={axisStroke} interval={0} angle={-30} textAnchor="end" height={60} />
                <YAxis tick={chartText} stroke={axisStroke} tickFormatter={axisFormat} width={56} allowDecimals={allowDecimals} />
              </>
            )}
            <Tooltip {...tooltipStyle} formatter={(v) => format(Number(v))} />
            <Bar
              dataKey="value"
              name={props.title}
              radius={horizontal ? barRadiusHorizontal : barRadius}
              maxBarSize={28}
              fill={seriesColor[0]}
              isAnimationActive={draw.animate}
              onAnimationEnd={draw.onAnimationEnd}
              {...barAnimation}
              shape={(p: { x?: number; y?: number; width?: number; height?: number; index?: number }) => (
                <rect x={p.x} y={p.y} width={p.width} height={p.height} rx={4} fill={colorAt(p.index ?? 0)} />
              )}
            />
          </BarChart>
        </ResponsiveContainer>
      </div>
      )}
      <DataTable label={props.title} rows={data.map((d) => [d.label, format(d.value)])} head={[props.labelHeader ?? "Item", "Value"]} />
    </Card>
  );
}

export interface Series {
  key: string;
  name: string;
}

/** Grouped bars for two or three series. Always has a legend (identity is never color alone). */
export function GroupedBarCard(props: {
  title: string;
  caption?: string;
  data: Record<string, string | number>[];
  series: Series[];
  format?: (value: number) => string;
  level?: 2 | 3 | 4;
  labelHeader?: string;
  empty?: string;
}) {
  const { format = (v) => v.toLocaleString("en-US") } = props;
  const draw = useDrawOnce();
  const flat = noData(props.data.flatMap((d) => props.series.map((s) => Number(d[s.key]))));
  return (
    <Card title={props.title} level={props.level}>
      {props.caption && <p className="chart-card__caption">{props.caption}</p>}
      <ul className="chart-legend" aria-label={`${props.title}: series`}>
        {props.series.map((s, i) => (
          <li key={s.key} className="chart-legend__item">
            <i className={`chart-legend__swatch chart-legend__swatch--${i}`} aria-hidden="true" />
            {s.name}
          </li>
        ))}
      </ul>
      {flat ? (
        <p className="chart-card__empty">{props.empty ?? EMPTY_CHART}</p>
      ) : (
      <div className="chart-card__plot" aria-hidden="true">
        <ResponsiveContainer width="100%" height="100%" initialDimension={{ width: 480, height: 260 }}>
          <BarChart data={props.data} margin={{ top: 8, right: 16, bottom: 8, left: 0 }} barGap={2} accessibilityLayer={false}>
            <CartesianGrid stroke={gridStroke} vertical={false} />
            <XAxis dataKey="label" tick={chartText} stroke={axisStroke} interval={0} />
            <YAxis tick={chartText} stroke={axisStroke} tickFormatter={format} width={56} />
            <Tooltip {...tooltipStyle} formatter={(v) => format(Number(v))} />
            {props.series.map((s, i) => (
              <Bar
                key={s.key}
                dataKey={s.key}
                name={s.name}
                fill={seriesColor[i]}
                radius={barRadius}
                maxBarSize={28}
                isAnimationActive={draw.animate}
                onAnimationEnd={i === 0 ? draw.onAnimationEnd : undefined}
                {...barAnimation}
              />
            ))}
          </BarChart>
        </ResponsiveContainer>
      </div>
      )}
      <DataTable
        label={props.title}
        head={[props.labelHeader ?? "Item", ...props.series.map((s) => s.name)]}
        rows={props.data.map((d) => [String(d.label), ...props.series.map((s) => format(Number(d[s.key])))])}
      />
    </Card>
  );
}

interface TableRow {
  id: number;
  cells: string[];
}

function DataTable(props: { label: string; head: string[]; rows: string[][] }) {
  const columns: TableColumn<TableRow>[] = props.head.map((header, i) => ({
    key: String(i),
    header,
    mono: i > 0,
    align: i > 0 ? "end" : "start",
    render: (row) => row.cells[i],
  }));
  return (
    <details className="chart-card__details">
      <summary>Table view</summary>
      <Table
        caption={`${props.label} (table)`}
        hideCaption
        columns={columns}
        rows={props.rows.map((cells, id) => ({ id, cells }))}
        rowKey={(row) => String(row.id)}
        empty="No data"
      />
    </details>
  );
}
