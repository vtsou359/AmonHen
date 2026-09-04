"use client";

/**
 * The left-hand incident list: search, sort, and one row per fire.
 *
 * Sorting and filtering happen here on the already-fetched picture rather than
 * on the server. The list is tens of rows, not thousands, so a round-trip per
 * keystroke would buy nothing and cost responsiveness.
 *
 * Rows show suspected non-fires by default. Hiding them without being asked
 * would eventually hide a real fire that happened to look odd, and nobody
 * would know to go looking — hence the explicit checkbox and its count.
 */
import { useMemo, useState } from "react";
import clsx from "clsx";
import { formatArea, formatLeadTime, formatRelative } from "@/lib/format";
import { SEVERITY_ORDER } from "@/lib/palette";
import { useUi } from "@/lib/store";
import type { IncidentSummary, Severity } from "@/lib/types";
import { VERDICT_HINT, VERDICT_LABEL } from "@/lib/labels";
import { DangerChip, SeverityChip, StatusDot } from "./Badges";

type SortKey = "severity" | "area" | "growth" | "recency";

export function IncidentList({ incidents }: { incidents: IncidentSummary[] }) {
  const { selectedIncidentId, select, requestFlyTo, hideSuspect, toggleHideSuspect } = useUi();
  const [sort, setSort] = useState<SortKey>("severity");
  const [query, setQuery] = useState("");

  const rows = useMemo(() => {
    let filtered = query
      ? incidents.filter((i) => i.name.toLowerCase().includes(query.toLowerCase()))
      : incidents;
    if (hideSuspect) {
      filtered = filtered.filter(
        (i) => i.verdict !== "likely_not_wildfire" && i.verdict !== "questionable",
      );
    }

    const sorted = [...filtered];
    switch (sort) {
      case "severity":
        // Ties broken by area, so the list is stable rather than jittering
        // between refreshes when several fires share a severity class.
        sorted.sort(
          (a, b) =>
            SEVERITY_ORDER.indexOf(a.severity as Severity) -
              SEVERITY_ORDER.indexOf(b.severity as Severity) ||
            b.estimated_area_ha - a.estimated_area_ha,
        );
        break;
      case "area":
        sorted.sort((a, b) => b.estimated_area_ha - a.estimated_area_ha);
        break;
      case "growth":
        sorted.sort((a, b) => b.growth_rate_ha_per_hour - a.growth_rate_ha_per_hour);
        break;
      case "recency":
        sorted.sort(
          (a, b) =>
            new Date(b.last_detected_at).getTime() - new Date(a.last_detected_at).getTime(),
        );
        break;
    }
    return sorted;
  }, [incidents, sort, query, hideSuspect]);

  const suspectCount = incidents.filter(
    (i) => i.verdict === "likely_not_wildfire" || i.verdict === "questionable",
  ).length;

  function open(incident: IncidentSummary) {
    select(incident.id);
    requestFlyTo({ longitude: incident.longitude, latitude: incident.latitude, zoom: 11 });
  }

  return (
    <div className="flex h-full flex-col">
      <div className="shrink-0 border-b border-edge p-3">
        <input
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="Search incidents…"
          className="w-full rounded-sm border border-edge bg-surface-base px-2.5 py-1.5 text-xs text-ink placeholder:text-ink-faint focus:border-brand-bright/60 focus:outline-none"
        />
        <div className="mt-2 flex gap-1">
          {(["severity", "area", "growth", "recency"] as SortKey[]).map((key) => (
            <button
              key={key}
              type="button"
              onClick={() => setSort(key)}
              className={clsx(
                "rounded-sm px-1.5 py-0.5 text-2xs capitalize transition-colors",
                sort === key
                  ? "bg-surface-raised text-brand-bright"
                  : "text-ink-faint hover:text-ink-muted",
              )}
            >
              {key}
            </button>
          ))}
        </div>
        {suspectCount > 0 && (
          <button
            type="button"
            onClick={toggleHideSuspect}
            title="Some heat detections behave more like factories or flares than fires. They are shown by default — hiding them is your call, not ours."
            className="mt-2 flex w-full items-center gap-1.5 text-left text-2xs text-ink-faint transition-colors hover:text-ink-muted"
          >
            <span
              className={clsx(
                "grid h-3 w-3 shrink-0 place-items-center rounded-[3px] border",
                hideSuspect ? "border-brand-bright bg-brand-bright" : "border-edge-strong",
              )}
            >
              {hideSuspect && (
                <svg width="8" height="8" viewBox="0 0 24 24" fill="none" stroke="#0E0F11" strokeWidth="4">
                  <path d="M20 6 9 17l-5-5" />
                </svg>
              )}
            </span>
            Hide {suspectCount} probably not fires
          </button>
        )}
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto">
        {rows.length === 0 && (
          <p className="p-4 text-xs text-ink-faint">
            {query ? "No incident matches that name." : "No active incidents in the area of interest."}
          </p>
        )}

        {rows.map((incident) => (
          <button
            key={incident.id}
            type="button"
            onClick={() => open(incident)}
            className={clsx(
              "w-full border-b border-edge-faint px-3 py-2.5 text-left transition-colors",
              selectedIncidentId === incident.id
                ? "bg-surface-raised"
                : "hover:bg-surface-raised/50",
              // Dimmed, not hidden: still scannable, clearly deprioritised.
              incident.verdict === "likely_not_wildfire" && "opacity-55",
            )}
          >
            <div className="flex items-start gap-2">
              <StatusDot status={incident.status} />
              <div className="min-w-0 flex-1">
                <div className="truncate text-xs font-medium text-ink">{incident.name}</div>

                <div className="tabular mt-1 flex flex-wrap items-center gap-x-3 gap-y-1 text-2xs text-ink-muted">
                  <span>{formatArea(incident.estimated_area_ha)}</span>
                  <span
                    className={clsx(
                      incident.growth_rate_ha_per_hour > 50 && "text-severity-major",
                    )}
                  >
                    {Math.round(incident.growth_rate_ha_per_hour)} ha/h
                  </span>
                  <span className="text-ink-faint">
                    {formatRelative(incident.last_detected_at)}
                  </span>
                </div>

                <div className="mt-1.5 flex flex-wrap gap-1">
                  <SeverityChip severity={incident.severity as Severity} />
                  <DangerChip danger={incident.danger_class} />
                  {(incident.verdict === "likely_not_wildfire" ||
                    incident.verdict === "questionable") && (
                    <span
                      className="chip border-ink-faint/40 text-ink-faint"
                      title={VERDICT_HINT[incident.verdict]}
                    >
                      {VERDICT_LABEL[incident.verdict]}
                    </span>
                  )}
                </div>

                {incident.top_threat && (
                  <div className="mt-1.5 flex items-center gap-1 text-2xs text-severity-critical">
                    <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.4">
                      <path d="M12 9v4m0 4h.01M10.3 3.9 1.8 18a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0Z" />
                    </svg>
                    {incident.top_threat} · {formatLeadTime(incident.minutes_to_top_threat)}
                  </div>
                )}
              </div>
            </div>
          </button>
        ))}
      </div>
    </div>
  );
}
