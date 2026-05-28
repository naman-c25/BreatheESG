import { useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api, Source, Run } from "../api";

type Mode = "upload" | "paste" | "pull";

export function IngestPanel({ onResult }: { onResult: (r: Run) => void }) {
  const { data: sources } = useQuery<Source[]>({
    queryKey: ["sources"],
    queryFn: async () => (await api.sources()).results || (await api.sources()),
    select: (r: any) => r.results ?? r,
  });
  const qc = useQueryClient();
  const [busyId, setBusyId] = useState<string | null>(null);

  async function run(p: () => Promise<Run>, sourceId: string) {
    setBusyId(sourceId);
    try {
      const run = await p();
      onResult(run);
      qc.invalidateQueries({ queryKey: ["activities"] });
      qc.invalidateQueries({ queryKey: ["runs"] });
      qc.invalidateQueries({ queryKey: ["summary"] });
    } catch (e: any) {
      onResult({ row_count_normalized: 0, row_count_failed: 1, error_log: [{ row_ref: "request", message: e.message }] } as any);
    } finally {
      setBusyId(null);
    }
  }

  return (
    <div className="section">
      <h2>Ingest</h2>
      {(sources || []).map(s => (
        <SourceCard
          key={s.id}
          source={s}
          busy={busyId === s.id}
          onUpload={(f) => run(() => api.ingest(s.id, f), s.id)}
          onPull={() => run(() => api.pull(s.id), s.id)}
          onPaste={(content, name) => run(() => api.paste(s.id, content, name), s.id)}
        />
      ))}
      {!sources?.length && <div className="muted">No sources configured.</div>}
    </div>
  );
}

function SourceCard({ source, busy, onUpload, onPull, onPaste }: {
  source: Source; busy: boolean;
  onUpload: (f: File) => void;
  onPull: () => void;
  onPaste: (content: string, file_name: string) => void;
}) {
  const ref = useRef<HTMLInputElement>(null);
  const [mode, setMode] = useState<Mode>("upload");
  const [pasted, setPasted] = useState("");
  const accept = {
    sap_flatfile: ".csv,.txt,.xlsx",
    utility_pdf: ".pdf",
    travel_api: ".json",
  }[source.kind] || "";
  // Pull aur paste sirf travel ke liye dikhao — JSON-shaped data ke liye sense banta hai.
  // SAP CSV ya Utility PDF ko paste karna technically possible hai but UX bekaar hai
  // (multi-KB content textarea mein paste karna kisko karna hai).
  const showPull = source.kind === "travel_api";
  const showPaste = source.kind === "travel_api";

  return (
    <div className="upload-row" style={{ flexDirection: "column", alignItems: "stretch", gap: 6 }}>
      <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
        <div className="source-name">
          {source.name}
          <div className="muted" style={{ fontSize: 11 }}>{source.kind}</div>
        </div>
        {(showPull || showPaste) && (
          <select value={mode} onChange={e => setMode(e.target.value as Mode)}
                  style={{ fontSize: 11, padding: "2px 4px", background: "var(--panel)", color: "var(--fg)", border: "1px solid var(--border)", borderRadius: 4 }}>
            <option value="upload">Upload file</option>
            {showPull && <option value="pull">Pull from API</option>}
            {showPaste && <option value="paste">Paste content</option>}
          </select>
        )}
      </div>

      {mode === "upload" && (
        <div style={{ display: "flex", gap: 8 }}>
          <input type="file" ref={ref} accept={accept} style={{ display: "none" }}
                 onChange={e => { const f = e.target.files?.[0]; if (f) onUpload(f); if (ref.current) ref.current.value = ""; }} />
          <button className="btn primary" disabled={busy} onClick={() => ref.current?.click()} style={{ flex: 1 }}>
            {busy ? "Working…" : "Choose file"}
          </button>
        </div>
      )}

      {mode === "pull" && (
        <div>
          <div className="muted" style={{ fontSize: 11, marginBottom: 4 }}>
            Demo: pulls from the configured fixture. In production this is a real API call (Concur OAuth, etc.).
          </div>
          <button className="btn primary" disabled={busy} onClick={onPull} style={{ width: "100%" }}>
            {busy ? "Pulling…" : "Pull now"}
          </button>
        </div>
      )}

      {mode === "paste" && (
        <div>
          <textarea
            value={pasted} onChange={e => setPasted(e.target.value)}
            placeholder='{"bookings":[...]}'
            rows={4}
            style={{ width: "100%", fontSize: 11, fontFamily: "ui-monospace, Consolas, monospace",
                     padding: 6, background: "var(--panel)", color: "var(--fg)",
                     border: "1px solid var(--border)", borderRadius: 4, resize: "vertical" }}
          />
          <button className="btn primary" disabled={busy || !pasted.trim()}
                  onClick={() => { onPaste(pasted, "pasted.json"); setPasted(""); }}
                  style={{ width: "100%", marginTop: 4 }}>
            {busy ? "Ingesting…" : "Ingest pasted content"}
          </button>
        </div>
      )}
    </div>
  );
}
