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
  if (!data) return null;

  const fixtures = data.sources.filter((s) => s.mode === "fixture");
  if (fixtures.length === 0) return null;

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
        {fixtures.map((s) => s.name).join(", ")} running on bundled fixtures — set{" "}
        <code className="rounded-sm bg-surface-base/60 px-1 font-mono">AMONHEN_FIRMS_MAP_KEY</code>{" "}
        in <code className="rounded-sm bg-surface-base/60 px-1 font-mono">.env</code> for live
        satellite detections.
      </span>
    </div>
  );
}
