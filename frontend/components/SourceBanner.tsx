"use client";

import { useSystemStatus } from "@/lib/api";

/**
 * The honesty bar.
 *
 * A platform serving demo data looks exactly like one serving live satellite
 * data, and that confusion is the single most dangerous failure mode this
 * system has. So when any feed is on fixtures, we say so, permanently, at the
 * top of the screen — and we say what to do about it.
 */
export function SourceBanner() {
  const { data } = useSystemStatus();
  // The backend decides whether the picture is on demo data; do not re-derive
  // it here. Deriving it from `mode` alone got it wrong: Sentinel-2 reports
  // "fixture" whenever the optional raster extra is not installed, which is the
  // default, so the banner told operators who had already set their FIRMS key
  // to go and set it. `api/routes/system.py` excludes Sentinel-2 for that
  // reason, and the name list below does the same.
  if (!data || data.live_sources) return null;

  const fixtures = data.sources.filter((s) => s.mode === "fixture" && s.name !== "sentinel2");
  if (fixtures.length === 0) return null;

  // Two different situations. A source with no key has never been live, and the
  // fix is to add one. A source that has a key but whose last request failed
  // carries the reason in `note` — telling that operator to "set the key" would
  // send them to change something they have already done.
  const unconfigured = fixtures.filter((s) => !s.note);
  const failing = fixtures.filter((s) => s.note);

  return (
    <div className="flex items-center gap-2 border-b border-severity-moderate/30 bg-severity-moderate/10 px-4 py-1.5 text-2xs text-severity-moderate">
      <svg width="12" height="12" viewBox="0 0 24 24" fill="none" className="shrink-0">
        <path
          d="M12 9v4m0 4h.01M10.29 3.86 1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0Z"
          stroke="currentColor"
          strokeWidth="2"
          strokeLinecap="round"
          strokeLinejoin="round"
        />
      </svg>
      <span>
        <strong className="font-semibold">Demo data.</strong>{" "}
        {unconfigured.length > 0 && (
          <>
            {unconfigured.map((s) => s.name).join(", ")} running on bundled fixtures — set{" "}
            <code className="rounded-sm bg-surface-base/60 px-1 font-mono">AMONHEN_FIRMS_MAP_KEY</code>{" "}
            in <code className="rounded-sm bg-surface-base/60 px-1 font-mono">.env</code> for live
            satellite detections.{" "}
          </>
        )}
        {failing.map((s) => `${s.name}: ${s.note}`).join(" ")}
      </span>
    </div>
  );
}
