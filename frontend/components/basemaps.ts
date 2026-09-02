import type { StyleSpecification } from "maplibre-gl";

/**
 * Basemaps, both keyless on purpose.
 *
 * The platform's promise is that it works the moment it starts, so requiring a
 * Mapbox or MapTiler token just to see a coastline would break it at the first
 * step. CARTO's dark-matter style and Esri's World Imagery are both free to use
 * with attribution, which is rendered in the map's own attribution control.
 *
 * Both are external services. If Amon Hen is ever deployed somewhere without
 * outbound internet — which is a real constraint for emergency operations —
 * self-host tiles with TileServer GL and point NEXT_PUBLIC_BASEMAP_DARK at it.
 */

export const DARK_STYLE_URL =
  process.env.NEXT_PUBLIC_BASEMAP_DARK ??
  "https://basemaps.cartocdn.com/gl/dark-matter-gl-style/style.json";

export const SATELLITE_STYLE: StyleSpecification = {
  version: 8,
  sources: {
    esri: {
      type: "raster",
      tiles: [
        "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
      ],
      tileSize: 256,
      maxzoom: 18,
      attribution:
        "Imagery &copy; Esri, Maxar, Earthstar Geographics, and the GIS User Community",
    },
    labels: {
      type: "raster",
      tiles: [
        "https://server.arcgisonline.com/ArcGIS/rest/services/Reference/World_Boundaries_and_Places/MapServer/tile/{z}/{y}/{x}",
      ],
      tileSize: 256,
      maxzoom: 18,
    },
  },
  layers: [
    { id: "esri-imagery", type: "raster", source: "esri" },
    { id: "esri-labels", type: "raster", source: "labels", paint: { "raster-opacity": 0.85 } },
  ],
};

/** Greece, framed so the mainland and the big islands are all on screen. */
export const INITIAL_VIEW = {
  longitude: 24.2,
  latitude: 38.3,
  zoom: 6.1,
  pitch: 0,
  bearing: 0,
};
