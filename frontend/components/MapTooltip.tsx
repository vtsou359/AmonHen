"use client";

import { formatArea, formatLeadTime, formatRelative, titleCase } from "@/lib/format";

export interface TooltipState {
  kind: "detection" | "incident" | "exposure" | "projection";
  x: number;
  y: number;
  properties: Record<string, any>;
}

export function MapTooltip({ state }: { state: TooltipState }) {
  const { x, y, kind, properties: p } = state;

  return (
    <div
      className="pointer-events-none absolute z-30 max-w-xs rounded-sm border border-edge-strong bg-surface-overlay/95 px-3 py-2 shadow-panel backdrop-blur-sm"
      // Offset so the cursor never covers the thing being described.
      style={{ left: x + 14, top: y + 14 }}
    >
      {kind === "incident" && (
        <>
          <div className="text-sm font-semibold text-ink">{p.name}</div>
          <div className="mt-1 flex flex-wrap gap-x-3 gap-y-0.5 text-2xs text-ink-muted">
            <span className="capitalize">{p.severity}</span>
            <span>{formatArea(p.area_ha ?? 0)}</span>
            <span>{Math.round(p.growth_rate_ha_per_hour ?? 0)} ha/h</span>
          </div>
          {p.spread_direction_label && (
            <div className="mt-1.5 text-2xs text-ink-muted">
              Head {Math.round(p.head_ros_m_per_min)} m/min → {p.spread_direction_label}
            </div>
          )}
        </>
      )}

      {kind === "detection" && (
        <>
          <div className="text-2xs uppercase tracking-wide text-ink-faint">
            {p.is_noise ? "Unmatched heat spot" : p.incident_name}
          </div>
          <div className="tabular mt-1 text-sm text-ink">
            {p.frp_mw != null ? `${p.frp_mw} MW` : "intensity unknown"}
          </div>
          <div className="mt-1 flex flex-wrap gap-x-3 text-2xs text-ink-muted">
            <span>{String(p.satellite).replace(/_/g, " ").toUpperCase()}</span>
            <span>{p.confidence} confidence</span>
            <span>{formatRelative(p.observed_at)}</span>
          </div>
        </>
      )}

      {kind === "projection" && (
        <>
          <div className="text-2xs uppercase tracking-wide text-ink-faint">
            {p.kind === "core" ? "Certain core" : p.kind === "scenario" ? p.scenario_label : "Could reach"}
          </div>
          <div className="mt-0.5 text-sm font-semibold text-ink">
            {Math.round((p.minutes as number) / 60)} h projection
          </div>
          <div className="mt-1 text-2xs text-ink-muted">
            {p.incident_name}
            {p.area_ha != null && ` · ${Math.round(p.area_ha).toLocaleString("en-GB")} ha`}
          </div>
          {p.rationale && (
            <div className="mt-1 text-2xs leading-relaxed text-ink-faint">{p.rationale}</div>
          )}
        </>
      )}

      {kind === "exposure" && (
        <>
          <div className="text-sm font-semibold text-ink">{p.name}</div>
          <div className="text-2xs text-ink-faint">{titleCase(String(p.kind))}</div>
          <div className="mt-1.5 flex flex-wrap gap-x-3 text-2xs text-ink-muted">
            <span>{p.distance_km?.toFixed(1)} km</span>
            {p.is_downwind && <span className="text-severity-critical">Downwind</span>}
            {p.minutes_to_impact != null && (
              <span>ETA {formatLeadTime(p.minutes_to_impact)}</span>
            )}
          </div>
        </>
      )}
    </div>
  );
}
