import type { ReactNode } from "react";

export interface Column<T> {
  key: string;
  label: string;
  nowrap?: boolean;
  wide?: boolean;
  render: (row: T) => ReactNode;
}

export function DataTable<T>({ columns, rows, rowKey }: { columns: Column<T>[]; rows: T[]; rowKey: (row: T) => string }) {
  return (
    <div className="table-scroll">
      <table className="grid">
        <thead>
          <tr>
            {columns.map((c) => (
              <th key={c.key} className={c.nowrap ? "col-nowrap" : c.wide ? "col-wide" : undefined}>
                {c.label}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={rowKey(row)}>
              {columns.map((c) => (
                <td key={c.key} className={c.nowrap ? "col-nowrap" : c.wide ? "col-wide" : undefined}>
                  {c.render(row)}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
