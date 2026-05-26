import { useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { api, Activity } from "../api";

type Notify = (msg: string, kind?: "error") => void;

export function RowDetail({ id, onClose, notify }: { id: string; onClose: () => void; notify: Notify }) {
  const qc = useQueryClient();

  const { data, refetch } = useQuery({
    queryKey: ["activity", id],
    queryFn: async () => {
      const list = await api.activities();
      const all = (list.results ?? list) as Activity[];
      return all.find(a => a.id === id);
    },
  });
  const { data: audit } = useQuery({
    queryKey: ["audit", id],
    queryFn: () => api.audit(id),
  });

  const [notes, setNotes] = useState<string>("");
  const [editing, setEditing] = useState(false);

  const approve = useMutation({
    mutationFn: () => api.approve(id),
    onSuccess: () => { notify("Approved"); invalidate(); },
    onError: (e: any) => notify(e.message, "error"),
  });
  const lock = useMutation({
    mutationFn: () => api.lock(id),
    onSuccess: () => { notify("Locked for audit"); invalidate(); },
    onError: (e: any) => notify(e.message, "error"),
  });
  const update = useMutation({
    mutationFn: (patch: Record<string, unknown>) => api.updateActivity(id, patch),
    onSuccess: () => { notify("Updated"); setEditing(false); invalidate(); },
    onError: (e: any) => notify(e.message, "error"),
  });

  function invalidate() {
    qc.invalidateQueries({ queryKey: ["activities"] });
    qc.invalidateQueries({ queryKey: ["activity", id] });
    qc.invalidateQueries({ queryKey: ["audit", id] });
    qc.invalidateQueries({ queryKey: ["summary"] });
    refetch();
  }

  if (!data) return <div className="detail-pane"><span className="close" onClick={onClose}>×</span>Loading…</div>;

  const canApprove = data.status === "pending" && !data.flags.some(f => f.severity === "error" && !f.dismissed_at);
  const canLock = data.status === "approved";
  const isLocked = data.status === "locked";

  return (
    <div className="detail-pane">
      <span className="close" onClick={onClose}>×</span>
      <h3>{data.category_label}</h3>
      <div className="muted" style={{ fontSize: 12 }}>{data.id}</div>

      <div style={{ marginTop: 12, display: "flex", gap: 8, flexWrap: "wrap" }}>
        <span className={`badge ${data.status}`}>{data.status}</span>
        <span className={`badge scope-${data.scope}`}>Scope {data.scope}</span>
      </div>

      <dl>
        <dt>Source</dt><dd>{data.source_name} <span className="muted">({data.source_kind})</span></dd>
        <dt>Facility</dt><dd>{data.facility_name || <span className="muted">unmapped</span>}</dd>
        <dt>Date</dt><dd>{data.activity_date}</dd>
        {data.period_start && <><dt>Period</dt><dd>{data.period_start} → {data.period_end}</dd></>}
        <dt>Original</dt><dd>{data.quantity_original} {data.unit_original}</dd>
        <dt>Normalized</dt><dd>{data.quantity_normalized || "—"} {data.unit_normalized || ""}</dd>
        <dt>Factor</dt><dd>{data.factor_source_snapshot || <span className="muted">not yet resolved</span>}</dd>
        <dt>Emissions</dt><dd><strong>{data.emissions_kgco2e ? `${data.emissions_kgco2e} kg CO₂e` : "—"}</strong></dd>
        <dt>Notes</dt><dd>{data.notes || <span className="muted">—</span>}</dd>
      </dl>

      {data.flags.length > 0 && (
        <div style={{ marginTop: 16 }}>
          <h4 style={{ margin: "0 0 6px", fontSize: 13 }}>Flags</h4>
          {data.flags.map(f => (
            <FlagRow key={f.id} flag={f} onDismissed={invalidate} notify={notify} />
          ))}
        </div>
      )}

      <div style={{ marginTop: 16, display: "flex", gap: 8, flexWrap: "wrap" }}>
        {canApprove && (
          <button className="btn primary" disabled={approve.isPending} onClick={() => approve.mutate()}>
            Approve
          </button>
        )}
        {canLock && (
          <button className="btn primary" disabled={lock.isPending} onClick={() => lock.mutate()}>
            Lock for audit
          </button>
        )}
        {!isLocked && (
          <button className="btn" onClick={() => { setEditing(!editing); setNotes(data.notes); }}>
            {editing ? "Cancel" : "Edit notes"}
          </button>
        )}
      </div>

      {editing && (
        <div style={{ marginTop: 12 }}>
          <textarea
            value={notes}
            onChange={e => setNotes(e.target.value)}
            rows={3}
            style={{ width: "100%", fontSize: 13, padding: 6 }}
          />
          <button className="btn primary" style={{ marginTop: 6 }} onClick={() => update.mutate({ notes })}>
            Save
          </button>
        </div>
      )}

      {data.raw_record && (
        <div style={{ marginTop: 24 }}>
          <h4 style={{ margin: "0 0 6px", fontSize: 13 }}>What we received <span className="muted">({data.raw_record.source_row_ref})</span></h4>
          <pre>{JSON.stringify(data.raw_record.payload, null, 2)}</pre>
        </div>
      )}

      <div style={{ marginTop: 24 }}>
        <h4 style={{ margin: "0 0 6px", fontSize: 13 }}>Audit trail</h4>
        {audit && audit.length > 0 ? (
          <ul style={{ paddingLeft: 16, fontSize: 12, margin: 0 }}>
            {audit.map((e: any) => (
              <li key={e.id}>
                <strong>{e.action}</strong> by {e.actor_email || "system"} · {new Date(e.ts).toLocaleString()}
                {e.reason && <div className="muted">{e.reason}</div>}
              </li>
            ))}
          </ul>
        ) : (
          <div className="muted">No history yet.</div>
        )}
      </div>
    </div>
  );
}

function FlagRow({ flag, onDismissed, notify }: { flag: any; onDismissed: () => void; notify: Notify }) {
  const [open, setOpen] = useState(false);
  const [reason, setReason] = useState("");
  const dismiss = useMutation({
    mutationFn: () => api.dismissFlag(flag.id, reason),
    onSuccess: () => { notify("Flag dismissed"); onDismissed(); },
    onError: (e: any) => notify(e.message, "error"),
  });
  return (
    <div style={{ marginBottom: 6, padding: 6, background: "#f9fafb", borderRadius: 4 }}>
      <div>
        <span className={`flag-pill ${flag.severity}`}>{flag.code}</span>
        <span style={{ fontSize: 12 }}> {flag.message}</span>
      </div>
      {flag.dismissed_at ? (
        <div className="muted" style={{ fontSize: 11, marginTop: 4 }}>
          Dismissed: {flag.dismissal_reason}
        </div>
      ) : (
        <>
          {!open && (
            <button className="btn" style={{ fontSize: 11, padding: "2px 6px", marginTop: 4 }} onClick={() => setOpen(true)}>
              Dismiss
            </button>
          )}
          {open && (
            <div style={{ marginTop: 4 }}>
              <input
                placeholder="Reason (required)"
                value={reason}
                onChange={e => setReason(e.target.value)}
                style={{ width: "100%", fontSize: 12, padding: 4 }}
              />
              <button className="btn" style={{ fontSize: 11, padding: "2px 6px", marginTop: 4 }} disabled={!reason.trim()} onClick={() => dismiss.mutate()}>
                Confirm
              </button>
            </div>
          )}
        </>
      )}
    </div>
  );
}
