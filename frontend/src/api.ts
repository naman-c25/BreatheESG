const BASE = (import.meta.env.VITE_API_BASE as string) || "";

function tenantId(): string {
  return localStorage.getItem("tenantId") || "";
}

async function req(path: string, init: RequestInit = {}) {
  const headers = new Headers(init.headers || {});
  if (!headers.has("Content-Type") && !(init.body instanceof FormData)) {
    headers.set("Content-Type", "application/json");
  }
  const tid = tenantId();
  if (tid) headers.set("X-Tenant-Id", tid);
  const res = await fetch(`${BASE}${path}`, { ...init, headers });
  if (!res.ok) {
    const text = await res.text();
    throw new Error(`${res.status} ${text}`);
  }
  if (res.status === 204) return null;
  return res.json();
}

export const api = {
  tenants: () => req("/api/tenants/"),
  sources: () => req("/api/sources/"),
  facilities: () => req("/api/facilities/"),
  categories: () => req("/api/categories/"),
  runs: () => req("/api/runs/"),
  activities: (params: Record<string, string> = {}) => {
    const q = new URLSearchParams(params).toString();
    return req(`/api/activities/${q ? `?${q}` : ""}`);
  },
  summary: () => req("/api/dashboard/summary/"),
  audit: (entityId?: string) => req(`/api/audit/${entityId ? `?entity_id=${entityId}` : ""}`),

  ingest: (sourceId: string, file: File) => {
    const fd = new FormData();
    fd.append("source", sourceId);
    fd.append("file", file);
    return req("/api/ingest/", { method: "POST", body: fd });
  },

  updateActivity: (id: string, patch: Record<string, unknown>) =>
    req(`/api/activities/${id}/`, { method: "PATCH", body: JSON.stringify(patch) }),

  approve: (id: string, reason = "") =>
    req(`/api/activities/${id}/approve/`, { method: "POST", body: JSON.stringify({ reason }) }),

  lock: (id: string, reason = "") =>
    req(`/api/activities/${id}/lock/`, { method: "POST", body: JSON.stringify({ reason }) }),

  bulkApprove: (ids: string[]) =>
    req(`/api/activities/bulk_approve/`, { method: "POST", body: JSON.stringify({ ids }) }),

  dismissFlag: (id: string, reason: string) =>
    req(`/api/flags/${id}/dismiss/`, { method: "POST", body: JSON.stringify({ reason }) }),
};

export type Activity = {
  id: string;
  status: "pending" | "flagged" | "approved" | "locked" | "superseded";
  scope: number;
  source_name: string;
  source_kind: string;
  facility_name: string | null;
  category_label: string;
  activity_date: string;
  period_start: string | null;
  period_end: string | null;
  quantity_original: string;
  unit_original: string;
  quantity_normalized: string | null;
  unit_normalized: string;
  emissions_kgco2e: string | null;
  factor_source_snapshot: string;
  notes: string;
  flags: Array<{
    id: string;
    code: string;
    severity: "info" | "warn" | "error";
    message: string;
    dismissed_at: string | null;
  }>;
  raw_record: { id: string; source_row_ref: string; payload: any; received_at: string } | null;
};

export type Tenant = { id: string; name: string; default_region: string; default_currency: string };
export type Source = { id: string; name: string; kind: string };
export type Run = {
  id: string; source: string; source_name: string; file_name: string;
  status: string; started_at: string; finished_at: string | null;
  row_count_received: number; row_count_normalized: number; row_count_failed: number;
  error_log: Array<{ row_ref: string; message: string }>;
};

export { tenantId };
