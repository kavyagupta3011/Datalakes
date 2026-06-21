import type {
  DrillResponse,
  HealthResponse,
  PipelineStatus,
  TableGroups,
  TableRowsResponse,
  UploadResponse,
} from "./types";

// In docker-compose, nginx proxies /api/* to the api service, so the default
// (empty -> relative "/api") just works there. For local `npm run dev`
// against a bare `python src/api.py`, set VITE_API_BASE_URL in .env.
const RAW_BASE = import.meta.env.VITE_API_BASE_URL as string | undefined;
const API_BASE = RAW_BASE && RAW_BASE.length > 0 ? RAW_BASE : "/api";

class ApiRequestError extends Error {
  status: number;
  constructor(message: string, status: number) {
    super(message);
    this.status = status;
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, init);
  if (!res.ok) {
    let detail = `${res.status} ${res.statusText}`;
    try {
      const body = await res.json();
      if (body?.detail) detail = body.detail;
    } catch {
      // ignore non-JSON error bodies
    }
    throw new ApiRequestError(detail, res.status);
  }
  return res.json() as Promise<T>;
}

export const api = {
  health: () => request<HealthResponse>("/health"),

  tables: () => request<TableGroups>("/tables"),

  tableRows: (name: string, limit = 50, offset = 0) =>
    request<TableRowsResponse>(`/tables/${encodeURIComponent(name)}?limit=${limit}&offset=${offset}`),

  drill: (factTable: string, filters: Record<string, string | number>, limit = 200) => {
    const params = new URLSearchParams();
    Object.entries(filters).forEach(([k, v]) => params.set(k, String(v)));
    params.set("limit", String(limit));
    return request<DrillResponse>(`/drill/${encodeURIComponent(factTable)}?${params.toString()}`);
  },

  upload: (domain: string, entity: string, file: File) => {
    const form = new FormData();
    form.append("domain", domain);
    form.append("entity", entity);
    form.append("file", file);
    return request<UploadResponse>("/upload", { method: "POST", body: form });
  },

  runPipeline: (fullReload: boolean) =>
    request<{ status: string; full_reload: boolean }>(`/pipeline/run?full_reload=${fullReload}`, {
      method: "POST",
    }),

  pipelineStatus: () => request<PipelineStatus>("/pipeline/status"),
};

export { ApiRequestError };
