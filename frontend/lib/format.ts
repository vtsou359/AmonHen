/** Presentation helpers. Formatting rules live here so the UI stays consistent.
 *
 * Every locale-sensitive call passes an explicit locale. Bare `toLocaleString()`
 * follows the *runtime's* locale, so Node and a Greek-configured browser format
 * the same number as "2,742" and "2.742" respectively — a hydration mismatch
 * waiting to happen, and inconsistent output regardless. Pinning to en-GB keeps
 * the numbers stable; switch it deliberately if the UI is ever localised.
 */

export function formatArea(hectares: number): string {
  if (hectares >= 10_000) return `${(hectares / 1000).toFixed(1)}k ha`;
  if (hectares >= 100) return `${Math.round(hectares).toLocaleString("en-GB")} ha`;
  return `${hectares.toFixed(1)} ha`;
}

/**
 * Lead time, rounded to the precision the estimate actually supports.
 *
 * Printing "487 minutes" implies we know the arrival time to the minute eight
 * hours out, which we emphatically do not. Past two hours we round to the
 * nearest half hour; past six, to the hour.
 */
export function formatLeadTime(minutes: number | null): string {
  if (minutes == null) return "—";
  if (minutes < 60) return `${Math.round(minutes / 5) * 5} min`;
  if (minutes < 360) return `${(Math.round((minutes / 60) * 2) / 2).toFixed(1)} h`;
  return `${Math.round(minutes / 60)} h`;
}

export function formatRelative(iso: string): string {
  const deltaMinutes = (Date.now() - new Date(iso).getTime()) / 60_000;
  if (deltaMinutes < 1) return "just now";
  if (deltaMinutes < 60) return `${Math.round(deltaMinutes)} min ago`;
  if (deltaMinutes < 1440) return `${Math.round(deltaMinutes / 60)} h ago`;
  return `${Math.round(deltaMinutes / 1440)} d ago`;
}

export function formatClock(iso: string): string {
  return new Date(iso).toLocaleTimeString("en-GB", {
    hour: "2-digit",
    minute: "2-digit",
    timeZone: "Europe/Athens",
  });
}

export function titleCase(value: string): string {
  return value.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
}
