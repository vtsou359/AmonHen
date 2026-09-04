"use client";

/**
 * The map: a MapLibre basemap with a deck.gl overlay drawn on top.
 *
 * MapLibre renders the basemap and the Copernicus raster overlays; deck.gl
 * renders everything of ours — detections, perimeters, incidents, exposure,
 * projections — into its own canvas above it. They are composed rather than
 * interleaved, which keeps our layers independent of whatever style the
 * basemap happens to be.
 *
 * Layout, roughly top to bottom:
 *
 *   - map init, readiness, and the load-failure handler
 *   - a ResizeObserver, because the container is often 0x0 at mount
 *   - basemap swapping, which discards and re-adds the overlays
 *   - deck.gl layer construction, one block per toggleable layer
 *   - `syncOverlays`, the reconcile for the Copernicus raster layers
 *
 * Several comments in here record behaviour measured against the running app
 * that contradicts what the MapLibre API appears to promise — `isStyleLoaded()`
 * reading false on a working map, `idle` never firing. Each one cost a bug;
 * please read them before "simplifying".
 */
import { useEffect, useMemo, useRef, useState } from "react";
// MapLibre GL v6 is ESM-only and dropped its default export, so the map
// class and controls are imported by name.
import {
  Map as MapLibreMap,
  NavigationControl,
  ScaleControl,
  setWorkerUrl,
  type IControl,
} from "maplibre-gl";
import { MapboxOverlay } from "@deck.gl/mapbox";
// Imported from @deck.gl/layers rather than the `deck.gl` umbrella. The
// umbrella pulls in 3D tiles, glTF/texture loaders, mesh layers and Carto —
// none of which this app uses, and which drag in a vulnerable `image-size`
// transitively. Taking only the subpackages we need drops that whole subtree.
import { GeoJsonLayer, LineLayer, ScatterplotLayer } from "@deck.gl/layers";
import "maplibre-gl/dist/maplibre-gl.css";

import { useLayer, useOverlayCatalogue, useProjections } from "@/lib/api";
import { ageOpacity, frpRgb, hexToRgb, severityRgb } from "@/lib/palette";
import { useUi } from "@/lib/store";
import type { Overlay, Severity } from "@/lib/types";
import { DARK_STYLE_URL, INITIAL_VIEW, SATELLITE_STYLE } from "./basemaps";
import { MapTooltip, type TooltipState } from "./MapTooltip";

// MapLibre v6 resolves its worker from `import.meta.url`, which Turbopack does
// not emit — so we serve the worker ourselves from public/maplibre (staged by
// scripts/copy-maplibre-worker.mjs) and point MapLibre at it. Without this the
// map renders its controls and attribution but never draws a single tile.
setWorkerUrl("/maplibre/maplibre-gl-worker.mjs");

/**
 * How many load failures, with nothing loading successfully in between, before
 * the map admits to a problem.
 *
 * A single failure is routine and self-healing: MapLibre re-requests the tile,
 * and neighbouring tiles usually cover the view meanwhile. Eight in a row is
 * not a blip.
 */
const FAILURES_BEFORE_REPORTING = 8;

/**
 * How long a failure may stand unanswered before it is reported anyway.
 *
 * Covers the case a count cannot: a style that fails once and then simply never
 * loads. Long enough that a single blip on a working map recovers first.
 */
const SILENCE_BEFORE_REPORTING_MS = 4000;

/** How long to wait before retrying an overlay reconcile that did not take. */
const OVERLAY_SYNC_RETRY_MS = 150;
/** ~3 s of retries. Past that the style is not coming and polling is waste. */
const OVERLAY_SYNC_MAX_ATTEMPTS = 20;
/** How long to keep re-applying overlays across a basemap swap. */
const OVERLAY_SETTLE_MS = 5000;
const OVERLAY_SETTLE_INTERVAL_MS = 250;

/** What the overlays *should* be, read live so a deferred reconcile is correct. */
type OverlayRefs = {
  catalogue: { current: Overlay[] | undefined };
  state: { current: Record<string, { enabled: boolean; opacity: number }> };
  time: { current: string };
};

export function MapCanvas() {
  const container = useRef<HTMLDivElement>(null);
  const map = useRef<MapLibreMap | null>(null);
  const overlay = useRef<MapboxOverlay | null>(null);
  const [ready, setReady] = useState(false);
  const [tooltip, setTooltip] = useState<TooltipState | null>(null);
  const [mapError, setMapError] = useState<string | null>(null);

  const {
    basemap, layers, showNoise, selectedIncidentId, select, flyTo, requestFlyTo, overlays,
    projectionKind,
  } = useUi();
  const { data: catalogue } = useOverlayCatalogue();

  // Mirrored into refs because the `styledata` handler is registered once, at
  // map creation, and would otherwise close over the state as it was then.
  const overlayStateRef = useRef(overlays);
  const catalogueRef = useRef<Overlay[] | undefined>(undefined);
  overlayStateRef.current = overlays;
  catalogueRef.current = catalogue?.overlays;
  const timeRef = useRef<string>("");
  timeRef.current = catalogue?.time ?? "";
  // Bundled once. The refs themselves are stable, so a handler registered at map
  // creation still reads current values through this.
  const overlayRefs = useRef<OverlayRefs>({
    catalogue: catalogueRef,
    state: overlayStateRef,
    time: timeRef,
  }).current;

  const detections = useLayer("detections", layers.detections);
  const perimeters = useLayer("perimeters", layers.perimeters);
  const incidents = useLayer("incidents", layers.incidents);
  const exposure = useLayer("exposure", layers.exposure);
  const projections = useProjections(projectionKind, layers.projections);

  // ---------------------------------------------------------------- map init
  useEffect(() => {
    if (!container.current || map.current) return;

    const instance = new MapLibreMap({
      container: container.current,
      style: basemap === "dark" ? DARK_STYLE_URL : SATELLITE_STYLE,
      center: [INITIAL_VIEW.longitude, INITIAL_VIEW.latitude],
      zoom: INITIAL_VIEW.zoom,
      attributionControl: { compact: true },
      // Keeps the globe from wrapping into an infinite scroll of Greeces.
      renderWorldCopies: false,
    });

    instance.addControl(new NavigationControl({ showCompass: true }), "bottom-right");
    instance.addControl(new ScaleControl({ unit: "metric" }), "bottom-left");

    const deck = new MapboxOverlay({ interleaved: false, layers: [] });
    instance.addControl(deck as unknown as IControl);

    // Readiness drives a veil over the whole map, so getting it wrong is
    // expensive: a missed signal leaves "Loading terrain" permanently covering a
    // map that is rendering perfectly underneath.
    //
    // `load` is the documented event but fires exactly once and is easy to miss
    // (a Strict Mode remount, a cached style). `idle` is unreliable here for a
    // different reason — the deck.gl overlay repaints continuously, so the map
    // may never actually go idle. `render` fires on the first painted frame and
    // is the one signal that cannot be missed, so it is the real backstop.
    const markReady = () => setReady(true);
    instance.on("load", markReady);
    instance.once("idle", markReady);
    instance.once("render", markReady);

    // Safety net for style changes we did not initiate. The authoritative
    // re-add across a deliberate basemap swap is `settleOverlays`, in the
    // basemap effect below.
    instance.on("styledata", () => {
      syncOverlays(instance, overlayRefs);
    });

    // A basemap that fails silently is worse than one that fails loudly: the
    // operator sees an empty ocean and has no idea whether that means "no fires"
    // or "no tiles". But the converse is a real cost too, and the first version
    // of this handler paid it. MapLibre fires `error` once per failed *tile*,
    // and a tile fails for reasons that fix themselves — a dropped connection, a
    // CDN hiccup, a pan that outran the network. Treating each one as fatal put
    // a red "Basemap failed to load" banner over a map that was working
    // perfectly, and sent the operator off to check their network.
    //
    // What this does NOT do is try to classify the error. Three ways to tell a
    // fatal failure from a tile blip were tried against the running app and all
    // three are unreliable: `isStyleLoaded()` returns false *permanently* on a
    // map that is rendering coastlines and labels; "has a frame been painted" is
    // unobservable, because the deck.gl overlay draws to its own canvas and
    // MapLibre emits no `render` after the first paint; and `sourceId` is
    // declared only on source-data events, not on error events.
    //
    // Persistence is observable, so persistence is the test. One failure is
    // noise. Failures that keep coming with no source loading in between are an
    // outage, whatever caused them — which is the thing worth telling the
    // operator, and the only thing they can act on.
    let failures = 0;
    let reported = false;
    let verdictTimer: ReturnType<typeof setTimeout> | null = null;

    const recovered = () => {
      failures = 0;
      if (verdictTimer) {
        clearTimeout(verdictTimer);
        verdictTimer = null;
      }
      if (reported) {
        reported = false;
        setMapError(null);
      }
    };

    const report = (message: string) => {
      reported = true;
      setMapError(message);
    };

    // Any source finishing its network requests is proof the map is being fed.
    instance.on("sourcedata", (event) => {
      if (event.isSourceLoaded) recovered();
    });

    instance.on("error", (event) => {
      const message = event.error?.message ?? "failed to load";
      failures += 1;
      // eslint-disable-next-line no-console
      console.warn(`[maplibre] load failure ${failures}:`, message);

      if (failures >= FAILURES_BEFORE_REPORTING) {
        report("tiles are not loading — check the network connection");
        return;
      }

      // A style that never loads can fail exactly once and then sit there, so a
      // count alone would let the worst case pass in silence. If nothing has
      // loaded successfully a few seconds after the first failure, say so.
      if (!verdictTimer) {
        verdictTimer = setTimeout(() => {
          verdictTimer = null;
          if (failures > 0) report(message);
        }, SILENCE_BEFORE_REPORTING_MS);
      }
    });

    map.current = instance;
    overlay.current = deck;

    // The map lives in a flex column, so its container can still be measuring
    // when the Map is constructed — MapLibre then falls back to a 400x300
    // canvas and simply never renders, showing a permanent "Loading terrain".
    // Watching the container and calling resize() makes that unreproducible,
    // and also keeps the map correct when the side panels open and close.
    // The map is constructed inside a flex column, so on first paint its
    // container is frequently still 0x0. MapLibre then falls back to an 800x600
    // canvas and renders nothing at all — the style loads, tiles are fetched,
    // and the screen stays black. Nothing recovers on its own, because MapLibre
    // only re-measures when told to.
    //
    // So: resize on every observation that reports a real size, including the
    // very first. (An earlier version skipped the first notification, which is
    // precisely the one carrying the post-layout dimensions — that reintroduced
    // the blank map it was meant to prevent.)
    const observer = new ResizeObserver((entries) => {
      const box = entries[0]?.contentRect;
      if (!box || box.width === 0 || box.height === 0) return;
      instance.resize();
    });
    observer.observe(container.current);

    return () => {
      observer.disconnect();
      if (verdictTimer) clearTimeout(verdictTimer);
      instance.remove();
      map.current = null;
      overlay.current = null;
    };
    // Basemap switching is handled separately; re-running this would tear down
    // the whole map and lose the user's pan/zoom.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // ------------------------------------------------------------ basemap swap
  useEffect(() => {
    if (!map.current || !ready) return;
    const instance = map.current;
    instance.setStyle(basemap === "dark" ? DARK_STYLE_URL : SATELLITE_STYLE);

    // setStyle discards every source and layer we added, so the overlays have
    // to be rebuilt. Both obvious hooks are wrong, for opposite reasons.
    //
    // `styledata` fires while the outgoing style is still partially in place, so
    // `getLayer` finds the *old* overlay, the reconcile takes its "already
    // present" branch, and the swap then wipes the layer for good.
    //
    // `idle` was the fix for that and is worse: it **never fires on this map**.
    // Measured across a basemap switch, zero `idle` events in twenty seconds —
    // because, as the readiness comment above already records, the deck.gl
    // overlay repaints continuously. So every basemap switch silently threw
    // away whatever Copernicus layers the user had turned on.
    //
    // Reconciling repeatedly across the swap depends on no event at all. It
    // costs a handful of no-op passes and is the only version measured to work.
    settleOverlays(instance, overlayRefs);
  }, [basemap, ready]);

  // ---------------------------------------------------------------- fly-to
  useEffect(() => {
    if (!map.current || !flyTo) return;
    map.current.flyTo({
      center: [flyTo.longitude, flyTo.latitude],
      zoom: flyTo.zoom,
      duration: 1200,
      essential: true,
    });
    requestFlyTo(null);
  }, [flyTo, requestFlyTo]);

  // ---------------------------------------------------------------- layers
  const deckLayers = useMemo(() => {
    const built: unknown[] = [];

    // Projections first, so they sit under the observed data. What a fire has
    // actually done must never be obscured by what it might do.
    if (layers.projections && projections.data) {
      built.push(
        new GeoJsonLayer({
          id: "projections",
          data: projections.data,
          filled: true,
          stroked: true,
          // Opacity falls off with the horizon: the 6 h band is the least
          // certain claim on the map and should read as the faintest.
          getFillColor: (f: any) => {
            const [r, g, b] = PROJECTION_RGB;
            return [r, g, b, horizonAlpha(f.properties.minutes as number)];
          },
          getLineColor: (f: any) => {
            const [r, g, b] = PROJECTION_RGB;
            return [r, g, b, Math.min(255, horizonAlpha(f.properties.minutes as number) + 90)];
          },
          getLineWidth: 1.2,
          lineWidthUnits: "pixels",
          pickable: true,
          updateTriggers: { getFillColor: [projectionKind], getLineColor: [projectionKind] },
          onHover: ({ object, x, y }: any) =>
            setTooltip(object ? { kind: "projection", x, y, properties: object.properties } : null),
        }),
      );
    }

    if (layers.perimeters && perimeters.data) {
      built.push(
        new GeoJsonLayer({
          id: "perimeters",
          data: perimeters.data,
          filled: true,
          stroked: true,
          // Two kinds of shape share this layer and must not look alike.
          //
          // A detection hull is a convex sketch around 375 m heat pixels: faint
          // fill, thin line, "approximate" said by the styling so a legend does
          // not have to. A Sentinel-2 dNBR scar is a 20 m measurement of ground
          // that actually burned: solid fill and a firm edge. Drawing them the
          // same would be the map asserting a precision it does not have.
          getFillColor: (f: any) =>
            severityRgb(
              (f.properties.severity as Severity) ?? "informational",
              f.properties.is_measured ? 90 : 38,
            ),
          getLineColor: (f: any) =>
            severityRgb((f.properties.severity as Severity) ?? "informational", 235),
          getLineWidth: (f: any) => {
            const selected = f.properties.incident_id === selectedIncidentId;
            const base = f.properties.is_measured ? 2.5 : 1.5;
            return selected ? base + 1.5 : base;
          },
          lineWidthUnits: "pixels",
          pickable: true,
          updateTriggers: {
            getFillColor: [selectedIncidentId],
            getLineWidth: [selectedIncidentId],
          },
          onClick: ({ object }: any) => object && select(object.properties.incident_id),
        }),
      );
    }

    if (layers.detections && detections.data) {
      const features = detections.data.features.filter(
        (f) => showNoise || !f.properties.is_noise,
      );
      built.push(
        new ScatterplotLayer({
          id: "detections",
          data: features,
          getPosition: (f: any) => f.geometry.coordinates,
          // Radius tracks FRP but with a floor, so a cool pixel is still visible
          // and a 600 MW pixel does not swallow the screen.
          getRadius: (f: any) =>
            220 + Math.sqrt(Math.max(f.properties.frp_mw ?? 1, 1)) * 90,
          getFillColor: (f: any) => {
            const [r, g, b] = frpRgb(f.properties.frp_mw as number | null);
            const alpha = Math.round(230 * ageOpacity(f.properties.observed_at as string));
            return [r, g, b, f.properties.is_noise ? Math.round(alpha * 0.4) : alpha];
          },
          radiusUnits: "meters",
          radiusMinPixels: 1.5,
          radiusMaxPixels: 26,
          pickable: true,
          onHover: ({ object, x, y }: any) =>
            setTooltip(object ? { kind: "detection", x, y, properties: object.properties } : null),
        }),
      );
    }

    if (layers.exposure && exposure.data) {
      built.push(
        new ScatterplotLayer({
          id: "exposure",
          data: exposure.data.features,
          getPosition: (f: any) => f.geometry.coordinates,
          getRadius: 900,
          radiusUnits: "meters",
          radiusMinPixels: 4,
          radiusMaxPixels: 12,
          stroked: true,
          filled: true,
          getFillColor: (f: any) =>
            f.properties.minutes_to_impact != null ? [244, 63, 94, 190] : [138, 151, 180, 130],
          getLineColor: [255, 255, 255, 200],
          lineWidthMinPixels: 1,
          pickable: true,
          onHover: ({ object, x, y }: any) =>
            setTooltip(object ? { kind: "exposure", x, y, properties: object.properties } : null),
        }),
      );
    }

    if (layers.incidents && incidents.data) {
      // Spread vectors: a line from the incident toward the predicted head.
      const vectors = incidents.data.features
        .filter((f) => f.properties.spread_direction_deg != null)
        .map((f) => {
          const [lon, lat] = (f.geometry as any).coordinates as [number, number];
          const bearing = ((f.properties.spread_direction_deg as number) * Math.PI) / 180;
          // One hour of head spread, in degrees.
          const km = ((f.properties.head_ros_m_per_min as number) * 60) / 1000;
          return {
            from: [lon, lat],
            to: [
              lon + (km * Math.sin(bearing)) / (111.32 * Math.cos((lat * Math.PI) / 180)),
              lat + (km * Math.cos(bearing)) / 111.32,
            ],
            severity: f.properties.severity as Severity,
          };
        });

      built.push(
        new LineLayer({
          id: "spread-vectors",
          data: vectors,
          getSourcePosition: (d: any) => d.from,
          getTargetPosition: (d: any) => d.to,
          getColor: (d: any) => severityRgb(d.severity, 210),
          getWidth: 2.5,
          widthUnits: "pixels",
        }),
      );

      built.push(
        new ScatterplotLayer({
          id: "incidents",
          data: incidents.data.features,
          getPosition: (f: any) => f.geometry.coordinates,
          getRadius: (f: any) => 1400 + Math.sqrt(f.properties.area_ha ?? 1) * 55,
          radiusUnits: "meters",
          radiusMinPixels: 7,
          radiusMaxPixels: 42,
          stroked: true,
          filled: false,
          getLineColor: (f: any) => severityRgb(f.properties.severity as Severity, 255),
          getLineWidth: (f: any) => (f.properties.id === selectedIncidentId ? 4 : 2),
          lineWidthUnits: "pixels",
          pickable: true,
          updateTriggers: { getLineWidth: [selectedIncidentId] },
          onClick: ({ object }: any) => object && select(object.properties.id),
          onHover: ({ object, x, y }: any) =>
            setTooltip(object ? { kind: "incident", x, y, properties: object.properties } : null),
        }),
      );
    }

    return built;
  }, [
    layers, detections.data, perimeters.data, incidents.data, exposure.data,
    projections.data, projectionKind, showNoise, selectedIncidentId, select,
  ]);

  useEffect(() => {
    overlay.current?.setProps({ layers: deckLayers as never[] });
  }, [deckLayers]);

  // Add, remove and re-opacity the Copernicus raster overlays.
  useEffect(() => {
    if (!map.current || !ready) return;
    syncOverlays(map.current, overlayRefs);
    // `catalogue` and `overlays` are read through refs inside, but they must
    // still be dependencies: they are what tells React to re-run this at all.
  }, [catalogue, overlays, ready, overlayRefs]);

  return (
    <div className="relative h-full w-full">
      <div ref={container} className="h-full w-full" />
      {!ready && (
        <div className="absolute inset-0 grid place-items-center bg-surface-base">
          <div className="flex items-center gap-3 text-ink-muted">
            <span className="h-2 w-2 animate-pulse rounded-full bg-brand-bright" />
            <span className="text-sm">Loading terrain…</span>
          </div>
        </div>
      )}
      {mapError && (
        <div className="pointer-events-none absolute left-1/2 top-3 z-30 -translate-x-1/2 rounded-sm border border-severity-critical/40 bg-severity-critical/15 px-3 py-1.5 text-2xs text-severity-critical backdrop-blur-sm">
          Basemap: {mapError}
        </div>
      )}
      {tooltip && <MapTooltip state={tooltip} />}
    </div>
  );
}

//: Amber. Deliberately not a severity colour — a projection is a hypothesis,
//: and must not be mistaken for an observation.
const PROJECTION_RGB: [number, number, number] = [249, 140, 40];

/** Later horizons are less certain, so they are drawn fainter. */
function horizonAlpha(minutes: number): number {
  if (minutes <= 60) return 78;
  if (minutes <= 180) return 46;
  return 26;
}

const OVERLAY_PREFIX = "amonhen-overlay-";

/**
 * Reconcile the map's raster layers with the requested overlay state.
 *
 * Written as a plain reconcile rather than add/remove callbacks because one code
 * path has to serve three cases: the user toggling a layer, the opacity slider
 * moving, and the entire style being replaced under us by a basemap switch.
 *
 * Stacking order comes out right for free: overlays are added after the basemap
 * style loads, and deck.gl draws above every MapLibre layer — so it is always
 * basemap → Copernicus overlays → fire data.
 */
/**
 * Add, remove and re-opacity the Copernicus raster overlays.
 *
 * Reads the desired state from refs rather than taking it as values, because
 * this can be *deferred* — see below — and a deferred call must act on what the
 * user wants now, not on what they wanted when it was scheduled.
 */
/**
 * Re-apply the overlays repeatedly for a few seconds.
 *
 * For use across a `setStyle`, which discards every source and layer we added
 * and provides no reliable signal for when it has finished doing so. A single
 * reconcile can land before the swap completes and be wiped; this keeps putting
 * them back until the new style stops taking them away.
 */
function settleOverlays(map: MapLibreMap, want: OverlayRefs) {
  const until = Date.now() + OVERLAY_SETTLE_MS;
  const tick = () => {
    try {
      syncOverlays(map, want);
    } catch {
      return; // map removed
    }
    if (Date.now() < until) window.setTimeout(tick, OVERLAY_SETTLE_INTERVAL_MS);
  };
  tick();
}

function syncOverlays(map: MapLibreMap, want: OverlayRefs, attempt = 0) {
  const catalogue = want.catalogue.current;
  const state = want.state.current;
  // Supplied by the backend. EFFIS defaults time-aware layers to 2019 and
  // returns a blank tile without a date, so this is not optional.
  const time = want.time.current;
  if (!catalogue) return;

  let deferred = false;

  for (const overlay of catalogue) {
    const id = `${OVERLAY_PREFIX}${overlay.id}`;
    const wanted = state[overlay.id];
    // Reconciled one layer at a time inside try/catch, so a style that really is
    // mid-swap costs a retry rather than taking the whole set down with it.
    try {
      const present = Boolean(map.getLayer(id));

      if (wanted?.enabled && !present) {
        if (!map.getSource(id)) {
          map.addSource(id, {
            type: "raster",
            tiles: [overlay.tile_url.replace("{time}", time)],
            tileSize: 256,
            attribution: overlay.attribution,
          });
        }
        map.addLayer({ id, type: "raster", source: id, paint: { "raster-opacity": wanted.opacity } });
      } else if (!wanted?.enabled && present) {
        map.removeLayer(id);
        if (map.getSource(id)) map.removeSource(id);
      } else if (wanted?.enabled && present) {
        map.setPaintProperty(id, "raster-opacity", wanted.opacity);
      }
    } catch {
      deferred = true;
    }
  }

  // Retry only what actually failed, and only for a bounded time.
  //
  // This used to be guarded up front by `!map.isStyleLoaded()`, which silently
  // broke the entire overlay feature: ticking a layer updated the checkbox and
  // the legend, this returned early, and no tile was ever added — with nothing
  // to retry it. The flag is not the precondition it looks like. Measured
  // against this running map it reads false for minutes at a time while
  // `addSource` and `addLayer` both succeed, so asking MapLibre to do the work
  // and handling the rare refusal is the honest test. (`once("idle")` is not an
  // option for the retry either: as the readiness comment above records, the
  // deck.gl overlay repaints continuously and this map may never go idle.)
  if (deferred && attempt < OVERLAY_SYNC_MAX_ATTEMPTS) {
    window.setTimeout(() => {
      try {
        syncOverlays(map, want, attempt + 1);
      } catch {
        // The map was removed while we waited. Nothing to reconcile.
      }
    }, OVERLAY_SYNC_RETRY_MS);
  }
}

