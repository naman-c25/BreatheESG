import { useMemo, useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { AnimatePresence, motion } from "framer-motion";
import { api, Activity, Source } from "../api";

type Notify = (msg: string, kind?: "error") => void;

export function ReviewTable({
  onSelect, selectedId, notify,
}: { onSelect: (id: string) => void; selectedId: string | null; notify: Notify }) {
  const [status, setStatus] = useState<string>("pending,flagged");
  const [scope, setScope] = useState<string>("");
  const [sourceId, setSourceId] = useState<string>("");
  const [q, setQ] = useState<string>("");
  const [selected, setSelected] = useState<Set<string>>(new Set());

  const params = useMemo(() => {
    const p: Record<string, string> = {};
    if (status) p.status = status;
    if (scope) p.scope = scope;
    if (sourceId) p.source = sourceId;
    if (q) p.q = q;
    return p;
  }, [status, scope, sourceId, q]);

  const qc = useQueryClient();
  const { data, isLoading } = useQuery({
    queryKey: ["activities", params],
    queryFn: () => api.activities(params),
    select: (r: any) => (r.results ?? r) as Activity[],
  });
  const { data: sourcesData } = useQuery({
    queryKey: ["sources"],
    queryFn: api.sources,
    select: (r: any) => (r.results ?? r) as Source[],
  });

  const bulkApprove = useMutation({
    mutationFn: () => api.bulkApprove([...selected]),
    onSuccess: (r: any) => {
      notify(`Approved ${r.approved.length}; ${r.errors.length} failed`);
      setSelected(new Set());
      qc.invalidateQueries({ queryKey: ["activities"] });
      qc.invalidateQueries({ queryKey: ["summary"] });
    },
    onError: (e: any) => notify(e.message, "error"),
  });

  function toggle(id: string) {
    const next = new Set(selected);
    if (next.has(id)) next.delete(id); else next.add(id);
    setSelected(next);
  }
  function toggleAll() {
    if (!data) return;
    if (selected.size === data.length) setSelected(new Set());
    else setSelected(new Set(data.map(a => a.id)));
  }

  return (
    <div>
      <div className="filters">
        <select value={status} onChange={e => setStatus(e.target.value)}>
          <option value="">All statuses</option>
          <option value="pending,flagged">Needs review</option>
          <option value="pending">Pending</option>
          <option value="flagged">Flagged</option>
          <option value="approved">Approved</option>
          <option value="locked">Locked</option>
        </select>
        <select value={scope} onChange={e => setScope(e.target.value)}>
          <option value="">All scopes</option>
          <option value="1">Scope 1</option>
          <option value="2">Scope 2</option>
          <option value="3">Scope 3</option>
        </select>
        <select value={sourceId} onChange={e => setSourceId(e.target.value)}>
          <option value="">All sources</option>
          {(sourcesData || []).map(s => (
            <option key={s.id} value={s.id}>{s.name}</option>
          ))}
        </select>
        <input placeholder="Search notes…" value={q} onChange={e => setQ(e.target.value)} />
      </div>

      <div className="actions">
        <button className="btn primary" disabled={selected.size === 0 || bulkApprove.isPending}
                onClick={() => bulkApprove.mutate()}>
          Approve selected ({selected.size})
        </button>
        <span className="muted">{data?.length ?? 0} rows</span>
      </div>

      <div className="table-wrap">
        <table>
          <thead>
            <tr>
              <th style={{ width: 24 }}>
                <input
                  type="checkbox"
                  onChange={toggleAll}
                  checked={!!data && data.length > 0 && selected.size === data.length}
                />
              </th>
              <th>Status</th>
              <th>Scope</th>
              <th>Category</th>
              <th>Facility</th>
              <th>Source</th>
              <th>Date</th>
              <th className="num">Quantity</th>
              <th>Unit</th>
              <th className="num">kg CO₂e</th>
              <th>Flags</th>
            </tr>
          </thead>
          <tbody>
            {isLoading && <tr><td colSpan={11} className="empty">Loading…</td></tr>}
            {!isLoading && data?.length === 0 && (
              <tr><td colSpan={11} className="empty">Nothing matches these filters.</td></tr>
            )}
            <AnimatePresence initial={false}>
              {data?.map((a, idx) => (
                <motion.tr
                  key={a.id}
                  className={selectedId === a.id ? "selected" : ""}
                  onClick={() => onSelect(a.id)}
                  initial={{ opacity: 0, y: -4 }}
                  animate={{ opacity: 1, y: 0 }}
                  exit={{ opacity: 0 }}
                  transition={{ duration: 0.18, delay: Math.min(idx, 12) * 0.012, ease: "easeOut" }}
                >
                  <td onClick={e => e.stopPropagation()}>
                    <input type="checkbox" checked={selected.has(a.id)} onChange={() => toggle(a.id)} />
                  </td>
                  <td><span className={`badge ${a.status}`}>{a.status}</span></td>
                  <td><span className={`badge scope-${a.scope}`}>S{a.scope}</span></td>
                  <td>{a.category_label}</td>
                  <td>{a.facility_name || <span className="muted">—</span>}</td>
                  <td>{a.source_name}</td>
                  <td>{a.activity_date}</td>
                  <td className="num">{fmt(a.quantity_original)}</td>
                  <td>
                    {a.unit_normalized || <span className="muted">{a.unit_original}</span>}
                    {a.unit_normalized && a.unit_original.toLowerCase() !== a.unit_normalized.toLowerCase() && (
                      <span className="muted" style={{ fontSize: 10 }}> (was {a.unit_original})</span>
                    )}
                  </td>
                  <td className="num">{a.emissions_kgco2e ? fmt(a.emissions_kgco2e) : <span className="muted">—</span>}</td>
                  <td>
                    {a.flags.filter(f => !f.dismissed_at).map(f => (
                      <span key={f.id} className={`flag-pill ${f.severity}`} title={f.message}>
                        {f.code}
                      </span>
                    ))}
                    {a.flags.filter(f => f.dismissed_at).map(f => (
                      <span key={f.id} className="flag-pill dismissed" title={`Dismissed: ${f.message}`}>
                        {f.code}
                      </span>
                    ))}
                  </td>
                </motion.tr>
              ))}
            </AnimatePresence>
          </tbody>
        </table>
      </div>
    </div>
  );
}

function fmt(s: string) {
  const n = parseFloat(s);
  if (Number.isNaN(n)) return s;
  return n.toLocaleString(undefined, { maximumFractionDigits: 3 });
}
