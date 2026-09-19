"use client";

import { useEffect, useState } from "react";
import clsx from "clsx";
import { ApiError, forceRefresh } from "@/lib/api";
import { formatArea, formatRelative } from "@/lib/format";
import type { PictureResponse } from "@/lib/types";
import { Logo } from "./Logo";

/**
 * The masthead.
 *
 * It carries the identity now that the phase rail is gone: the platform does
 * one thing — monitor active fires and project where they go — so a navigation
 * column listing four things it does not do was costing 72px to advertise
 * absence. The logo moved here and grew.
 */
export function TopBar({
  picture,
  onRefreshed,
}: {
  picture?: PictureResponse;
  onRefreshed: () => void;
}) {
  const [refreshing, setRefreshing] = useState(false);
  const [note, setNote] = useState<string | null>(null);

  // The note is about something that has just happened and stops being true
  // shortly afterwards — the cooldown is a minute — so it clears itself rather
  // than sitting in the masthead asserting a stale fact.
  useEffect(() => {
    if (!note) return;
    const timer = setTimeout(() => setNote(null), 8000);
    return () => clearTimeout(timer);
  }, [note]);

  async function refresh() {
    setRefreshing(true);
    setNote(null);
    try {
      await forceRefresh();
      onRefreshed();
    } catch (error) {
      // Previously this threw into the void: the spinner stopped and nothing
      // else changed, so a refused refresh looked exactly like a successful one.
      // A forced refresh bypasses every cache and spends NASA quota, so the
      // backend allows one a minute and answers 429 to the rest.
      setNote(
        error instanceof ApiError && error.status === 429
          ? "Refreshed moments ago — try again shortly."
          : "Refresh failed.",
      );
    } finally {
      setRefreshing(false);
    }
  }

  return (
    <header className="flex h-16 shrink-0 items-center gap-6 border-b border-edge bg-surface-panel px-4">
      <div className="flex shrink-0 items-center gap-2.5">
        <Logo size={34} />
        <div>
          <h1 className="text-[13px] font-semibold leading-none tracking-[0.14em] text-ink">
            AMON HEN
          </h1>
          <p className="mt-1 text-2xs leading-none text-ink-faint">
            {picture?.area_of_interest ?? "—"}
            {picture && ` · ${formatRelative(picture.generated_at)}`}
          </p>
        </div>
      </div>

      <div className="flex items-center gap-5 border-l border-edge pl-6">
        <Metric label="Active" value={picture ? String(picture.active_count) : "—"} accent />
        <Metric label="Incidents" value={picture ? String(picture.total_incidents) : "—"} />
        <Metric label="Area" value={picture ? formatArea(picture.total_area_ha) : "—"} />
        <Metric
          label="Unmatched"
          value={picture ? String(picture.unclustered_detections) : "—"}
          hint="Lone satellite heat spots that could not be matched to a fire — often factories, farm burning or sun glare. Worth a look."
        />
        {picture && picture.suspect_count > 0 && (
          <Metric
            label="Not fires?"
            value={String(picture.suspect_count)}
            muted
            hint="Heat detections that behave more like factories or flares than wildfires. Still listed — open one to see the reasoning."
          />
        )}
        {picture && picture.dropped_outside_boundary > 0 && (
          <Metric
            label="Outside area"
            value={String(picture.dropped_outside_boundary)}
            muted
            hint={`Heat spots detected just beyond ${picture.area_of_interest} — fires in neighbouring countries, filtered out of the list`}
          />
        )}
      </div>

      <div className="ml-auto flex items-center gap-2.5">
        {note && (
          <span role="status" className="text-2xs text-ink-faint">
            {note}
          </span>
        )}

        <button
          type="button"
          onClick={refresh}
          disabled={refreshing}
          className="flex items-center gap-1.5 rounded-sm border border-edge px-2.5 py-1 text-2xs text-ink-muted transition-colors hover:border-edge-strong hover:text-ink disabled:opacity-50"
        >
          <svg
            width="11"
            height="11"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth="2.2"
            strokeLinecap="round"
            className={refreshing ? "animate-spin" : undefined}
          >
            <path d="M21 12a9 9 0 1 1-2.6-6.4M21 3v6h-6" />
          </svg>
          {refreshing ? "Rebuilding" : "Refresh"}
        </button>
      </div>
    </header>
  );
}

function Metric({
  label,
  value,
  accent,
  muted,
  hint,
}: {
  label: string;
  value: string;
  accent?: boolean;
  muted?: boolean;
  hint?: string;
}) {
  return (
    <div title={hint}>
      <div className="label">{label}</div>
      <div
        className={clsx(
          "tabular text-lg font-semibold leading-tight",
          accent ? "text-brand-bright" : muted ? "text-ink-faint" : "text-ink",
        )}
      >
        {value}
      </div>
    </div>
  );
}
