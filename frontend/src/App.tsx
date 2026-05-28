import { useEffect, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api, Me, setAuthLostHandler } from "./api";
import { IngestPanel } from "./components/IngestPanel";
import { ReviewTable } from "./components/ReviewTable";
import { RowDetail } from "./components/RowDetail";
import { RunsList } from "./components/RunsList";
import { Summary } from "./components/Summary";
import { Login } from "./components/Login";
import { useTheme } from "./theme";

export default function App() {
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [toast, setToast] = useState<{ msg: string; kind?: "error" } | null>(null);
  const [theme, toggleTheme] = useTheme();
  const qc = useQueryClient();

  const { data: me, isLoading, refetch } = useQuery<Me | null>({
    queryKey: ["me"],
    queryFn: api.me,
    retry: false,
    refetchOnWindowFocus: false,
  });

  useEffect(() => {
    if (toast) {
      const t = setTimeout(() => setToast(null), 4000);
      return () => clearTimeout(t);
    }
  }, [toast]);

  // Agar koi bhi API call 401 deti hai mid-session, sab kuch invalidate karke
  // me query refetch karo — jisse user automatically login page pe redirect ho jaaye.
  // Yeh stale-session ke "logged in but nothing loads" wale bug ko fix karta hai.
  useEffect(() => {
    setAuthLostHandler(() => {
      qc.setQueryData(["me"], null);
      qc.invalidateQueries({ queryKey: ["me"] });
    });
  }, [qc]);

  function notify(msg: string, kind?: "error") {
    setToast({ msg, kind });
  }

  async function logout() {
    await api.logout();
    qc.clear();
    refetch();
  }

  if (isLoading) return <div className="empty">Loading…</div>;
  if (!me) return <Login onAuthed={() => refetch()} />;

  return (
    <div className="app">
      <header className="topbar">
        <h1>Breathe ESG — Ingest & Review</h1>
        <span className="muted">·</span>
        <span style={{ fontWeight: 500 }}>{me.tenant.name}</span>
        <span className="muted">({me.tenant.default_region})</span>
        <span className="spacer" />
        <button className="theme-toggle" onClick={toggleTheme} title="Toggle theme">
          {theme === "dark" ? "☀" : "☾"}
        </button>
        <span className="muted">{me.user.email}</span>
        <button className="btn" onClick={logout}>Sign out</button>
      </header>

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
