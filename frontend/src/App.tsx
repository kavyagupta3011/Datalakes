import { useState } from "react";
import Sidebar, { ViewKey } from "./components/Sidebar";
import Dashboard from "./views/Dashboard";
import Upload from "./views/Upload";
import Explorer from "./views/Explorer";
import Cuboids from "./views/Cuboids";

const TITLES: Record<ViewKey, { title: string; subtitle: string }> = {
  dashboard: { title: "Dashboard", subtitle: "Live snapshot of the Gold layer and the last pipeline run." },
  upload: { title: "Upload data", subtitle: "Drop a file into Bronze and kick off the pipeline." },
  explorer: { title: "Table explorer", subtitle: "Browse every dimension, fact, and cuboid table." },
  cuboids: { title: "Cuboid viewer", subtitle: "Visualize OLAP cuboids and drill back to source rows." },
};

export default function App() {
  const [view, setView] = useState<ViewKey>("dashboard");
  const { title, subtitle } = TITLES[view];

  return (
    <div className="flex h-screen w-full overflow-hidden">
      <Sidebar active={view} onChange={setView} />
      <main className="flex-1 overflow-y-auto">
        <header className="border-b border-lavender-100 bg-white/60 px-8 py-6 backdrop-blur-sm">
          <h1 className="text-2xl font-extrabold text-slate-800">{title}</h1>
          <p className="muted mt-1">{subtitle}</p>
        </header>
        <div className="px-8 py-6">
          {view === "dashboard" && <Dashboard onNavigate={setView} />}
          {view === "upload" && <Upload />}
          {view === "explorer" && <Explorer />}
          {view === "cuboids" && <Cuboids />}
        </div>
      </main>
    </div>
  );
}
