import { Loader2 } from "lucide-react";

export default function Spinner({ label }: { label?: string }) {
  return (
    <div className="flex items-center gap-2 text-sm text-slate-500">
      <Loader2 size={16} className="animate-spin text-lavender-500" />
      {label ?? "Loading..."}
    </div>
  );
}
