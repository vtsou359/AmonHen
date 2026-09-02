"use client";

import { useEffect, useState } from "react";
import clsx from "clsx";
import { ApiError, useIncident } from "@/lib/api";
import { formatArea, formatLeadTime, formatRelative, titleCase } from "@/lib/format";
import { DANGER_HEX } from "@/lib/palette";
import {
  DANGER_LABEL,
  FWI_CODE,
  STATUS_LABEL,
  VERDICT_HINT,
  VERDICT_LABEL,
  exposureLabel,
} from "@/lib/labels";
import { useUi } from "@/lib/store";
import type { ProjectionSummary, Severity } from "@/lib/types";
import { DangerChip, SeverityChip } from "./Badges";

/**
 * Everything known about one fire, on one screen.
 *
 * Ordered the way a duty officer reads: the plain-language brief first, then
 * what is threatened, then the evidence behind it. The numbers that drive
 * decisions come before the numbers that explain them.
 */
export function IncidentDossier() {
  const { selectedIncidentId, select, requestFlyTo } = useUi();
  const { data, isLoading, error } = useIncident(selectedIncidentId);

  // A 404 means this incident is no longer in the current picture. Drop the
  // selection rather than leaving the panel stuck on a skeleton forever.
  const isGone = error instanceof ApiError && error.status === 404;
  useEffect(() => {
    if (isGone) select(null);
  }, [isGone, select]);

  if (!selectedIncidentId) {
    return (
      <aside className="flex w-[380px] shrink-0 items-center justify-center border-l border-edge bg-surface-panel p-6">
        <p className="text-center text-xs leading-relaxed text-ink-faint">
          Select an incident to open its dossier.
          <br />
          <span className="text-ink-faint/70">
            Clustered detections, fire weather, projected spread and exposure.
          </span>
        </p>
      </aside>
    );
  }

  if (error && !isGone) {
    return (
      <aside className="flex w-[380px] shrink-0 items-center justify-center border-l border-edge bg-surface-panel p-6">
        <p className="text-center text-xs leading-relaxed text-severity-critical">
          Could not load this incident.
          <br />
          <span className="text-ink-faint">{String(error.message)}</span>
        </p>
      </aside>
    );
  }

  if (isLoading || !data) {
    return (
      <aside className="w-[380px] shrink-0 border-l border-edge bg-surface-panel p-4">
        <div className="h-4 w-2/3 animate-pulse rounded-sm bg-surface-raised" />
        <div className="mt-3 h-20 animate-pulse rounded-sm bg-surface-raised" />
        <div className="mt-3 h-40 animate-pulse rounded-sm bg-surface-raised" />
      </aside>
    );
  }

  const { incident, weather, danger, spread, exposed, brief, perimeter, projection, plausibility } =
    data;
  const threatened = exposed.filter((e) => e.minutes_to_impact != null || e.is_downwind);

  return (
    <aside className="flex w-[380px] shrink-0 flex-col border-l border-edge bg-surface-panel">
      {/* ---------------------------------------------------------- header */}
      <div className="shrink-0 border-b border-edge p-4">
        <div className="flex items-start justify-between gap-2">
          <div className="min-w-0">
            <h2 className="truncate text-sm font-semibold text-ink">{incident.name}</h2>
            <p className="mt-0.5 text-2xs text-ink-faint">
              First seen {formatRelative(incident.first_detected_at)} · last{" "}
              {formatRelative(incident.last_detected_at)}
            </p>
          </div>
          <button
            type="button"
            onClick={() => select(null)}
            className="shrink-0 rounded-sm p-1 text-ink-faint hover:bg-surface-raised hover:text-ink"
            aria-label="Close dossier"
          >
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
              <path d="M18 6 6 18M6 6l12 12" />
            </svg>
          </button>
        </div>

        <div className="mt-2 flex flex-wrap gap-1">
          <SeverityChip severity={incident.severity as Severity} />
          <DangerChip danger={danger?.danger_class ?? null} />
          <span className="chip border-edge text-ink-muted">{STATUS_LABEL[incident.status] ?? incident.status}</span>
        </div>

        <button
          type="button"
          onClick={() =>
            requestFlyTo({
              longitude: incident.longitude,
              latitude: incident.latitude,
              zoom: 12,
            })
          }
          className="mt-2.5 text-2xs text-brand-bright hover:underline"
        >
          Centre map on incident →
        </button>
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto">
        {/* ------------------------------------------------------- the brief */}
        {/* Shown first when in doubt: if this might not be a fire at all, that
            changes how every number below it should be read. */}
        {plausibility &&
          plausibility.verdict !== "wildfire" &&
          plausibility.verdict !== "probable" && (
            <Section title="Is this a fire?">
              <div className="mb-2 flex items-baseline justify-between gap-2">
                <span className="text-xs font-medium text-severity-moderate">
                  {VERDICT_LABEL[plausibility.verdict]}
                </span>
                <span className="tabular text-2xs text-ink-faint">
                  {Math.round(plausibility.score * 100)}/100
                </span>
              </div>
              <p className="mb-2 text-2xs leading-relaxed text-ink-faint">
                {VERDICT_HINT[plausibility.verdict]}
              </p>
              <ul className="space-y-1.5">
                {plausibility.reasons.map((reason) => (
                  <li key={reason} className="text-2xs leading-relaxed text-ink-muted">
                    · {reason}
                  </li>
                ))}
              </ul>
            </Section>
          )}

        <Section title="Summary">
          <p className="text-xs leading-relaxed text-ink-muted">{brief}</p>
        </Section>

        {/* ------------------------------------------------------ key figures */}
        <Section title="This fire">
          <div className="grid grid-cols-2 gap-3">
            <Figure label="Burnt area" value={formatArea(incident.estimated_area_ha)} />
            <Figure
              label="Growing by"
              value={`${Math.round(incident.growth_rate_ha_per_hour)} ha/h`}
              alert={incident.growth_rate_ha_per_hour > 50}
            />
            <Figure label="Peak intensity" value={`${Math.round(incident.max_frp_mw)} MW`} hint="Heat output of the hottest satellite pixel. Higher means a more intense fire." />
            <Figure label="Heat spots" value={String(data.detection_count)} hint="How many satellite hot pixels make up this fire." />
          </div>
          {perimeter && (
            <p className="mt-3 border-t border-edge-faint pt-2 text-2xs leading-relaxed text-ink-faint">
              The outline is a rough sketch drawn around satellite heat spots, not a
              surveyed boundary. Treat the shape as approximate.
            </p>
          )}
        </Section>

        {/* ---------------------------------------------------------- threats */}
        {/* When an ensemble exists it supersedes the single-estimate exposure
            list: "6 of 9 scenarios" is a far more useful statement than one
            deterministic ETA, and it is the honest one. */}
        {projection && projection.threats.length > 0 ? (
          <Section title={`At risk (${projection.threats.length})`}>
            <p className="mb-2.5 text-2xs leading-relaxed text-ink-faint">
              How many of the {projection.scenarios.length} cases reach each place, and how
              soon the earliest one gets there.
            </p>
            <ul className="space-y-2">
              {projection.threats.slice(0, 8).map((threat) => (
                <li key={`${threat.name}-${threat.kind}`}>
                  <div className="flex items-baseline justify-between gap-2">
                    <span className="truncate text-xs text-ink">{threat.name}</span>
                    <span className="tabular shrink-0 text-2xs text-ink-muted">
                      {threat.hit_count}/{threat.scenario_count}
                    </span>
                  </div>
                  {/* A bar, not a percentage: these are named assumptions that
                      lead somewhere, not samples from a distribution. */}
                  <div className="mt-1 h-1 w-full overflow-hidden rounded-full bg-edge">
                    <div
                      className={clsx(
                        "h-full rounded-full",
                        threat.likelihood > 0.66
                          ? "bg-severity-critical"
                          : threat.likelihood > 0.33
                            ? "bg-severity-major"
                            : "bg-severity-moderate",
                      )}
                      style={{ width: `${Math.round(threat.likelihood * 100)}%` }}
                    />
                  </div>
                  <div className="tabular mt-1 flex justify-between text-2xs text-ink-faint">
                    <span>
                      {exposureLabel(threat.kind)}
                      {threat.population ? ` · ${threat.population.toLocaleString("en-GB")}` : ""}
                    </span>
                    <span>
                      earliest {formatLeadTime(threat.earliest_minutes)} · {threat.distance_km.toFixed(1)} km
                    </span>
                  </div>
                </li>
              ))}
            </ul>
          </Section>
        ) : (
        <Section title={`Exposure (${threatened.length})`}>
          {threatened.length === 0 ? (
            <p className="text-2xs text-ink-faint">
              Nothing in the gazetteer is downwind within 30 km.
            </p>
          ) : (
            <ul className="space-y-1.5">
              {threatened.slice(0, 8).map((element) => (
                <li
                  key={`${element.name}-${element.kind}`}
                  className="flex items-baseline justify-between gap-2 border-b border-edge-faint pb-1.5 last:border-0"
                >
                  <div className="min-w-0">
                    <div className="truncate text-xs text-ink">{element.name}</div>
                    <div className="text-2xs text-ink-faint">
                      {exposureLabel(element.kind)}
                      {element.population ? ` · ${element.population.toLocaleString("en-GB")} people` : ""}
                    </div>
                  </div>
                  <div className="tabular shrink-0 text-right">
                    <div
                      className={clsx(
                        "text-xs font-medium",
                        element.minutes_to_impact != null && element.minutes_to_impact < 120
                          ? "text-severity-critical"
                          : "text-ink-muted",
                      )}
                    >
                      {element.minutes_to_impact != null
                        ? formatLeadTime(element.minutes_to_impact)
                        : element.is_downwind
                          ? "> 12 h"
                          : "—"}
                    </div>
                    <div className="text-2xs text-ink-faint">
                      {element.distance_km?.toFixed(1)} km
                    </div>
                  </div>
                </li>
              ))}
            </ul>
          )}
        </Section>
        )}

        {/* ---------------------------------------------------------- weather */}
        {weather && (
          <Section title="Weather at the fire">
            <div className="grid grid-cols-2 gap-3">
              <Figure label="Temperature" value={`${weather.temperature_c?.toFixed(0) ?? "—"} °C`} />
              <Figure label="Humidity" value={`${weather.relative_humidity_pct?.toFixed(0) ?? "—"} %`} />
              <Figure label="Wind" value={`${weather.wind_speed_kmh?.toFixed(0) ?? "—"} km/h`} />
              <Figure label="Gusts" value={`${weather.wind_gust_kmh?.toFixed(0) ?? "—"} km/h`} />
            </div>
          </Section>
        )}

        {/* ------------------------------------------------------------- FWI */}
        {danger && (
          <Section title="Fire danger">
            <div
              className="mb-3 flex items-baseline gap-2 rounded-sm px-3 py-2"
              style={{ backgroundColor: `${DANGER_HEX[danger.danger_class]}18` }}
            >
              <span
                className="tabular text-3xl font-semibold leading-none"
                style={{ color: DANGER_HEX[danger.danger_class] }}
              >
                {danger.fwi.toFixed(0)}
              </span>
              <span className="text-2xs" style={{ color: DANGER_HEX[danger.danger_class] }}>
                {DANGER_LABEL[danger.danger_class]}
              </span>
            </div>
            <dl className="grid grid-cols-3 gap-x-2 gap-y-2">
              {(["ffmc", "dmc", "dc", "isi", "bui"] as const).map((code) => (
                <Code
                  key={code}
                  label={FWI_CODE[code].label}
                  value={danger[code]}
                  hint={FWI_CODE[code].hint}
                />
              ))}
            </dl>
          </Section>
        )}

        {/* ------------------------------------------------------ projections */}
        {projection ? (
          <ProjectionSection projection={projection} spread={spread} />
        ) : (
          spread && (
            <Section title="Where it could go">
              <div className="flex items-center gap-4">
                <Compass bearing={spread.direction_deg} />
                <div>
                  <div className="tabular text-2xl font-semibold leading-none text-ink">
                    {spread.head_ros_m_per_min.toFixed(0)}
                    <span className="ml-1 text-xs font-normal text-ink-faint">m/min</span>
                  </div>
                  <div className="mt-1 text-2xs text-ink-muted">
                    toward {spread.direction_label} ({spread.direction_deg.toFixed(0)}°)
                  </div>
                </div>
              </div>
            </Section>
          )
        )}

      </div>
    </aside>
  );
}

/**
 * The ensemble, as a range rather than a number.
 *
 * The headline is deliberately the *spread* of forward rates across scenarios,
 * not the expected one. A single figure invites people to plan against it; the
 * range invites them to plan against being wrong, which is the point of running
 * an ensemble at all.
 */
function ProjectionSection({
  projection,
  spread,
}: {
  projection: ProjectionSummary;
  spread: { direction_deg: number; direction_label: string } | null;
}) {
  const [showScenarios, setShowScenarios] = useState(false);
  const rates = projection.scenarios.map((s) => s.head_ros_m_per_min);
  const slowest = Math.min(...rates);
  const fastest = Math.max(...rates);
  const expected = projection.scenarios.find((s) => s.id === "expected");
  const longest = projection.horizons_minutes[projection.horizons_minutes.length - 1];

  return (
    <Section title={`Where it could go · ${projection.scenarios.length} cases`}>
      <div className="flex items-center gap-4">
        {spread && <Compass bearing={spread.direction_deg} />}
        <div>
          <div className="tabular text-2xl font-semibold leading-none text-ink">
            {slowest.toFixed(0)}–{fastest.toFixed(0)}
            <span className="ml-1 text-xs font-normal text-ink-faint">m/min</span>
          </div>
          <div className="mt-1 text-2xs text-ink-muted">
            front speed, slowest to fastest case
            {expected && ` · most likely ${expected.head_ros_m_per_min.toFixed(0)}`}
          </div>
          {spread && (
            <div className="text-2xs text-ink-faint">
              toward {spread.direction_label} ({spread.direction_deg.toFixed(0)}°)
            </div>
          )}
        </div>
      </div>

      {projection.terrain && (
        <div className="mt-3 flex items-baseline justify-between gap-2 border-t border-edge-faint pt-2.5">
          <div>
            <div className="label">Ground</div>
            <div className="text-xs capitalize text-ink">{projection.terrain.descriptor}</div>
          </div>
          <div
            className="tabular text-right text-2xs leading-tight text-ink-faint"
            title={`Measured from ${projection.terrain.source}. Fire moves considerably faster uphill, so this shapes every projection above.`}
          >
            {projection.terrain.slope_pct.toFixed(0)}% slope
            <br />
            {projection.terrain.elevation_m.toFixed(0)} m up
          </div>
        </div>
      )}

      <div className="mt-3 grid grid-cols-3 gap-2 border-t border-edge-faint pt-2.5">
        {projection.horizons_minutes.map((minutes) => (
          <div key={minutes}>
            <div className="label">{minutes / 60} h</div>
            <div className="tabular text-sm font-semibold text-ink">
              {formatArea(projection.envelope_areas_ha[String(minutes)] ?? 0)}
            </div>
          </div>
        ))}
      </div>
      <p className="mt-1 text-2xs leading-relaxed text-ink-faint">
        Area the fire <strong>could</strong> reach if nothing stops it. Not a prediction
        that all of it burns.
      </p>

      <button
        type="button"
        onClick={() => setShowScenarios((v) => !v)}
        className="mt-2.5 text-2xs text-brand-bright hover:underline"
      >
        {showScenarios ? "Hide the cases" : `Show all ${projection.scenarios.length} cases`}
      </button>

      {showScenarios && (
        <ul className="mt-2 space-y-1.5 border-t border-edge-faint pt-2">
          {projection.scenarios.map((scenario) => (
            <li key={scenario.id} title={scenario.rationale}>
              <div className="flex items-baseline justify-between gap-2">
                <span className="truncate text-2xs text-ink">{scenario.label}</span>
                <span className="tabular shrink-0 text-2xs text-ink-muted">
                  {scenario.head_ros_m_per_min.toFixed(0)} m/min · {scenario.direction_label}
                </span>
              </div>
              <div className="text-[9px] leading-tight text-ink-faint">
                {scenario.rationale}
                {scenario.areas_ha[String(longest)] != null &&
                  ` — ${formatArea(scenario.areas_ha[String(longest)])} at ${longest / 60} h`}
              </div>
            </li>
          ))}
        </ul>
      )}

      <div className="mt-3 rounded-sm border border-severity-moderate/25 bg-severity-moderate/[0.07] p-2">
        <ul className="space-y-1">
          {projection.caveats.map((caveat) => (
            <li key={caveat} className="text-2xs leading-relaxed text-ink-faint">
              · {caveat}
            </li>
          ))}
        </ul>
      </div>
    </Section>
  );
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="border-b border-edge p-4">
      <h3 className="label mb-2.5">{title}</h3>
      {children}
    </section>
  );
}

function Figure({
  label,
  value,
  alert,
  hint,
}: {
  label: string;
  value: string;
  alert?: boolean;
  hint?: string;
}) {
  return (
    <div title={hint}>
      <div className="label">{label}</div>
      <div
        className={clsx(
          "tabular text-base font-semibold leading-tight",
          alert ? "text-severity-major" : "text-ink",
        )}
      >
        {value}
      </div>
    </div>
  );
}

function Code({ label, value, hint }: { label: string; value: number; hint: string }) {
  return (
    <div title={hint}>
      <dt className="text-[9px] font-medium leading-tight text-ink-faint">{label}</dt>
      <dd className="tabular text-xs text-ink-muted">{value.toFixed(1)}</dd>
    </div>
  );
}

/** A small compass rose showing the predicted head direction. */
function Compass({ bearing }: { bearing: number }) {
  return (
    <svg width="56" height="56" viewBox="0 0 56 56" className="shrink-0">
      <circle cx="28" cy="28" r="25" fill="none" stroke="#1E2740" strokeWidth="1" />
      <circle cx="28" cy="28" r="17" fill="none" stroke="#151D2E" strokeWidth="1" />
      {["N", "E", "S", "W"].map((point, index) => {
        const angle = (index * 90 * Math.PI) / 180;
        return (
          <text
            key={point}
            x={28 + Math.sin(angle) * 31}
            y={28 - Math.cos(angle) * 31 + 3}
            textAnchor="middle"
            fontSize="7"
            fill="#5A6684"
          >
            {point}
          </text>
        );
      })}
      <g transform={`rotate(${bearing} 28 28)`}>
        <path d="M28 8 L33 30 L28 26 L23 30 Z" fill="#F97316" />
      </g>
      <circle cx="28" cy="28" r="2.5" fill="#0C111C" stroke="#2B3752" />
    </svg>
  );
}
