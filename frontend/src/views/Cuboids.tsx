import { useEffect, useMemo, useState } from "react";
import {
  Bar,
  BarChart,
  CartesianGrid,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { Hash, Layers, Sigma, Search } from "lucide-react";
import { api, ApiRequestError } from "../api";
import type { TableGroups } from "../types";
import DataTable from "../components/DataTable";
import Spinner from "../components/Spinner";
import clsx from "../lib/clsx";

type Row = Record<string, unknown>;

export default function Cuboids() {
  const [groups, setGroups] = useState<TableGroups | null>(null);
  const [fact, setFact] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api
      .tables()
      .then((t) => {
        setGroups(t);
        if (t.facts[0]) setFact(t.facts[0]);
      })
      .catch((e) => setError(e instanceof ApiRequestError ? e.message : "Could not load tables."));
  }, []);

  const related = useMemo(() => {
    if (!fact || !groups) return { apex: undefined, byMonth: undefined, byDim: [] as string[], combo: [] as string[] };
    const prefix = `cuboid_${fact}_`;
    const mine = groups.cuboids.filter((c) => c.startsWith(prefix));
    const apex = mine.find((c) => c === `${prefix}apex`);
    const byMonth = mine.find((c) => c === `${prefix}by_month`);
    const byDim = mine.filter((c) => c.startsWith(`${prefix}by_`) && c !== byMonth && !c.includes(`${prefix}by_month_`));
    const combo = mine.filter((c) => c.includes(`${prefix}by_month_`));
    return { apex, byMonth, byDim, combo };
  }, [fact, groups]);

  if (error) return <p className="text-sm text-rose-600">{error}</p>;
  if (!groups) return <Spinner label="Loading..." />;
  if (groups.facts.length === 0) {
    return <p className="muted">No fact tables yet - upload data and run the pipeline first.</p>;
  }

  return (
    <div className="flex flex-col gap-6">
      <div className="card flex flex-wrap items-center gap-2 p-4">
        <span className="muted mr-2">Fact table</span>
        {groups.facts.map((f) => (
          <button
            key={f}
            onClick={() => setFact(f)}
            className={clsx(
              "rounded-full px-3 py-1.5 text-sm font-medium transition",
              fact === f ? "bg-brand-gradient text-white" : "bg-lavender-50 text-lavender-700 hover:bg-lavender-100"
            )}
          >
            {f}
          </button>
        ))}
      </div>

      {fact && related.apex && <ApexCard cuboid={related.apex} />}

      {fact && related.byMonth && <MonthChart fact={fact} cuboid={related.byMonth} />}

      {fact && related.byDim.length > 0 && <DimensionChart fact={fact} cuboids={related.byDim} />}
    </div>
  );
}

function ApexCard({ cuboid }: { cuboid: string }) {
  const [rows, setRows] = useState<Row[] | null>(null);

  useEffect(() => {
    setRows(null);
    api.tableRows(cuboid, 50, 0).then((r) => setRows(r.rows));
  }, [cuboid]);

  return (
    <div className="card p-6">
      <div className="mb-4 flex items-center gap-2">
        <Sigma size={18} className="text-lavender-500" />
        <p className="section-title">Grand totals</p>
      </div>
      {!rows ? (
        <Spinner />
      ) : (
        <div className="grid grid-cols-2 gap-4 sm:grid-cols-3 lg:grid-cols-4">
          {rows.map((r, i) => (
            <div key={i} className="rounded-xl bg-brand-gradient-soft p-4">
              <p className="text-xs font-semibold uppercase tracking-wide text-lavender-700">
                {String(r.measure)}
              </p>
              <p className="mt-1 text-2xl font-extrabold text-slate-800">{fmt(r.sum)}</p>
              <p className="muted mt-1">
                count {fmt(r.count)} · avg {fmt(r.mean)}
              </p>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function MonthChart({ fact, cuboid }: { fact: string; cuboid: string }) {
  const [rows, setRows] = useState<Row[] | null>(null);
  const [measure, setMeasure] = useState<string | null>(null);
  const [drill, setDrill] = useState<{ year: number; month: number } | null>(null);
  const [drillRows, setDrillRows] = useState<Row[] | null>(null);

  useEffect(() => {
    setRows(null);
    api.tableRows(cuboid, 500, 0).then((r) => {
      setRows(r.rows);
      const measures = sumKeys(r.rows[0] ?? {});
      setMeasure(measures[0] ?? null);
    });
  }, [cuboid]);

  useEffect(() => {
    if (!drill) {
      setDrillRows(null);
      return;
    }
    api.drill(fact, { year: drill.year, month: drill.month }, 50).then((r) => setDrillRows(r.rows));
  }, [drill, fact]);

  const chartData = useMemo(() => {
    if (!rows) return [];
    return [...rows]
      .sort((a, b) => Number(a.year) * 12 + Number(a.month) - (Number(b.year) * 12 + Number(b.month)))
      .map((r) => ({
        label: `${String(r.month_name).slice(0, 3)} '${String(r.year).slice(2)}`,
        value: measure ? Number(r[measure] ?? 0) : 0,
        year: r.year,
        month: r.month,
      }));
  }, [rows, measure]);

  if (!rows) return <Spinner />;
  const measures = sumKeys(rows[0] ?? {});

  return (
    <div className="card p-6">
      <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
        <div className="flex items-center gap-2">
          <Layers size={18} className="text-skyblue-500" />
          <p className="section-title">Monthly trend</p>
        </div>
        {measures.length > 1 && (
          <select className="input w-auto" value={measure ?? ""} onChange={(e) => setMeasure(e.target.value)}>
            {measures.map((m) => (
              <option key={m} value={m}>
                {m}
              </option>
            ))}
          </select>
        )}
      </div>

      <ResponsiveContainer width="100%" height={260}>
        <LineChart data={chartData} onClick={(e) => {
          const p = e?.activePayload?.[0]?.payload;
          if (p) setDrill({ year: Number(p.year), month: Number(p.month) });
        }}>
          <CartesianGrid strokeDasharray="3 3" stroke="#ede9fe" />
          <XAxis dataKey="label" tick={{ fontSize: 12 }} stroke="#94a3b8" />
          <YAxis tick={{ fontSize: 12 }} stroke="#94a3b8" />
          <Tooltip contentStyle={{ borderRadius: 12, borderColor: "#ddd6fe" }} />
          <Line type="monotone" dataKey="value" stroke="#8b5cf6" strokeWidth={2.5} dot={{ r: 3, fill: "#3b82f6" }} />
        </LineChart>
      </ResponsiveContainer>
      <p className="muted mt-2">Click a point to drill into that month's underlying fact rows.</p>

      {drill && (
        <div className="mt-4 rounded-xl bg-slate-50 p-4">
          <div className="mb-2 flex items-center justify-between">
            <p className="text-sm font-semibold text-slate-700">
              Drill: {drill.year}-{String(drill.month).padStart(2, "0")}
            </p>
            <button className="muted underline" onClick={() => setDrill(null)}>
              clear
            </button>
          </div>
          {!drillRows ? <Spinner /> : <DataTable rows={drillRows} highlightCols={["_bronze_path", "_file_checksum"]} />}
        </div>
      )}
    </div>
  );
}

function DimensionChart({ fact, cuboids }: { fact: string; cuboids: string[] }) {
  const [active, setActive] = useState(cuboids[0]);
  const [rows, setRows] = useState<Row[] | null>(null);
  const [measure, setMeasure] = useState<string | null>(null);
  const [labels, setLabels] = useState<Record<string, string>>({});
  const [drillKey, setDrillKey] = useState<string | number | null>(null);
  const [drillRows, setDrillRows] = useState<Row[] | null>(null);

  const dimFk = active.replace(`cuboid_${fact}_by_`, "");

  useEffect(() => {
    setRows(null);
    setLabels({});
    api.tableRows(active, 1000, 0).then((r) => {
      setRows(r.rows);
      const measures = sumKeys(r.rows[0] ?? {});
      setMeasure(measures[0] ?? null);
    });

    const entity = dimFk.replace(/_key$/, "");
    api
      .tableRows(`dim_${entity}`, 1000, 0)
      .then((r) => {
        const map: Record<string, string> = {};
        for (const row of r.rows) {
          const key = row[dimFk as keyof Row] ?? row[`${entity}_key`];
          if (key === undefined) continue;
          map[String(key)] = pickLabel(row, entity);
        }
        setLabels(map);
      })
      .catch(() => setLabels({}));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [active]);

  useEffect(() => {
    if (drillKey === null) {
      setDrillRows(null);
      return;
    }
    api.drill(fact, { [dimFk]: drillKey }, 50).then((r) => setDrillRows(r.rows));
  }, [drillKey, fact, dimFk]);

  const chartData = useMemo(() => {
    if (!rows || !measure) return [];
    return [...rows]
      .map((r) => ({
        key: r[dimFk],
        label: labels[String(r[dimFk])] ?? String(r[dimFk]),
        value: Number(r[measure] ?? 0),
      }))
      .sort((a, b) => b.value - a.value)
      .slice(0, 15);
  }, [rows, measure, labels, dimFk]);

  if (!rows) return <Spinner />;
  const measures = sumKeys(rows[0] ?? {});

  return (
    <div className="card p-6">
      <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
        <div className="flex items-center gap-2">
          <Hash size={18} className="text-lavender-500" />
          <p className="section-title">By dimension</p>
        </div>
        <div className="flex gap-2">
          {cuboids.length > 1 &&
            cuboids.map((c) => (
              <button
                key={c}
                onClick={() => setActive(c)}
                className={clsx(
                  "rounded-full px-3 py-1 text-xs font-medium",
                  active === c ? "bg-skyblue-500 text-white" : "bg-skyblue-50 text-skyblue-700"
                )}
              >
                {c.replace(`cuboid_${fact}_by_`, "")}
              </button>
            ))}
          {measures.length > 1 && (
            <select className="input w-auto" value={measure ?? ""} onChange={(e) => setMeasure(e.target.value)}>
              {measures.map((m) => (
                <option key={m} value={m}>
                  {m}
                </option>
              ))}
            </select>
          )}
        </div>
      </div>

      <ResponsiveContainer width="100%" height={Math.max(220, chartData.length * 28)}>
        <BarChart
          data={chartData}
          layout="vertical"
          margin={{ left: 16 }}
          onClick={(e) => {
            const p = e?.activePayload?.[0]?.payload;
            if (p) setDrillKey(p.key);
          }}
        >
          <CartesianGrid strokeDasharray="3 3" stroke="#ede9fe" />
          <XAxis type="number" tick={{ fontSize: 12 }} stroke="#94a3b8" />
          <YAxis type="category" dataKey="label" width={140} tick={{ fontSize: 12 }} stroke="#94a3b8" />
          <Tooltip contentStyle={{ borderRadius: 12, borderColor: "#bfdbfe" }} />
          <Bar dataKey="value" fill="#60a5fa" radius={[0, 6, 6, 0]} cursor="pointer" />
        </BarChart>
      </ResponsiveContainer>
      <p className="muted mt-2 flex items-center gap-1">
        <Search size={13} /> Click a bar to drill into the underlying fact rows (with lineage columns).
      </p>

      {drillKey !== null && (
        <div className="mt-4 rounded-xl bg-slate-50 p-4">
          <div className="mb-2 flex items-center justify-between">
            <p className="text-sm font-semibold text-slate-700">
              Drill: {dimFk} = {labels[String(drillKey)] ?? String(drillKey)}
            </p>
            <button className="muted underline" onClick={() => setDrillKey(null)}>
              clear
            </button>
          </div>
          {!drillRows ? <Spinner /> : <DataTable rows={drillRows} highlightCols={["_bronze_path", "_file_checksum"]} />}
        </div>
      )}
    </div>
  );
}

function sumKeys(row: Row): string[] {
  return Object.keys(row).filter((k) => k.endsWith("_sum"));
}

function pickLabel(row: Row, entity: string): string {
  if (typeof row.name === "string") return row.name;
  const skip = new Set([`${entity}_key`, "_needs_review", "_pipeline_run_id", "_row_checksum", "_file_checksum"]);
  for (const [k, v] of Object.entries(row)) {
    if (skip.has(k) || k.startsWith("_") || k.endsWith("_key")) continue;
    if (typeof v === "string") return v;
  }
  return String(row[`${entity}_key`] ?? "");
}

function fmt(v: unknown): string {
  const n = Number(v);
  if (Number.isNaN(n)) return String(v ?? "—");
  return Number.isInteger(n) ? n.toLocaleString() : n.toLocaleString(undefined, { maximumFractionDigits: 2 });
}
