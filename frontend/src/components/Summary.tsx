import { useQuery } from "@tanstack/react-query";
import { api } from "../api";

type Summary = {
  counts_by_status: Record<string, number>;
  kgco2e_by_scope: Record<string, number>;
};

export function Summary() {
  const { data } = useQuery<Summary>({
    queryKey: ["summary"],
    queryFn: api.summary,
    refetchInterval: 10_000,
  });
  if (!data) return null;
  const c = data.counts_by_status;
  const s = data.kgco2e_by_scope;
  const tco2e = ((s.scope_1 || 0) + (s.scope_2 || 0) + (s.scope_3 || 0)) / 1000;
  return (
    <div className="summary-grid">
      <Kpi label="Pending review" value={(c.pending || 0) + (c.flagged || 0)} />
      <Kpi label="Approved" value={c.approved || 0} />
      <Kpi label="Locked for audit" value={c.locked || 0} />
      <Kpi label="Total tCO₂e (approved+locked)" value={tco2e.toFixed(2)} />
    </div>
  );
}

function Kpi({ label, value }: { label: string; value: number | string }) {
  return (
    <div className="kpi">
      <div className="label">{label}</div>
      <div className="value">{value}</div>
    </div>
  );
}
