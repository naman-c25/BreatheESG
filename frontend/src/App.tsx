import { useEffect, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api, Tenant } from "./api";
import { IngestPanel } from "./components/IngestPanel";
import { ReviewTable } from "./components/ReviewTable";
import { RowDetail } from "./components/RowDetail";
import { RunsList } from "./components/RunsList";
import { Summary } from "./components/Summary";

export default function App() {
  const [tenantId, setTenantId] = useState<string>(localStorage.getItem("tenantId") || "");
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [toast, setToast] = useState<{ msg: string; kind?: "error" } | null>(null);

  const { data: tenants } = useQuery<Tenant[]>({ queryKey: ["tenants"], queryFn: api.tenants });

  useEffect(() => {
    if (!tenantId && tenants && tenants.length > 0) {
      pickTenant(tenants[0].id);
    }
  }, [tenants]);

  function pickTenant(id: string) {
    setTenantId(id);
    localStorage.setItem("tenantId", id);
    setSelectedId(null);
    // Force-refetch everything tenant-scoped by reloading
    setTimeout(() => window.location.reload(), 50);
  }

  function notify(msg: string, kind?: "error") {
    setToast({ msg, kind });
    setTimeout(() => setToast(null), 4000);
  }

  if (!tenants) return <div className="empty">Loading…</div>;
  if (tenants.length === 0)
    return <div className="empty">No tenants. Run <code>python manage.py seed</code> on the backend.</div>;

  return (
    <div className="app">
      <header className="topbar">
        <h1>Breathe ESG — Ingest & Review</h1>
        <span className="muted">·</span>
        <label>
          Tenant:&nbsp;
          <select value={tenantId} onChange={e => pickTenant(e.target.value)}>
            {tenants.map(t => (
              <option key={t.id} value={t.id}>{t.name} ({t.default_region})</option>
            ))}
          </select>
        </label>
        <span className="spacer" />
        <span className="muted">analyst@acme.example</span>
      </header>

      {tenantId && (
        <div className="layout">
          <aside className="sidebar">
            <IngestPanel onResult={(r) => notify(`Ingested: ${r.row_count_normalized} rows OK, ${r.row_count_failed} failed`)} />
            <RunsList />
          </aside>
          <main className="main">
            <Summary />
            <ReviewTable
              onSelect={setSelectedId}
              selectedId={selectedId}
              notify={notify}
            />
          </main>
        </div>
      )}

      {selectedId && (
        <RowDetail
          id={selectedId}
          onClose={() => setSelectedId(null)}
          notify={notify}
        />
      )}

      {toast && <div className={`toast ${toast.kind || ""}`}>{toast.msg}</div>}
    </div>
  );
}
