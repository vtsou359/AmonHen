"use client";

import dynamic from "next/dynamic";
import { usePicture } from "@/lib/api";
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
            Cannot reach the Amon Hen API ({String(error.message)}). Is the backend running on{" "}
            <code className="font-mono">:8000</code>?
          </div>
        )}

        <div className="flex min-h-0 flex-1">
          <section className="w-[300px] shrink-0 border-r border-edge bg-surface-panel">
            <IncidentList incidents={data?.incidents ?? []} />
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
