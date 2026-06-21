import { useEffect, useMemo, useState } from "react";
import { ChevronLeft, ChevronRight, RefreshCw } from "lucide-react";
import { api, ApiRequestError } from "../api";
import type { TableGroups, TableRowsResponse } from "../types";
import DataTable from "../components/DataTable";
import Spinner from "../components/Spinner";
import clsx from "../lib/clsx";

const PAGE_SIZE = 25;

export default function Explorer() {
  const [groups, setGroups] = useState<TableGroups | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const [offset, setOffset] = useState(0);
  const [data, setData] = useState<TableRowsResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api
      .tables()
      .then((t) => {
        setGroups(t);
        const first = t.dimensions[0] || t.facts[0] || t.cuboids[0];
        if (first) setSelected(first);
      })
      .catch((e) => setError(e instanceof ApiRequestError ? e.message : "Could not load tables."));
  }, []);

  useEffect(() => {
    if (!selected) return;
    setLoading(true);
    setError(null);
    api
      .tableRows(selected, PAGE_SIZE, offset)
      .then(setData)
      .catch((e) => setError(e instanceof ApiRequestError ? e.message : "Could not load rows."))
      .finally(() => setLoading(false));
  }, [selected, offset]);

  const lineageCols = useMemo(
    () => ["_bronze_path", "_file_checksum", "_row_checksum", "_pipeline_run_id"],
    []
  );

  return (
    <div className="grid grid-cols-1 gap-6 lg:grid-cols-[260px_1fr]">
      <div className="card flex flex-col gap-4 p-4">
        <TableList title="Dimensions" items={groups?.dimensions} selected={selected} onSelect={pick} />
        <TableList title="Facts" items={groups?.facts} selected={selected} onSelect={pick} />
        <TableList title="Cuboids" items={groups?.cuboids} selected={selected} onSelect={pick} />
      </div>

      <div className="card flex flex-col gap-4 p-6">
        {!selected && <p className="muted">Pick a table on the left.</p>}
        {selected && (
          <>
            <div className="flex items-center justify-between">
              <div>
                <p className="section-title">{selected}</p>
                <p className="muted">{data ? `${data.total_rows} total rows` : ""}</p>
              </div>
              <button className="btn-secondary" onClick={() => setOffset((o) => o)}>
                <RefreshCw size={14} /> Refresh
              </button>
            </div>

            {loading && <Spinner label="Loading rows..." />}
            {error && <p className="text-sm text-rose-600">{error}</p>}
            {!loading && data && (
              <>
                <DataTable rows={data.rows} highlightCols={lineageCols} />
                <div className="flex items-center justify-between">
                  <span className="muted">
                    Rows {data.offset + 1}-{Math.min(data.offset + data.rows.length, data.total_rows)} of{" "}
                    {data.total_rows}
                  </span>
                  <div className="flex gap-2">
                    <button
                      className="btn-secondary"
                      disabled={offset === 0}
                      onClick={() => setOffset((o) => Math.max(0, o - PAGE_SIZE))}
                    >
                      <ChevronLeft size={15} /> Prev
                    </button>
                    <button
                      className="btn-secondary"
                      disabled={offset + PAGE_SIZE >= data.total_rows}
                      onClick={() => setOffset((o) => o + PAGE_SIZE)}
                    >
                      Next <ChevronRight size={15} />
                    </button>
                  </div>
                </div>
              </>
            )}
          </>
        )}
      </div>
    </div>
  );

  function pick(name: string) {
    setSelected(name);
    setOffset(0);
  }
}

function TableList({
  title,
  items,
  selected,
  onSelect,
}: {
  title: string;
  items?: string[];
  selected: string | null;
  onSelect: (name: string) => void;
}) {
  if (!items || items.length === 0) return null;
  return (
    <div>
      <p className="mb-2 text-xs font-semibold uppercase tracking-wide text-slate-400">{title}</p>
      <div className="flex flex-col gap-1">
        {items.map((t) => (
          <button
            key={t}
            onClick={() => onSelect(t)}
            className={clsx(
              "truncate rounded-lg px-3 py-1.5 text-left text-sm transition",
              selected === t ? "bg-brand-gradient text-white" : "text-slate-600 hover:bg-lavender-50"
            )}
            title={t}
          >
            {t}
          </button>
        ))}
      </div>
    </div>
  );
}
