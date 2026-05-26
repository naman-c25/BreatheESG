import { useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api, Source, Run } from "../api";

export function IngestPanel({ onResult }: { onResult: (r: Run) => void }) {
  const { data: sources } = useQuery<Source[]>({
    queryKey: ["sources"],
    queryFn: async () => (await api.sources()).results || (await api.sources()),
    select: (r: any) => r.results ?? r,
  });
  const qc = useQueryClient();
  const [busyId, setBusyId] = useState<string | null>(null);

  async function upload(source: Source, file: File) {
    setBusyId(source.id);
    try {
      const run = await api.ingest(source.id, file);
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
        <SourceUpload key={s.id} source={s} busy={busyId === s.id} onUpload={upload} />
      ))}
      {!sources?.length && <div className="muted">No sources configured.</div>}
    </div>
  );
}

function SourceUpload({ source, busy, onUpload }: {
  source: Source; busy: boolean;
  onUpload: (s: Source, f: File) => void;
}) {
  const ref = useRef<HTMLInputElement>(null);
  const accept = {
    sap_flatfile: ".csv,.txt",
    utility_pdf: ".pdf",
    travel_api: ".json",
  }[source.kind] || "";
  return (
    <div className="upload-row">
      <div className="source-name">
        {source.name}
        <div className="muted" style={{ fontSize: 11 }}>{source.kind}</div>
      </div>
      <input
        type="file"
        ref={ref}
        accept={accept}
        style={{ display: "none" }}
        onChange={e => {
          const f = e.target.files?.[0];
          if (f) onUpload(source, f);
          if (ref.current) ref.current.value = "";
        }}
      />
      <button className="btn primary" disabled={busy} onClick={() => ref.current?.click()}>
        {busy ? "Uploading…" : "Upload"}
      </button>
    </div>
  );
}
