import { useCallback, useEffect, useRef, useState, type DragEvent } from "react";
import { CheckCircle2, FileUp, PlayCircle, UploadCloud, XCircle } from "lucide-react";
import { api, ApiRequestError } from "../api";
import type { PipelineStatus } from "../types";
import StatusBadge from "../components/StatusBadge";
import Spinner from "../components/Spinner";
import clsx from "../lib/clsx";

const DOMAIN_OPTIONS = ["retail", "education", "support"];

export default function Upload() {
  const [domain, setDomain] = useState("retail");
  const [customDomain, setCustomDomain] = useState("");
  const [entity, setEntity] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [dragOver, setDragOver] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [uploadMsg, setUploadMsg] = useState<{ kind: "ok" | "err"; text: string } | null>(null);
  const [status, setStatus] = useState<PipelineStatus | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  const effectiveDomain = domain === "__custom__" ? customDomain.trim() : domain;

  async function refreshStatus() {
    try {
      setStatus(await api.pipelineStatus());
    } catch {
      // dashboard handles connectivity errors; stay quiet here
    }
  }

  useEffect(() => {
    refreshStatus();
    const interval = setInterval(() => {
      refreshStatus();
    }, 3000);
    return () => clearInterval(interval);
  }, []);

  const onDrop = useCallback((e: DragEvent) => {
    e.preventDefault();
    setDragOver(false);
    if (e.dataTransfer.files?.[0]) setFile(e.dataTransfer.files[0]);
  }, []);

  async function handleUpload() {
    if (!file || !effectiveDomain || !entity.trim()) {
      setUploadMsg({ kind: "err", text: "Pick a domain, entity name, and a file first." });
      return;
    }
    setUploading(true);
    setUploadMsg(null);
    try {
      const res = await api.upload(effectiveDomain, entity.trim(), file);
      setUploadMsg({ kind: "ok", text: `Saved to ${res.path}. Run the pipeline below to process it.` });
      setFile(null);
      if (inputRef.current) inputRef.current.value = "";
    } catch (e) {
      setUploadMsg({ kind: "err", text: e instanceof ApiRequestError ? e.message : "Upload failed." });
    } finally {
      setUploading(false);
    }
  }

  async function handleRunPipeline(fullReload: boolean) {
    try {
      await api.runPipeline(fullReload);
      refreshStatus();
    } catch (e) {
      setUploadMsg({ kind: "err", text: e instanceof ApiRequestError ? e.message : "Could not start the pipeline." });
    }
  }

  const isRunning = !!status?.running;

  return (
    <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
      <div className="card flex flex-col gap-5 p-6">
        <p className="section-title">1. Drop a file into Bronze</p>

        <div className="grid grid-cols-2 gap-3">
          <label className="flex flex-col gap-1">
            <span className="muted">Domain</span>
            <select className="input" value={domain} onChange={(e) => setDomain(e.target.value)}>
              {DOMAIN_OPTIONS.map((d) => (
                <option key={d} value={d}>
                  {d}
                </option>
              ))}
              <option value="__custom__">+ new domain</option>
            </select>
          </label>
          <label className="flex flex-col gap-1">
            <span className="muted">Entity name</span>
            <input
              className="input"
              placeholder="e.g. orders"
              value={entity}
              onChange={(e) => setEntity(e.target.value)}
            />
          </label>
        </div>

        {domain === "__custom__" && (
          <label className="flex flex-col gap-1">
            <span className="muted">New domain name</span>
            <input
              className="input"
              placeholder="e.g. logistics"
              value={customDomain}
              onChange={(e) => setCustomDomain(e.target.value)}
            />
          </label>
        )}

        <div
          onDragOver={(e) => {
            e.preventDefault();
            setDragOver(true);
          }}
          onDragLeave={() => setDragOver(false)}
          onDrop={onDrop}
          onClick={() => inputRef.current?.click()}
          className={clsx(
            "flex cursor-pointer flex-col items-center gap-2 rounded-xl border-2 border-dashed px-6 py-10 text-center transition",
            dragOver ? "border-lavender-400 bg-lavender-50" : "border-slate-200 hover:border-lavender-300"
          )}
        >
          <UploadCloud size={28} className="text-lavender-500" />
          {file ? (
            <p className="text-sm font-medium text-slate-700">{file.name}</p>
          ) : (
            <>
              <p className="text-sm font-medium text-slate-600">Drag & drop a file here, or click to browse</p>
              <p className="muted">CSV, Excel, JSON, XML, PNG/JPG, PDF</p>
            </>
          )}
          <input
            ref={inputRef}
            type="file"
            className="hidden"
            onChange={(e) => setFile(e.target.files?.[0] ?? null)}
          />
        </div>

        <button className="btn-primary" disabled={uploading || !file} onClick={handleUpload}>
          {uploading ? <Spinner label="Uploading..." /> : <><FileUp size={16} /> Upload to Bronze</>}
        </button>

        {uploadMsg && (
          <div
            className={clsx(
              "flex items-start gap-2 rounded-lg px-3 py-2 text-sm",
              uploadMsg.kind === "ok" ? "bg-emerald-50 text-emerald-700" : "bg-rose-50 text-rose-700"
            )}
          >
            {uploadMsg.kind === "ok" ? <CheckCircle2 size={16} className="mt-0.5 shrink-0" /> : <XCircle size={16} className="mt-0.5 shrink-0" />}
            <span>{uploadMsg.text}</span>
          </div>
        )}
      </div>

      <div className="card flex flex-col gap-5 p-6">
        <div className="flex items-center justify-between">
          <p className="section-title">2. Run the pipeline</p>
          {isRunning && <StatusBadge tone="running">running</StatusBadge>}
        </div>
        <p className="muted">
          Runs Bronze → Silver → Gold → OLAP via the orchestrator (retry + backoff per step). New rows are
          appended incrementally by default.
        </p>

        <div className="flex gap-3">
          <button className="btn-primary flex-1" disabled={isRunning} onClick={() => handleRunPipeline(false)}>
            <PlayCircle size={16} /> Run incremental
          </button>
          <button className="btn-secondary flex-1" disabled={isRunning} onClick={() => handleRunPipeline(true)}>
            Full reload
          </button>
        </div>

        {isRunning && (
          <div className="flex items-center justify-center rounded-xl bg-lavender-50 p-6">
            <Spinner label="Pipeline running - this page polls automatically..." />
          </div>
        )}

        {!isRunning && status?.steps && Object.keys(status.steps).length > 0 && (
          <ul className="flex flex-col gap-2">
            {Object.entries(status.steps).map(([step, result]) => (
              <li key={step} className="flex items-center justify-between rounded-lg bg-slate-50 px-3 py-2 text-sm">
                <span className="font-medium text-slate-700">{step}</span>
                <StatusBadge tone={result.status === "success" ? "success" : "failed"}>{result.status}</StatusBadge>
              </li>
            ))}
          </ul>
        )}

        {!isRunning && status?.last_result && status.last_result.returncode !== 0 && (
          <pre className="max-h-40 overflow-auto rounded-lg bg-slate-900 p-3 text-xs text-rose-300">
            {status.last_result.stderr_tail || status.last_result.stdout_tail}
          </pre>
        )}
      </div>
    </div>
  );
}
