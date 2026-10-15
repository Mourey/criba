import type { ReactNode } from "react";

export type TableColumn<Row> = {
  key: string;
  header: string;
  /** Figures, counts and times: Azeret Mono, tabular. */
  mono?: boolean;
  align?: "start" | "end";
  render?: (row: Row) => ReactNode;
};

export type TableProps<Row> = {
  caption: string;
  /** Keeps the caption for assistive technology and hides it visually. */
  hideCaption?: boolean;
  columns: TableColumn<Row>[];
  rows: Row[];
  rowKey: (row: Row) => string;
  /** Shown in one row spanning every column when there are no rows. */
  empty: ReactNode;
};

export function Table<Row extends object>({ caption, hideCaption = false, columns, rows, rowKey, empty }: TableProps<Row>) {
  return (
    <div className="ui-table__wrap">
      <table className="ui-table">
        <caption className={hideCaption ? "ui-table__caption ui-sr-only" : "ui-table__caption"}>{caption}</caption>
        <thead className="ui-table__head ui-table__head--sticky">
          <tr>
            {columns.map((column) => (
              <th key={column.key} scope="col" className={column.align === "end" ? "ui-table__th ui-table__th--end" : "ui-table__th"}>
                {column.header}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.length === 0 ? (
            <tr className="ui-table__empty">
              <td colSpan={columns.length}>{empty}</td>
            </tr>
          ) : (
            rows.map((row) => (
              <tr key={rowKey(row)} className="ui-table__row">
                {columns.map((column) => {
                  const classes = ["ui-table__cell", column.mono ? "ui-table__cell--mono" : "", column.align === "end" ? "ui-table__cell--end" : ""]
                    .filter(Boolean)
                    .join(" ");
                  return (
                    <td key={column.key} className={classes}>
                      {column.render ? column.render(row) : (row as Record<string, ReactNode>)[column.key]}
                    </td>
                  );
                })}
              </tr>
            ))
          )}
        </tbody>
      </table>
    </div>
  );
}
