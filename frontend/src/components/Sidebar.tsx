import { LayoutDashboard, UploadCloud, Table2, BarChart3, Database } from "lucide-react";
import clsx from "../lib/clsx";

export type ViewKey = "dashboard" | "upload" | "explorer" | "cuboids";

const NAV: { key: ViewKey; label: string; icon: typeof LayoutDashboard }[] = [
  { key: "dashboard", label: "Dashboard", icon: LayoutDashboard },
  { key: "upload", label: "Upload data", icon: UploadCloud },
  { key: "explorer", label: "Table explorer", icon: Table2 },
  { key: "cuboids", label: "Cuboid viewer", icon: BarChart3 },
];

export default function Sidebar({
  active,
  onChange,
}: {
  active: ViewKey;
  onChange: (v: ViewKey) => void;
}) {
  return (
    <aside className="flex h-full w-64 flex-col gap-6 border-r border-lavender-100 bg-white/70 px-5 py-6 backdrop-blur-sm">
      <div className="flex items-center gap-2 px-1">
        <div className="flex h-9 w-9 items-center justify-center rounded-xl bg-brand-gradient text-white shadow-glow">
          <Database size={18} />
        </div>
        <div>
          <p className="text-sm font-extrabold leading-tight text-slate-800">Lakehouse</p>
          <p className="text-xs leading-tight text-slate-400">Console</p>
        </div>
      </div>

      <nav className="flex flex-col gap-1">
        {NAV.map(({ key, label, icon: Icon }) => (
          <button
            key={key}
            onClick={() => onChange(key)}
            className={clsx(
              "flex items-center gap-3 rounded-xl px-3 py-2.5 text-left text-sm font-medium transition",
              active === key
                ? "bg-brand-gradient text-white shadow-glow"
                : "text-slate-600 hover:bg-lavender-50"
            )}
          >
            <Icon size={17} />
            {label}
          </button>
        ))}
      </nav>

      <div className="mt-auto rounded-xl bg-brand-gradient-soft p-3 text-xs text-lavender-700">
        Generic Medallion lakehouse: Bronze → Silver → Gold → OLAP, with full
        lineage back to the source file.
      </div>
    </aside>
  );
}
