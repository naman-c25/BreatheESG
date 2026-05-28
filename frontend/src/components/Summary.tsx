import { useEffect, useRef } from "react";
import { useQuery } from "@tanstack/react-query";
import gsap from "gsap";
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
      <Kpi label="Total tCO₂e (approved+locked)" value={tco2e} fractionDigits={2} />
    </div>
  );
}

/**
 * KPI card with count-up animation.
 * GSAP use kiya hai (Framer Motion nahi) kyunki yahan ek arbitrary number ko
 * tween karna hai — CSS property nahi. Framer mein yeh awkward hota hai.
 * Har library ka apna use case hai, dono ek saath rakhne ki real wajah yahi.
 */
function Kpi({ label, value, fractionDigits = 0 }: { label: string; value: number; fractionDigits?: number }) {
  const ref = useRef<HTMLDivElement>(null);
  const prevRef = useRef(0);
  useEffect(() => {
    const obj = { v: prevRef.current };
    const target = value;
    gsap.to(obj, {
      v: target,
      duration: 0.6,
      ease: "power2.out",
      onUpdate: () => {
        if (ref.current) {
          ref.current.textContent = obj.v.toLocaleString(undefined, {
            minimumFractionDigits: fractionDigits, maximumFractionDigits: fractionDigits,
          });
        }
      },
    });
    prevRef.current = target;
  }, [value, fractionDigits]);
  return (
    <div className="kpi">
      <div className="label">{label}</div>
      <div className="value" ref={ref}>0</div>
    </div>
  );
}
