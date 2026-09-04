"use client";

import { useEffect, useState } from "react";
import clsx from "clsx";
import { useOverlayCatalogue } from "@/lib/api";
import { SEVERITY_HEX } from "@/lib/palette";
import { SEVERITY_LABEL } from "@/lib/labels";
import { useUi } from "@/lib/store";
import type { Severity } from "@/lib/types";

/**
 * The map's colour keys.
 *
 * Deliberately *not* inside the layer panel. The panel is a dropdown that
 * collapses to give the map room back, but you need a legend precisely when you
 * are reading the map — i.e. when the panel is shut. So legends live here, on
 * the map, and appear the moment a layer is switched on.
 */
export function MapLegends() {
  const { overlays, layers } = useUi();
  const { data: catalogue } = useOverlayCatalogue();
  const [collapsed, setCollapsed] = useState(false);

  const active = (catalogue?.overlays ?? []).filter((o) => overlays[o.id]?.enabled);
  const severities: Severity[] = ["critical", "major", "moderate", "minor"];

  return (
    <div className="pointer-events-auto absolute bottom-14 left-3 z-20 flex max-h-[70%] w-60 flex-col rounded-sm border border-edge bg-surface-panel/95 backdrop-blur-sm">
      <button
        type="button"
        onClick={() => setCollapsed((c) => !c)}
        className="flex shrink-0 items-center justify-between px-3 py-2 text-left hover:bg-surface-raised/50"
      >
        <span className="label">Legend{active.length > 0 && ` · ${active.length + 1}`}</span>
        <svg
          width="10"
          height="10"
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          strokeWidth="3"
          className={clsx("text-ink-faint transition-transform", collapsed && "-rotate-90")}
        >
          <path d="m6 9 6 6 6-6" />
        </svg>
      </button>

      {!collapsed && (
        <div className="min-h-0 flex-1 overflow-y-auto px-3 pb-3">
          {/* ---------------------------------------------- our own symbology */}
          {layers.incidents && (
            <div className="mb-3">
              <div className="mb-1.5 text-[9px] font-medium uppercase tracking-wider text-ink-faint/70">
                How bad each fire is
              </div>
              <div className="flex flex-col gap-1">
                {severities.map((s) => (
                  <div key={s} className="flex items-center gap-2">
                    <span
                      className="h-2.5 w-2.5 rounded-full border"
                      style={{ borderColor: SEVERITY_HEX[s] }}
                    />
                    <span className="text-2xs text-ink-muted">{SEVERITY_LABEL[s]}</span>
                  </div>
                ))}
                {layers.detections && (
                  <div className="mt-1 flex items-center gap-2 border-t border-edge-faint pt-1.5">
                    <span className="h-2.5 w-2.5 rounded-full bg-[#FCB040]" />
                    <span className="text-2xs text-ink-muted">Heat spot (brighter = hotter)</span>
                  </div>
                )}
                {/* The outline layer draws a measurement and a guess in the
                    same place. Without this the difference is visible but
                    unexplained, which is worse than not drawing it. */}
                {layers.perimeters && (
                  <div className="mt-1 flex flex-col gap-1 border-t border-edge-faint pt-1.5">
                    <div className="flex items-center gap-2">
                      <span className="h-2.5 w-3.5 shrink-0 rounded-[2px] border-2 border-ink-muted bg-ink-muted/35" />
                      <span
                        className="text-2xs text-ink-muted"
                        title="Measured from Sentinel-2 imagery at 20 m, comparing the ground before and after."
                      >
                        Burn scar, measured
                      </span>
                    </div>
                    <div className="flex items-center gap-2">
                      <span className="h-2.5 w-3.5 shrink-0 rounded-[2px] border border-ink-faint bg-ink-faint/10" />
                      <span
                        className="text-2xs text-ink-muted"
                        title="A convex sketch drawn around 375 m satellite heat spots. Approximate."
                      >
                        Rough outline, estimated
                      </span>
                    </div>
                  </div>
                )}
              </div>
            </div>
          )}

          {/* ------------------------------------------- Copernicus overlays */}
          {active.map((overlay) => (
            <div key={overlay.id} className="mb-3 last:mb-0">
              <div className="mb-1 flex items-baseline justify-between gap-2">
                <span className="text-2xs font-medium leading-tight text-ink">
                  {overlay.title}
                </span>
                <span className="tabular shrink-0 text-[9px] text-ink-faint">
                  {Math.round((overlays[overlay.id]?.opacity ?? overlay.default_opacity) * 100)}%
                </span>
              </div>
              <OverlayLegend
                url={overlay.legend_url}
                title={overlay.title}
                description={overlay.description}
              />
            </div>
          ))}

          {active.length === 0 && (
            <p className="text-[9px] leading-relaxed text-ink-faint">
              Turn on a map layer to see what its colours mean.
            </p>
          )}
        </div>
      )}
    </div>
  );
}


/** How long to wait for a legend image before giving up on it. */
const LEGEND_TIMEOUT_MS = 8000;

/**
 * One overlay's colour key, with the source's own image when it arrives.
 *
 * EFFIS is not uniformly reliable here, and it fails in two different ways.
 * `fuel_map` ("What would burn") answers GetLegendGraphic with HTTP 200,
 * content-type image/png and content-length 41173 — then sends no body. Some
 * of the time the connection is cut and the browser paints a broken-image icon
 * inside the legend card; the rest of the time it simply hangs, `complete`
 * stays false and `onError` never fires at all.
 *
 * So both are handled. An error falls back immediately, and an image that has
 * not arrived within a few seconds is treated as absent too — because a legend
 * card that is permanently blank tells an operator less than the one sentence
 * describing what the layer shows.
 */
function OverlayLegend({
  url,
  title,
  description,
}: {
  url: string;
  title: string;
  description: string;
}) {
  const [failed, setFailed] = useState(false);
  const [loaded, setLoaded] = useState(false);

  useEffect(() => {
    setFailed(false);
    setLoaded(false);
    if (!url) return;
    // Once this fires the <img> is unmounted, so a very slow image cannot come
    // back later — which is the intended trade. Eight seconds is already far
    // longer than anyone will sit looking at an empty legend card.
    const timer = setTimeout(() => setFailed(true), LEGEND_TIMEOUT_MS);
    return () => clearTimeout(timer);
  }, [url]);

  if (!url || failed) {
    return (
      <p className="text-[9px] leading-relaxed text-ink-faint">
        {description}
        {url && failed && (
          <span className="text-ink-faint/70">
            {" "}
            (no colour key — the source did not return one)
          </span>
        )}
      </p>
    );
  }

  return (
    <>
      {/*
        EFFIS serves legends as PNGs with a solid white background and dark
        text. They are shown on a light card rather than filtered to match the
        dark UI: inverting would shift every swatch, and a legend whose colours
        do not match the map is worse than useless.
      */}
      <div className="rounded-sm bg-white p-1.5">
        {/*
          `max-w-full` without `w-full`, deliberately. EFFIS legends range from
          a 72px strip to a 367px class table: constraining only the maximum
          lets the small ones render pixel-crisp at natural size, while the
          oversized ones shrink to fit rather than truncating their labels — a
          legend reading "Broadleaved or mixed fores…" is not a legend. Forcing
          `w-full` instead upscaled the narrow legends into a blurry mess.
        */}
        <img
          src={url}
          alt={`${title} legend`}
          className="mx-auto block h-auto max-w-full"
          onLoad={() => setLoaded(true)}
          onError={() => setFailed(true)}
        />
      </div>
      {!loaded && (
        <p className="mt-1 text-[9px] leading-relaxed text-ink-faint">
          Loading colour key…
        </p>
      )}
    </>
  );
}
