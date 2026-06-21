import type { ReactNode } from "react";
import clsx from "../lib/clsx";

type Tone = "success" | "failed" | "running" | "idle";

const STYLES: Record<Tone, string> = {
  success: "bg-emerald-100 text-emerald-700",
  failed: "bg-rose-100 text-rose-700",
  running: "bg-skyblue-100 text-skyblue-700",
  idle: "bg-slate-100 text-slate-500",
};

export default function StatusBadge({ tone, children }: { tone: Tone; children: ReactNode }) {
  return (
    <span className={clsx("rounded-full px-2.5 py-1 text-xs font-semibold", STYLES[tone])}>
      {children}
    </span>
  );
}
