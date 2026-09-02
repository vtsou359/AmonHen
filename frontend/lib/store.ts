/** UI state. Data lives in SWR; this is only what the user has clicked. */

import { create } from "zustand";

export type BasemapId = "dark" | "satellite";

/** The toggleable map layers, named once so consumers cannot drift. */
export type LayerKey =
  | "projections"
  | "detections"
  | "perimeters"
  | "incidents"
  | "exposure"
  | "places";

/** Which slice of the ensemble the map draws. */
export type ProjectionKind = "envelope" | "core" | "scenarios";

export interface UiState {
  selectedIncidentId: string | null;
  hoveredIncidentId: string | null;
  basemap: BasemapId;
  layers: Record<LayerKey, boolean>;
  showNoise: boolean;
  /** Hide detections that behave like industry. Off by default, deliberately. */
  hideSuspect: boolean;
  projectionKind: ProjectionKind;
  flyTo: { longitude: number; latitude: number; zoom: number } | null;

  /** Per-overlay visibility and opacity, keyed by overlay id. */
  overlays: Record<string, { enabled: boolean; opacity: number }>;

  select: (id: string | null) => void;
  hover: (id: string | null) => void;
  setBasemap: (id: BasemapId) => void;
  toggleLayer: (key: LayerKey) => void;
  toggleNoise: () => void;
  toggleHideSuspect: () => void;
  setProjectionKind: (kind: ProjectionKind) => void;
  requestFlyTo: (target: UiState["flyTo"]) => void;
  toggleOverlay: (id: string, defaultOpacity: number) => void;
  setOverlayOpacity: (id: string, opacity: number) => void;
}

export const useUi = create<UiState>()((set) => ({
  selectedIncidentId: null,
  hoveredIncidentId: null,
  basemap: "dark",
  layers: {
    // On by default: projecting where fires go is what this platform is for.
    projections: true,
    detections: true,
    perimeters: true,
    incidents: true,
    exposure: true,
    places: false,
  },
  showNoise: false,
  // Off by default on purpose. Hiding suspected non-fires without being asked
  // would eventually hide a real one that happened to look odd, and nobody
  // would know to go looking.
  hideSuspect: false,
  projectionKind: "envelope",
  flyTo: null,
  // Overlays start off. Ten raster layers switched on at once is unreadable,
  // and each one is an upstream request the user did not ask for.
  overlays: {},

  select: (id) => set({ selectedIncidentId: id }),
  hover: (id) => set({ hoveredIncidentId: id }),
  setBasemap: (basemap) => set({ basemap }),
  toggleLayer: (key) =>
    set((state) => ({ layers: { ...state.layers, [key]: !state.layers[key] } })),
  toggleNoise: () => set((state) => ({ showNoise: !state.showNoise })),
  toggleHideSuspect: () => set((state) => ({ hideSuspect: !state.hideSuspect })),
  setProjectionKind: (projectionKind) => set({ projectionKind }),
  requestFlyTo: (flyTo) => set({ flyTo }),

  toggleOverlay: (id, defaultOpacity) =>
    set((state) => {
      const current = state.overlays[id];
      return {
        overlays: {
          ...state.overlays,
          [id]: current
            ? { ...current, enabled: !current.enabled }
            : { enabled: true, opacity: defaultOpacity },
        },
      };
    }),

  setOverlayOpacity: (id, opacity) =>
    set((state) => ({
      overlays: {
        ...state.overlays,
        [id]: { enabled: state.overlays[id]?.enabled ?? true, opacity },
      },
    })),
}));
