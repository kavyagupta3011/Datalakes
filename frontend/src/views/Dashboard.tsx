import { useEffect, useState } from "react";
import { Boxes, Database, GitBranch, RefreshCw } from "lucide-react";
import { api, ApiRequestError } from "../api";
import type { PipelineStatus, TableGroups } from "../types";
import StatCard from "../components/StatCard";
import StatusBadge from "../components/StatusBadge";
import Spinner from "../components/Spinner";
import type { ViewKey } from "../components/Sidebar";

export default function Dashboard({ onNavigate }: { onNavigate: (v: ViewKey) => void }) {
  const [tables, setTables] = useState<TableGroups | null>(null);
  const [status, setStatus] = useState<PipelineStatus | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  async function load() {
    try {
      const [t, s] = await Promise.all([api.tables(), api.pipelineStatus()]);
      setTables(t);
      setStatus(s);
      setError(null);
    } catch (e) {
      setError(e instanceof ApiRequestError ? e.message : "Could not reach the API. Is it running?");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    load();
    const interval = setInterval(load, 8000);
    return () => clearInterval(interval);
  }, []);

  if (loading) {
    return (
      <div className="card flex items-center justify-center p-12">
        <Spinner label="Loading dashboard..." />
      </div>
    );
  }

  if (error) {
    return (
      <div className="card border-rose-100 p-6 text-rose-600">
        <p className="font-semibold">Couldn't reach the API</p>
        <p className="muted mt-1">{error}</p>
        <button className="btn-secondary mt-4" onClick={load}>
          <RefreshCw size={15} /> Retry
        </button>
      </div>
    );
  }

  const steps = status?.steps ?? {};
  const stepEntries = Object.entries(steps);

  return (
    <div className="flex flex-col gap-6">
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-3">
        <StatCard label="Dimension tables" value={tables?.dimensions.length ?? 0} icon={Database} accent="lavender" />
        <StatCard label="Fact tables" value={tables?.facts.length ?? 0} icon={Boxes} accent="skyblue" />
        <StatCard label="OLAP cuboids" value={tables?.cuboids.length ?? 0} icon={GitBranch} accent="lavender" />
      </div>

      <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
        <div className="card p-6">
          <div className="flex items-center justify-between">
            <p className="section-title">Last pipeline run</p>
            {status?.running && <StatusBadge tone="running">running</StatusBadge>}
          </div>
          {stepEntries.length === 0 ? (
            <p className="muted mt-3">
              No pipeline run yet.{" "}
              <button className="font-semibold text-lavender-600 underline" onClick={() => onNavigate("upload")}>
                Upload a file
              </button>{" "}
              to get started.
            </p>
          ) : (
            <ul className="mt-4 flex flex-col gap-2">
              {stepEntries.map(([step, result]) => (
                <li key={step} className="flex items-center justify-between rounded-lg bg-slate-50 px-3 py-2">
                  <span className="text-sm font-medium text-slate-700">{step}</span>
                  <div className="flex items-center gap-2">
                    <span className="muted">{result.attempts} attempt(s)</span>
                    <StatusBadge tone={result.status === "success" ? "success" : "failed"}>
                      {result.status}
                    </StatusBadge>
                  </div>
                </li>
              ))}
            </ul>
          )}
        </div>

        <div className="card p-6">
          <p className="section-title">Gold layer</p>
          <p className="muted mt-1">Auto-discovered tables across every domain.</p>
          <div className="mt-4 flex flex-col gap-3">
            <TableGroup label="Dimensions" items={tables?.dimensions ?? []} accent="lavender" />
            <TableGroup label="Facts" items={tables?.facts ?? []} accent="skyblue" />
          </div>
          <button className="btn-secondary mt-5 w-full" onClick={() => onNavigate("explorer")}>
            Browse all tables
          </button>
        </div>
      </div>
    </div>
  );
}

function TableGroup({
  label,
  items,
  accent,
}: {
  label: string;
  items: string[];
  accent: "lavender" | "skyblue";
}) {
  const chip = accent === "lavender" ? "bg-lavender-100 text-lavender-700" : "bg-skyblue-100 text-skyblue-700";
  return (
    <div>
      <p className="text-xs font-semibold uppercase tracking-wide text-slate-400">{label}</p>
      <div className="mt-2 flex flex-wrap gap-2">
        {items.length === 0 && <span className="muted">none yet</span>}
        {items.map((t) => (
          <span key={t} className={`rounded-full px-2.5 py-1 text-xs font-medium ${chip}`}>
            {t}
          </span>
        ))}
      </div>
    </div>
  );
}
