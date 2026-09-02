/** Thin API client + SWR hooks. */

import useSWR from "swr";
import type {
  FeatureCollection,
  IncidentDetail,
  OverlayCatalogue,
  PictureResponse,
  SystemStatus,
} from "./types";

export const API_BASE =
  process.env.NEXT_PUBLIC_API_BASE ?? "http://localhost:8000/api/v1";

/** An API error that carries its status, so callers can treat 404 specially. */
export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

async function fetcher<T>(path: string): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, { cache: "no-store" });
  if (!response.ok) {
    throw new ApiError(`${response.status} ${response.statusText} — ${path}`, response.status);
  }
  return response.json() as Promise<T>;
}

/**
 * Refresh cadences are matched to how fast the underlying data can actually
 * change. FIRMS publishes every few hours, so polling the picture every 60 s is
 * already far more often than it can differ — it exists to pick up an operator's
 * manual refresh, not to chase satellites.
 */
const PICTURE_REFRESH_MS = 60_000;
const STATUS_REFRESH_MS = 120_000;

export function usePicture() {
  return useSWR<PictureResponse>("/incidents", fetcher, {
    refreshInterval: PICTURE_REFRESH_MS,
    revalidateOnFocus: true,
    keepPreviousData: true,
  });
}

export function useIncident(id: string | null) {
  return useSWR<IncidentDetail>(id ? `/incidents/${id}` : null, fetcher, {
    keepPreviousData: false,
    // An incident can legitimately disappear between rebuilds — it burned out,
    // or re-clustering absorbed it into a neighbour. Retrying that 404 on a
    // loop achieves nothing except filling the console.
    shouldRetryOnError: (error: unknown) =>
      !(error instanceof ApiError && error.status === 404),
  });
}

export function useLayer(name: string, enabled = true) {
  return useSWR<FeatureCollection>(enabled ? `/layers/${name}` : null, fetcher, {
    refreshInterval: PICTURE_REFRESH_MS,
    keepPreviousData: true,
  });
}

export function useSystemStatus() {
  return useSWR<SystemStatus>("/system/status", fetcher, {
    refreshInterval: STATUS_REFRESH_MS,
  });
}

export async function forceRefresh(): Promise<PictureResponse> {
  const response = await fetch(`${API_BASE}/incidents/refresh`, { method: "POST" });
  if (!response.ok) throw new Error("Refresh failed");
  return response.json();
}


/**
 * The Copernicus overlay catalogue.
 *
 * Effectively static — it changes only when we add a layer — so it is fetched
 * once and never revalidated. The tiles themselves come straight from EFFIS.
 */
export function useOverlayCatalogue() {
  return useSWR<OverlayCatalogue>("/layers/overlays", fetcher, {
    revalidateOnFocus: false,
    revalidateIfStale: false,
    revalidateOnReconnect: false,
  });
}


/** Projected fire footprints. `kind` selects envelope / core / per-scenario. */
export function useProjections(kind: "envelope" | "core" | "scenarios", enabled = true) {
  return useSWR<FeatureCollection>(
    enabled ? `/layers/projections?kind=${kind}` : null,
    fetcher,
    { refreshInterval: PICTURE_REFRESH_MS, keepPreviousData: true },
  );
}
