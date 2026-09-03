/**
 * Colour lookups shared by the DOM and by deck.gl.
 *
 * deck.gl wants [r,g,b,a] arrays, Tailwind wants class names, and keeping the
 * two in sync by hand is exactly the kind of thing that quietly rots. So hex is
 * the single source of truth here and both forms are derived from it.
 */

import type { BurnSeverity, DangerClass, Severity } from "./types";

export const SEVERITY_HEX: Record<Severity, string> = {
  informational: "#64748B",
  minor: "#38BDF8",
  moderate: "#EAB308",
  major: "#F97316",
  critical: "#F43F5E",
};

export const DANGER_HEX: Record<DangerClass, string> = {
  very_low: "#22C55E",
  low: "#84CC16",
  moderate: "#EAB308",
  high: "#F97316",
  very_high: "#EF4444",
  extreme: "#C026D3",
};

/**
 * Burn severity, as the ground actually looks.
 *
 * A deliberately different ramp from SEVERITY_HEX: that one is a warning scale
 * for an active fire, this one is a record of damage already done. Sharing the
 * palette would have the map say "urgent" about ground that finished burning a
 * week ago. Greens through ochre to black-red, reading as vegetation lost.
 */
export const BURN_SEVERITY_HEX: Record<BurnSeverity, string> = {
  unburned: "#2E7D4F",
  low: "#A3B534",
  moderate_low: "#D99A2B",
  moderate_high: "#C05621",
  high: "#7B1D1D",
};

export const BURN_SEVERITY_ORDER: BurnSeverity[] = [
  "high",
  "moderate_high",
  "moderate_low",
  "low",
];

export const SEVERITY_ORDER: Severity[] = [
  "critical",
  "major",
  "moderate",
  "minor",
  "informational",
];

export const DANGER_LABEL: Record<DangerClass, string> = {
  very_low: "Very low",
  low: "Low",
  moderate: "Moderate",
  high: "High",
  very_high: "Very high",
  extreme: "Extreme",
};

export function hexToRgb(hex: string, alpha = 255): [number, number, number, number] {
  const value = hex.replace("#", "");
  return [
    parseInt(value.slice(0, 2), 16),
    parseInt(value.slice(2, 4), 16),
    parseInt(value.slice(4, 6), 16),
    alpha,
  ];
}

export function severityRgb(severity: Severity, alpha = 255) {
  return hexToRgb(SEVERITY_HEX[severity] ?? SEVERITY_HEX.informational, alpha);
}

/**
 * Colour a detection by how hot it is. FRP is heavy-tailed — a handful of pixels
 * are ten times the median — so a linear ramp would render almost everything the
 * same dull colour. Log scaling spreads the useful range across the palette.
 */
export function frpRgb(frpMw: number | null, alpha = 200): [number, number, number, number] {
  if (frpMw == null) return [120, 130, 155, alpha];
  const t = Math.min(1, Math.log10(Math.max(frpMw, 1) + 1) / Math.log10(600));
  // dim ember -> orange -> white-hot
  const stops: Array<[number, [number, number, number]]> = [
    [0.0, [120, 60, 40]],
    [0.35, [220, 90, 30]],
    [0.7, [252, 176, 64]],
    [1.0, [255, 244, 214]],
  ];
  for (let i = 0; i < stops.length - 1; i++) {
    const [t0, c0] = stops[i];
    const [t1, c1] = stops[i + 1];
    if (t <= t1) {
      const f = (t - t0) / (t1 - t0 || 1);
      return [
        Math.round(c0[0] + (c1[0] - c0[0]) * f),
        Math.round(c0[1] + (c1[1] - c0[1]) * f),
        Math.round(c0[2] + (c1[2] - c0[2]) * f),
        alpha,
      ];
    }
  }
  return [255, 244, 214, alpha];
}

/** Older detections fade, so the eye is drawn to the active front. */
export function ageOpacity(observedAt: string, maxHours = 48): number {
  const ageHours = (Date.now() - new Date(observedAt).getTime()) / 3_600_000;
  return Math.max(0.15, 1 - ageHours / maxHours);
}
