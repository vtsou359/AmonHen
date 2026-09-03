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

import type { AreaSource, BurnSeverity, DangerClass, Severity, Verdict } from "./types";

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


/** Fuel models, in words rather than shorthand. */
export const FUEL_LABEL: Record<string, string> = {
  maquis: "dense shrub",
  phrygana: "low scrub",
  pine: "pine forest",
  mixed_forest: "mixed forest",
  agricultural: "farmland",
};


/**
 * How a burnt area was arrived at.
 *
 * These are not two estimates of the same thing at different precisions — they
 * are different measurements. Counting 375 m heat pixels can only ever give a
 * lower bound, because fire that burned between satellite passes leaves no
 * pixel to count. A 20 m burn scar is the ground itself. The UI says which,
 * every time it prints a number of hectares.
 */
export const AREA_SOURCE_LABEL: Record<AreaSource, string> = {
  thermal_pixels: "estimated",
  sentinel2_dnbr: "measured",
};

export const AREA_SOURCE_HINT: Record<AreaSource, string> = {
  thermal_pixels:
    "Counted from satellite heat spots at 375 m. This is a lower bound — anything that burned between passes is not in it.",
  sentinel2_dnbr:
    "Measured from the burn scar in Sentinel-2 imagery at 20 m, by comparing the ground before and after.",
};

/**
 * How hard the ground was hit, from the change in the burn ratio.
 *
 * Standard Key & Benson classes. The labels say what the words mean on the
 * ground rather than repeating the class name, because "moderate-low severity"
 * tells a non-specialist nothing about whether anything survived.
 */
export const BURN_SEVERITY_LABEL: Record<BurnSeverity, string> = {
  unburned: "Untouched",
  low: "Lightly burnt",
  moderate_low: "Partly burnt",
  moderate_high: "Badly burnt",
  high: "Destroyed",
};

export const BURN_SEVERITY_HINT: Record<BurnSeverity, string> = {
  unburned: "No detectable change.",
  low: "Surface fire. Scorched ground, most trees alive.",
  moderate_low: "Understory gone, canopy patchy.",
  moderate_high: "Most vegetation consumed, some structure left standing.",
  high: "Near-total loss of vegetation. Bare, exposed soil, and the ground most at risk of erosion this winter.",
};

/**
 * Live fuel moisture — how much water is in the *living* plants.
 *
 * Distinct from the dryness codes above it in the dossier, which come from
 * weather and describe dead litter. This one is measured from orbit, and it is
 * the thing weather cannot tell you.
 */
export const FUEL_MOISTURE_HINT =
  "How much water is in the living plants around the fire, measured from Sentinel-2 satellite imagery. " +
  "The weather-based dryness numbers above describe dead leaves and litter; this describes the living " +
  "vegetation the fire is heading into, which weather alone cannot tell you.";
