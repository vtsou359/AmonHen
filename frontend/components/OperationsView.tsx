"use client";

/**
 * The whole screen: top bar, incident list, map, dossier.
 *
 * The only component that fetches the operational picture. Everything below it
 * either reads from SWR's cache by calling the same hook (which dedupes) or
 * takes what it needs as props, so there is exactly one request for the picture
 * no matter how many panels want it.
 */
import dynamic from "next/dynamic";
import { API_BASE, usePicture } from "@/lib/api";
import { IncidentDossier } from "./IncidentDossier";
import { IncidentList } from "./IncidentList";
import { LayerControl } from "./LayerControl";
import { MapLegends } from "./MapLegends";
import { SourceBanner } from "./SourceBanner";
import { TopBar } from "./TopBar";

// MapLibre and deck.gl both touch `window` at import time, so the map is
// client-only. Without this, `next build` fails during static prerendering.
const MapCanvas = dynamic(() => import("./MapCanvas").then((m) => m.MapCanvas), {
  ssr: false,
  loading: () => <div className="h-full w-full bg-surface-base" />,
});

export function OperationsView() {
  const { data, error, mutate } = usePicture();

  return (
    <div className="flex h-full w-full">
      <div className="flex min-w-0 flex-1 flex-col">
        <TopBar picture={data} onRefreshed={() => mutate()} />
        <SourceBanner />

        {error && (
          <div className="border-b border-severity-critical/30 bg-severity-critical/10 px-4 py-2 text-2xs text-severity-critical">
            {/* The address comes from the build, not a literal: after a port
                change (docs/RUNNING.md) a hard-coded ":8000" pointed at the wrong
                place. */}
            Cannot reach the Amon Hen API ({String(error.message)}). Is the backend running at{" "}
            <code className="font-mono">{API_BASE}</code>?
          </div>
        )}

        <div className="flex min-h-0 flex-1">
          <section className="w-[300px] shrink-0 border-r border-edge bg-surface-panel">
            {/* Passed through as-is, not `?? []`: "no picture yet" and "no fires"
                are different answers, and the list says which. */}
            <IncidentList incidents={data?.incidents} unreachable={Boolean(error) && !data} />
          </section>

          <main className="relative min-w-0 flex-1">
            <MapCanvas />
            <LayerControl />
            <MapLegends />
          </main>

          <IncidentDossier />
        </div>
      </div>
    </div>
  );
}
