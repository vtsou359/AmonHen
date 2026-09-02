/**
 * Plain-language display names.
 *
 * The domain vocabulary stays precise — `informational`, `ffmc`, `envelope` all
 * mean something specific and the backend, the tests and the ontology depend on
 * them. But precision in the data model is not a reason to make a duty officer
 * learn forestry jargon at three in the morning.
 *
 * So translation happens here, once, at the display boundary. Where a technical
 * name is genuinely useful (someone wants to look it up) it goes in the tooltip
 * rather than the label.
 */

import type { DangerClass, Severity, Verdict } from "./types";

/** How bad this fire is. "Informational" is accurate and means nothing to most people. */
export const SEVERITY_LABEL: Record<Severity, string> = {
  informational: "Watch",
  minor: "Minor",
  moderate: "Moderate",
  major: "Serious",
  critical: "Critical",
};

export const SEVERITY_HINT: Record<Severity, string> = {
  informational: "Small and not growing quickly. Worth keeping an eye on.",
  minor: "A real fire, but limited in size and speed.",
  moderate: "Growing steadily, or burning intensely.",
  major: "Large, fast-moving, or both.",
  critical: "Very large and spreading fast.",
};

/** Fire danger, from the Fire Weather Index. */
export const DANGER_LABEL: Record<DangerClass, string> = {
  very_low: "Very low",
  low: "Low",
  moderate: "Moderate",
  high: "High",
  very_high: "Very high",
  extreme: "Extreme",
};

export const DANGER_HINT =
  "Today's fire danger where this fire is burning, on the scale used across Europe. " +
  "It combines heat, dryness, wind and how long it has been without rain.";

/** Status of a fire. */
export const STATUS_LABEL: Record<string, string> = {
  active: "Burning",
  contained: "Held",
  controlled: "Under control",
  out: "Out",
  archived: "Archived",
};

export const STATUS_HINT: Record<string, string> = {
  active: "Satellites saw it burning within the last few hours.",
  contained: "No fresh heat at the edges for over 12 hours. It may still be burning inside.",
  controlled: "Reported under control by responders.",
  out: "No heat detected for two days. Presumed out.",
  archived: "Closed.",
};

/**
 * The six numbers behind the fire danger rating.
 *
 * These are the standard codes of the Canadian FWI system, which is what Europe
 * runs on. The acronyms are meaningless outside forestry, so the label says what
 * the number means and the acronym is kept only as a subtitle.
 */
export const FWI_CODE: Record<string, { label: string; hint: string }> = {
  ffmc: {
    label: "Surface dryness",
    hint: "How dry the leaves and twigs on the surface are. Changes within hours — this is what decides whether a spark catches.",
  },
  dmc: {
    label: "Deeper dryness",
    hint: "How dry the layer of decaying material below the surface is. Changes over days.",
  },
  dc: {
    label: "Drought",
    hint: "How dry the deep soil and heavy logs are. Changes over weeks — this is what makes a fire hard to put out.",
  },
  isi: {
    label: "Spread speed",
    hint: "How fast a fire would move, from wind and surface dryness together.",
  },
  bui: {
    label: "Fuel available",
    hint: "How much material is dry enough to burn.",
  },
  fwi: {
    label: "Overall danger",
    hint: "The headline fire danger number, combining everything above.",
  },
};

/** Kinds of place that can be at risk. */
export const EXPOSURE_LABEL: Record<string, string> = {
  settlement: "Town or village",
  hospital: "Hospital",
  school: "School",
  power_infrastructure: "Power station",
  industrial_site: "Industrial site",
  natura2000: "Nature reserve",
  forest: "Forest",
  cultural_heritage: "Heritage site",
  evacuation_route: "Evacuation route",
};

export function exposureLabel(kind: string): string {
  return EXPOSURE_LABEL[kind] ?? kind.replace(/_/g, " ");
}

/** Overlay groupings in the layer menu. */
export const OVERLAY_CATEGORY_LABEL: Record<string, string> = {
  danger: "Fire danger",
  behaviour: "How fire would spread",
  terrain: "Ground",
  fuel: "What's on the ground",
  exposure: "What's at risk",
  detections: "Cross-check",
};


/**
 * Whether a set of heat detections behaves like a wildfire at all.
 *
 * Satellites see heat, not fire — a steel works and a burning forest look
 * similar from orbit. These labels are worded so an operator understands they
 * are being shown a judgement, not a fact.
 */
export const VERDICT_LABEL: Record<Verdict, string> = {
  wildfire: "Looks like a fire",
  probable: "Probably a fire",
  questionable: "Unclear",
  likely_not_wildfire: "Probably not a fire",
};

export const VERDICT_HINT: Record<Verdict, string> = {
  wildfire: "Behaves like a wildfire: strong heat, daytime peak, and it is moving.",
  probable: "Consistent with a fire, but the evidence is thin.",
  questionable: "Some signs do not fit a wildfire. Worth a look before acting on it.",
  likely_not_wildfire:
    "Behaves more like a factory, flare or other fixed heat source than a fire. Still shown, never hidden — open it to see why.",
};
