// Single API client. credentials: 'include' har request mein bhejna mandatory
// hai warna session cookie nahi jaayegi. Same-origin pe dev/prod dono kaam karta hai.
const BASE = (import.meta.env.VITE_API_BASE as string) || "";

// Callback set by App.tsx — agar koi bhi request 401 deti hai, app ko bolte hain
// "me query invalidate kar do" taaki user automatically login page pe redirect ho.
// Bina iske session expire hone par UI stuck ho jaata tha "logged in but nothing loads."
let onAuthLost: (() => void) | null = null;
export function setAuthLostHandler(fn: () => void) { onAuthLost = fn; }

async function req(path: string, init: RequestInit = {}) {
  const headers = new Headers(init.headers || {});
  if (!headers.has("Content-Type") && !(init.body instanceof FormData)) {
    headers.set("Content-Type", "application/json");
  }
  const res = await fetch(`${BASE}${path}`, {
    ...init,
    headers,
    credentials: "include",
  });
  if (res.status === 401 && !path.startsWith("/api/auth/")) {
    // Session expired ya invalid ho gaya. App ko notify karo — woh login
    // page render kar dega. Throw kar dete hain taaki caller mutation
    // ka error handler bhi fire ho.
    onAuthLost?.();
    throw new Error("Session expired — please sign in again");
  }
  if (!res.ok) {
    const text = await res.text();
    const err = new Error(`${res.status} ${text}`) as Error & { status?: number };
    err.status = res.status;
    throw err;
  }
  if (res.status === 204) return null;
  return res.json();
}

export const api = {
  // auth
  login: (email: string, password: string) =>
    req("/api/auth/login/", { method: "POST", body: JSON.stringify({ email, password }) }),
  logout: () => req("/api/auth/logout/", { method: "POST" }),
  // me() ek special case hai — 401 = "logged out", error nahi. Null return
  // karte hain taaki App.tsx login page dikha sake. React Query 401 pe retry
  // bhi nahi karega, kyunki throw nahi ho raha.
  me: async () => {
    const res = await fetch(`${BASE}/api/auth/me/`, { credentials: "include" });
    if (res.status === 401) return null;
    if (!res.ok) throw new Error(`${res.status} ${await res.text()}`);
    return res.json();
  },

  // data
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

  reject: (id: string, reason: string) =>
    req(`/api/activities/${id}/reject/`, { method: "POST", body: JSON.stringify({ reason }) }),

  pull: (sourceId: string) => {
    const fd = new FormData();
    fd.append("source", sourceId);
    fd.append("mode", "pull");
    return req("/api/ingest/", { method: "POST", body: fd });
  },

  paste: (sourceId: string, content: string, file_name: string) => {
    const fd = new FormData();
    fd.append("source", sourceId);
    fd.append("mode", "paste");
    fd.append("content", content);
    fd.append("file_name", file_name);
    return req("/api/ingest/", { method: "POST", body: fd });
  },

  bulkApprove: (ids: string[]) =>
    req(`/api/activities/bulk_approve/`, { method: "POST", body: JSON.stringify({ ids }) }),

  dismissFlag: (id: string, reason: string) =>
    req(`/api/flags/${id}/dismiss/`, { method: "POST", body: JSON.stringify({ reason }) }),
};

export type Tenant = { id: string; name: string; default_region: string; default_currency: string };
export type AuthUser = { id: string; email: string; display_name: string };
export type Me = { user: AuthUser; tenant: Tenant };

export type Activity = {
  id: string;
  status: "pending" | "flagged" | "approved" | "rejected" | "locked" | "superseded";
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
    dismissal_reason?: string;
  }>;
  raw_record: { id: string; source_row_ref: string; payload: any; received_at: string } | null;
};

export type Source = { id: string; name: string; kind: string };
export type Run = {
  id: string; source: string; source_name: string; file_name: string;
  status: string; started_at: string; finished_at: string | null;
  row_count_received: number; row_count_normalized: number; row_count_failed: number;
  error_log: Array<{ row_ref: string; message: string }>;
};
export type AuditEntry = {
  id: string; actor_email: string | null; entity_type: string; entity_id: string;
  action: string; before: Record<string, unknown>; after: Record<string, unknown>;
  reason: string; ts: string;
};
