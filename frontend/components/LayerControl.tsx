"use client";

import { useEffect, useRef, useState } from "react";
import clsx from "clsx";
import { useOverlayCatalogue } from "@/lib/api";
import { useUi, type LayerKey, type ProjectionKind } from "@/lib/store";
import type { Overlay, OverlayCategory } from "@/lib/types";
import { OVERLAY_CATEGORY_LABEL } from "@/lib/labels";

/**
 * The map's layer control, as a dropdown.
 *
 * It used to be a permanently-open panel, which cost a 260px column of map on a
 * screen whose entire job is showing the map. Collapsed by default now, with
 * the active-overlay count on the trigger so the state is legible without
 * opening it.
 *
 * Contents are ordered the way the map is stacked — Copernicus overlays, then
 * our fire data, then the basemap underneath — so reading the panel top to
 * bottom tells you what is drawn over what.
 *
 * Colour keys deliberately live outside this panel, in `MapLegends`: you need a
 * legend while reading the map, which is exactly when this is shut.
 */

const DATA_LAYERS: Array<{ key: LayerKey; label: string; hint: string }> = [
  {
    key: "projections",
    label: "Where it could go",
    hint: "Nine different assumptions about wind, vegetation and slope, drawn together. The shaded area is what any of them reach.",
  },
  { key: "incidents", label: "Fires", hint: "Each fire, with an arrow showing which way it is heading" },
  {
    key: "perimeters",
    label: "Burnt area outline",
    hint: "Solid where the burn scar has been measured from Sentinel-2 imagery at 20 m; faint and thin where it is still only a sketch drawn around satellite heat spots.",
  },
  {
    key: "detections",
    label: "Satellite heat spots",
    hint: "Individual hot pixels seen from orbit. Brighter and larger means more intense.",
  },
  { key: "exposure", label: "Places at risk", hint: "Towns and sites the fire could reach" },
];

const CATEGORY_ORDER: OverlayCategory[] = [
  "danger",
  "behaviour",
  "terrain",
  "fuel",
  "exposure",
  "detections",
];

export function LayerControl() {
  const { layers, toggleLayer, showNoise, toggleNoise, basemap, setBasemap, overlays } = useUi();
  const { data: catalogue } = useOverlayCatalogue();
  const [open, setOpen] = useState(false);
  const root = useRef<HTMLDivElement>(null);

  // Close on outside click and on Escape — a dropdown that traps you is worse
  // than the panel it replaced.
  useEffect(() => {
    if (!open) return;
    const onPointerDown = (event: MouseEvent) => {
      if (root.current && !root.current.contains(event.target as Node)) setOpen(false);
    };
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") setOpen(false);
    };
    document.addEventListener("mousedown", onPointerDown);
    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.removeEventListener("mousedown", onPointerDown);
      document.removeEventListener("keydown", onKeyDown);
    };
  }, [open]);

  const activeOverlays = Object.values(overlays).filter((o) => o.enabled).length;
  const grouped = CATEGORY_ORDER.map((category) => ({
    category,
    items: (catalogue?.overlays ?? []).filter((o) => o.category === category),
  })).filter((group) => group.items.length > 0);

  return (
    <div ref={root} className="absolute right-3 top-3 z-30 flex flex-col items-end">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        className={clsx(
          "flex items-center gap-2 rounded-sm border px-2.5 py-1.5 text-2xs transition-colors",
          open
            ? "border-edge-strong bg-surface-raised text-ink"
            : "border-edge bg-surface-panel/95 text-ink-muted backdrop-blur-sm hover:text-ink",
        )}
      >
        <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
          <path d="m12 2 9 5-9 5-9-5 9-5Z" />
          <path d="m3 12 9 5 9-5" />
          <path d="m3 17 9 5 9-5" />
        </svg>
        Layers
        {activeOverlays > 0 && (
          <span className="tabular rounded-full bg-brand-bright px-1.5 text-[9px] font-semibold text-surface-base">
            {activeOverlays}
          </span>
        )}
        <svg
          width="10"
          height="10"
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          strokeWidth="3"
          className={clsx("transition-transform", open && "rotate-180")}
        >
          <path d="m6 9 6 6 6-6" />
        </svg>
      </button>

      {open && (
        <div className="mt-1.5 flex max-h-[calc(100vh-8rem)] w-64 flex-col rounded-sm border border-edge bg-surface-panel/97 shadow-panel backdrop-blur-sm">
          <div className="min-h-0 flex-1 overflow-y-auto">
            <div className="sticky top-0 z-10 border-b border-edge bg-surface-panel/97 px-3 py-2 backdrop-blur-sm">
              <div className="label">Map layers</div>
              <p className="mt-0.5 text-[9px] leading-tight text-ink-faint">
                {catalogue?.attribution ?? "Loading…"}
              </p>
            </div>

            {grouped.length === 0 && (
              <p className="px-3 py-3 text-2xs text-ink-faint">No overlays available.</p>
            )}

            {grouped.map((group) => (
              <div key={group.category} className="border-b border-edge-faint px-1.5 py-1.5">
                <div className="px-2 pb-1 text-[9px] font-medium uppercase tracking-wider text-ink-faint/70">
                  {OVERLAY_CATEGORY_LABEL[group.category] ?? group.category}
                </div>
                {group.items.map((overlay) => (
                  <OverlayRow key={overlay.id} overlay={overlay} />
                ))}
              </div>
            ))}
          </div>

          <div className="shrink-0 border-t border-edge">
            <div className="label px-3 py-2">Fires &amp; projections</div>
            <div className="px-1.5 pb-1.5">
              {DATA_LAYERS.map((layer) => (
                <div key={layer.key}>
                  <Toggle
                    label={layer.label}
                    hint={layer.hint}
                    checked={layers[layer.key]}
                    onChange={() => toggleLayer(layer.key)}
                  />
                  {layer.key === "projections" && layers.projections && <ProjectionKindPicker />}
                </div>
              ))}
              <div className="mt-1 border-t border-edge-faint pt-1">
                <Toggle
                  label="Unmatched heat spots"
                  hint="Isolated detections DBSCAN rejected as noise — industry, agricultural burns, glint"
                  checked={showNoise}
                  onChange={toggleNoise}
                  disabled={!layers.detections}
                />
              </div>
            </div>
          </div>

          <div className="shrink-0 border-t border-edge px-3 py-2">
            <div className="label mb-1.5">Background map</div>
            <div className="flex overflow-hidden rounded-sm border border-edge">
              {(["dark", "satellite"] as const).map((id) => (
                <button
                  key={id}
                  type="button"
                  onClick={() => setBasemap(id)}
                  className={clsx(
                    "flex-1 px-2 py-1 text-2xs capitalize transition-colors",
                    basemap === id
                      ? "bg-surface-raised text-brand-bright"
                      : "text-ink-faint hover:text-ink",
                  )}
                >
                  {id === "dark" ? "Plain" : "Satellite"}
                </button>
              ))}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

/**
 * Which slice of the ensemble to draw. The three are genuinely different
 * claims, and conflating them is how a projection becomes a promise.
 */
const PROJECTION_KINDS: Array<{ id: ProjectionKind; label: string; hint: string }> = [
  {
    id: "envelope",
    label: "Could reach",
    hint: "Everywhere any of the nine cases reach. Plan against this one.",
  },
  {
    id: "core",
    label: "Most likely",
    hint: "The area every one of the nine cases burns. Usually much smaller than people expect.",
  },
  {
    id: "scenarios",
    label: "Compare",
    hint: "Each case drawn separately, so you can see which assumption is driving the shape.",
  },
];

function ProjectionKindPicker() {
  const { projectionKind, setProjectionKind } = useUi();
  return (
    <div className="flex gap-1 px-2 pb-1.5 pl-7">
      {PROJECTION_KINDS.map((kind) => (
        <button
          key={kind.id}
          type="button"
          title={kind.hint}
          onClick={() => setProjectionKind(kind.id)}
          className={clsx(
            "rounded-sm px-1.5 py-0.5 text-[9px] transition-colors",
            projectionKind === kind.id
              ? "bg-surface-raised text-brand-bright"
              : "text-ink-faint hover:text-ink-muted",
          )}
        >
          {kind.label}
        </button>
      ))}
    </div>
  );
}

/** One overlay: a toggle, and — once on — the opacity slider for it. */
function OverlayRow({ overlay }: { overlay: Overlay }) {
  const { overlays, toggleOverlay, setOverlayOpacity } = useUi();
  const state = overlays[overlay.id];
  const enabled = Boolean(state?.enabled);
  const opacity = state?.opacity ?? overlay.default_opacity;

  return (
    <div className={clsx("rounded-sm", enabled && "bg-surface-raised/40")}>
      <Toggle
        label={overlay.title}
        // The `why` is the useful half: a list of ten unexplained toggles is a
        // list nobody turns anything on in.
        hint={`${overlay.description}\n\n${overlay.why}${
          overlay.technical_name ? `\n\nTechnical name: ${overlay.technical_name}` : ""
        }`}
        checked={enabled}
        onChange={() => toggleOverlay(overlay.id, overlay.default_opacity)}
      />
      {enabled && (
        <div className="flex items-center gap-2 px-2 pb-1.5 pl-7">
          <input
            type="range"
            min={0}
            max={1}
            step={0.05}
            value={opacity}
            onChange={(e) => setOverlayOpacity(overlay.id, Number(e.target.value))}
            aria-label={`${overlay.title} opacity`}
            className="h-1 flex-1 cursor-pointer appearance-none rounded-full bg-edge-strong accent-brand-bright"
          />
          <span className="tabular w-7 text-right text-[9px] text-ink-faint">
            {Math.round(opacity * 100)}%
          </span>
        </div>
      )}
    </div>
  );
}

function Toggle({
  label,
  hint,
  checked,
  onChange,
  disabled,
}: {
  label: string;
  hint: string;
  checked: boolean;
  onChange: () => void;
  disabled?: boolean;
}) {
  return (
    <button
      type="button"
      onClick={onChange}
      disabled={disabled}
      title={hint}
      className={clsx(
        "flex w-full items-center gap-2 rounded-sm px-2 py-1.5 text-left transition-colors",
        disabled ? "cursor-not-allowed opacity-40" : "hover:bg-surface-raised",
      )}
    >
      <span
        className={clsx(
          "grid h-3.5 w-3.5 shrink-0 place-items-center rounded-[3px] border transition-colors",
          checked ? "border-brand-bright bg-brand-bright" : "border-edge-strong",
        )}
      >
        {checked && (
          <svg width="9" height="9" viewBox="0 0 24 24" fill="none" stroke="#0E0F11" strokeWidth="4">
            <path d="M20 6 9 17l-5-5" />
          </svg>
        )}
      </span>
      <span className="text-2xs leading-tight text-ink-muted">{label}</span>
    </button>
  );
}
