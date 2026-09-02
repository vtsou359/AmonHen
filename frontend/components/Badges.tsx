"use client";

import clsx from "clsx";
import { DANGER_HEX, SEVERITY_HEX } from "@/lib/palette";
import {
  DANGER_HINT,
  DANGER_LABEL,
  SEVERITY_HINT,
  SEVERITY_LABEL,
  STATUS_HINT,
  STATUS_LABEL,
} from "@/lib/labels";
import type { DangerClass, IncidentStatus, Severity } from "@/lib/types";

export function SeverityChip({ severity }: { severity: Severity }) {
  const hex = SEVERITY_HEX[severity];
  return (
    <span
      className="chip"
      title={SEVERITY_HINT[severity]}
      style={{ color: hex, borderColor: `${hex}55`, backgroundColor: `${hex}14` }}
    >
      {SEVERITY_LABEL[severity]}
    </span>
  );
}

export function DangerChip({ danger }: { danger: DangerClass | null }) {
  if (!danger) return null;
  const hex = DANGER_HEX[danger];
  return (
    <span
      className="chip"
      style={{ color: hex, borderColor: `${hex}55`, backgroundColor: `${hex}14` }}
      title={DANGER_HINT}
    >
      {DANGER_LABEL[danger]} danger
    </span>
  );
}

export function StatusDot({ status }: { status: IncidentStatus }) {
  return (
    <span
      className={clsx(
        "h-1.5 w-1.5 shrink-0 rounded-full",
        status === "active" && "animate-pulse-slow bg-severity-critical",
        status === "contained" && "bg-severity-moderate",
        status === "controlled" && "bg-severity-minor",
        (status === "out" || status === "archived") && "bg-ink-faint",
      )}
      title={`${STATUS_LABEL[status] ?? status} — ${STATUS_HINT[status] ?? ""}`}
    />
  );
}
