import type { LucideIcon } from "lucide-react";

export default function StatCard({
  label,
  value,
  icon: Icon,
  accent = "lavender",
}: {
  label: string;
  value: string | number;
  icon: LucideIcon;
  accent?: "lavender" | "skyblue";
}) {
  const ring = accent === "lavender" ? "bg-lavender-100 text-lavender-600" : "bg-skyblue-100 text-skyblue-600";
  return (
    <div className="card flex items-center gap-4 p-5">
      <div className={`flex h-11 w-11 shrink-0 items-center justify-center rounded-xl ${ring}`}>
        <Icon size={20} />
      </div>
      <div>
        <p className="muted">{label}</p>
        <p className="text-2xl font-extrabold text-slate-800">{value}</p>
      </div>
    </div>
  );
}
