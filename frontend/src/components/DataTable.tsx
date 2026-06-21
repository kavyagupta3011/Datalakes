export default function DataTable({
  rows,
  emptyLabel = "No rows.",
  highlightCols = [],
}: {
  rows: Record<string, unknown>[];
  emptyLabel?: string;
  highlightCols?: string[];
}) {
  if (rows.length === 0) {
    return <p className="muted py-6 text-center">{emptyLabel}</p>;
  }
  const cols = Object.keys(rows[0]);

  return (
    <div className="overflow-auto rounded-xl border border-slate-100">
      <table className="w-full min-w-max text-left text-sm">
        <thead className="sticky top-0 bg-lavender-50/80 backdrop-blur-sm">
          <tr>
            {cols.map((c) => (
              <th
                key={c}
                className={`whitespace-nowrap px-3 py-2 font-semibold text-slate-600 ${
                  highlightCols.includes(c) ? "text-lavender-700" : ""
                }`}
              >
                {c}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row, i) => (
            <tr key={i} className={i % 2 === 0 ? "bg-white" : "bg-lavender-50/30"}>
              {cols.map((c) => (
                <td key={c} className="whitespace-nowrap px-3 py-2 text-slate-700">
                  {formatCell(row[c])}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function formatCell(value: unknown): string {
  if (value === null || value === undefined) return "—";
  if (typeof value === "number") {
    return Number.isInteger(value) ? String(value) : value.toFixed(2);
  }
  return String(value);
}
