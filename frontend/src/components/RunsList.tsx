import { useQuery } from "@tanstack/react-query";
import { api, Run } from "../api";

export function RunsList() {
  const { data } = useQuery({
    queryKey: ["runs"],
    queryFn: api.runs,
    refetchInterval: 5_000,
    select: (r: any) => (r.results ?? r) as Run[],
  });
  if (!data) return null;
  return (
    <div className="section">
      <h2>Recent runs</h2>
      {data.length === 0 && <div className="muted">No runs yet.</div>}
      {data.slice(0, 10).map(r => (
        <div key={r.id} className={`run-card ${r.status}`}>
          <div><strong>{r.source_name}</strong></div>
          <div className="run-meta">{r.file_name || "(no file name)"}</div>
          <div className="run-meta">
            {new Date(r.started_at).toLocaleString()} · {r.status}
          </div>
          <div className="run-meta">
            {r.row_count_normalized} ok · {r.row_count_failed} failed
          </div>
          {r.error_log && r.error_log.length > 0 && (
            <details style={{ marginTop: 6 }}>
              <summary style={{ fontSize: 11, color: "#92400e", cursor: "pointer" }}>
                {r.error_log.length} parse error(s)
              </summary>
              <ul style={{ fontSize: 11, paddingLeft: 16, margin: "6px 0" }}>
                {r.error_log.slice(0, 8).map((e, i) => (
                  <li key={i}><code>{e.row_ref}</code>: {e.message}</li>
                ))}
              </ul>
            </details>
          )}
        </div>
      ))}
    </div>
  );
}
