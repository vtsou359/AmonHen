/**
 * Wire types, mirroring `backend/amonhen/api/schemas.py`.
 *
 * Kept hand-written rather than generated, because there are few enough of them
 * that a generator would be more machinery than it saves. If this grows, run
 * `openapi-typescript` against /openapi.json instead of expanding this file.
 */

export type Severity = "informational" | "minor" | "moderate" | "major" | "critical";
export type IncidentStatus = "active" | "contained" | "controlled" | "out" | "archived";
export type DangerClass = "very_low" | "low" | "moderate" | "high" | "very_high" | "extreme";

export interface IncidentSummary {
  id: string;
  name: string;
  status: IncidentStatus;
  severity: Severity;
  latitude: number;
  longitude: number;
  estimated_area_ha: number;
  growth_rate_ha_per_hour: number;
  max_frp_mw: number;
  detection_count: number;
  first_detected_at: string;
  last_detected_at: string;
  danger_class: DangerClass | null;
  top_threat: string | null;
  minutes_to_top_threat: number | null;
  verdict: Verdict;
  verdict_score: number;
}

export type Verdict = "wildfire" | "probable" | "questionable" | "likely_not_wildfire";

export interface PlausibilitySummary {
  verdict: Verdict;
  score: number;
  reasons: string[];
  signals: Record<string, number>;
}

export interface SourceStatus {
  name: string;
  mode: "live" | "fixture";
  attribution: string;
  homepage: string;
  requires_credentials: boolean;
}

export interface PictureResponse {
  generated_at: string;
  area_of_interest: string;
  active_count: number;
  total_incidents: number;
  total_area_ha: number;
  unclustered_detections: number;
  dropped_outside_boundary: number;
  suspect_count: number;
  incidents: IncidentSummary[];
  sources: SourceStatus[];
}

export interface FwiSummary {
  ffmc: number;
  dmc: number;
  dc: number;
  isi: number;
  bui: number;
  fwi: number;
  danger_class: DangerClass;
}

export interface SpreadSummary {
  head_ros_m_per_min: number;
  flank_ros_m_per_min: number;
  back_ros_m_per_min: number;
  length_to_breadth: number;
  direction_deg: number;
  direction_label: string;
  fuel_model: string;
  confidence: string;
  caveats: string[];
}

export interface ExposedElement {
  name: string;
  kind: string;
  latitude: number;
  longitude: number;
  population: number | null;
  distance_km: number | null;
  bearing_deg: number | null;
  minutes_to_impact: number | null;
  is_downwind: boolean | null;
}

export interface WeatherObservation {
  observed_at: string;
  temperature_c: number | null;
  relative_humidity_pct: number | null;
  wind_speed_kmh: number | null;
  wind_direction_deg: number | null;
  wind_gust_kmh: number | null;
  precipitation_mm: number | null;
}

export interface IncidentDetail {
  incident: IncidentSummary & {
    total_frp_mw: number;
    region: string | null;
    notes: string | null;
  };
  perimeter: { geometry: GeoJSON.Geometry; area_ha: number; method: string; confidence: number } | null;
  weather: WeatherObservation | null;
  danger: FwiSummary | null;
  spread: SpreadSummary | null;
  exposed: ExposedElement[];
  projection: ProjectionSummary | null;
  plausibility: PlausibilitySummary | null;
  brief: string;
  detection_count: number;
}

export interface FeatureCollection {
  type: "FeatureCollection";
  features: Array<{
    type: "Feature";
    geometry: GeoJSON.Geometry;
    properties: Record<string, unknown>;
  }>;
  generated_at?: string;
  attribution?: string;
}

export interface SystemStatus {
  status: "ok" | "degraded";
  environment: string;
  area_of_interest: string;
  bbox: [number, number, number, number];
  live_sources: boolean;
  sources: SourceStatus[];
  picture_generated_at: string | null;
}


export type OverlayCategory =
  | "danger"
  | "behaviour"
  | "terrain"
  | "fuel"
  | "exposure"
  | "detections";

/** A Copernicus EFFIS raster overlay, as described by GET /layers/overlays. */
export interface Overlay {
  id: string;
  title: string;
  /** EFFIS WMS layer name; empty for non-EFFIS sources such as the hillshade. */
  layer: string;
  /** The formal name, for anyone who wants to look the layer up. */
  technical_name: string;
  category: OverlayCategory;
  description: string;
  /** The decision this layer supports — shown on hover. */
  why: string;
  time_aware: boolean;
  default_opacity: number;
  source: string;
  attribution: string;
  /** MapLibre raster template. `{time}` must be substituted by the client. */
  tile_url: string;
  /** Empty when the source has no legend service (e.g. the hillshade). */
  legend_url: string;
}

export interface OverlayCatalogue {
  attribution: string;
  wms_endpoint: string;
  /** Date to request for time-aware layers, decided by the backend. */
  time: string;
  overlays: Overlay[];
}


export interface ScenarioSummary {
  id: string;
  label: string;
  rationale: string;
  fuel: string;
  head_ros_m_per_min: number;
  direction_deg: number;
  direction_label: string;
  areas_ha: Record<string, number>;
}

export interface ThreatSummary {
  name: string;
  kind: string;
  latitude: number;
  longitude: number;
  distance_km: number;
  population: number | null;
  hit_count: number;
  scenario_count: number;
  /** Fraction of the ensemble that reaches this place — not a probability. */
  likelihood: number;
  earliest_minutes: number | null;
  median_minutes: number | null;
  scenarios_hit: string[];
}

export interface TerrainSummary {
  elevation_m: number;
  slope_pct: number;
  aspect_deg: number;
  relief_m: number;
  /** Plain words — "hilly", "steep". */
  descriptor: string;
  source: string;
}

export interface LandCoverSummary {
  code: string;
  label: string;
  /** null when nothing here can carry a fire. */
  fuel: string | null;
  burnable: boolean;
  source: string;
}

export interface ProjectionSummary {
  incident_id: string;
  generated_at: string;
  horizons_minutes: number[];
  scenarios: ScenarioSummary[];
  envelope_areas_ha: Record<string, number>;
  threats: ThreatSummary[];
  terrain: TerrainSummary | null;
  land_cover: LandCoverSummary | null;
  caveats: string[];
}
