export type TableGroups = {
  dimensions: string[];
  facts: string[];
  cuboids: string[];
};

export type TableRowsResponse = {
  table: string;
  total_rows: number;
  limit: number;
  offset: number;
  rows: Record<string, unknown>[];
};

export type DrillResponse = {
  fact_table: string;
  filters: Record<string, string | number>;
  row_count: number;
  rows: Record<string, unknown>[];
};

export type UploadResponse = {
  status: string;
  domain: string;
  entity: string;
  path: string;
};

export type PipelineStepResult = {
  status: "success" | "failed";
  attempts: number;
  started_at: string;
  finished_at: string;
  last_error: string | null;
};

export type PipelineStatus = {
  running: boolean;
  started_at: string | null;
  finished_at: string | null;
  last_result: { returncode: number; stdout_tail: string; stderr_tail: string } | null;
  steps: Record<string, PipelineStepResult>;
  updated_at: string | null;
};

export type HealthResponse = {
  status: string;
  table_count: number;
};

export type ApiError = {
  detail: string;
};
