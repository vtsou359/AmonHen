"""Generate the bundled demo dataset.

The goal is a scenario that behaves like a real fire week in Greece, so that
every downstream component — clustering, area estimation, growth rates, exposure
ranking — is exercised honestly rather than flattered by tidy synthetic input.

That means reproducing the awkward parts of real FIRMS data:

* Detections arrive in *bursts* at satellite overpasses, not continuously. The
  clustering has to bridge 6-12 hour gaps, which is why eps_hours is generous.
* Fires drift downwind and elongate rather than growing as circles.
* Confidence is mixed, FRP is heavy-tailed, and some pixels are plain noise
  (sun glint, industrial heat, agricultural burns).

Run:  python scripts/make_fixtures.py
"""

from __future__ import annotations

import json
import math
import random
from datetime import UTC, datetime, timedelta
from pathlib import Path

random.seed(20240811)  # reproducible demo

REPO_ROOT = Path(__file__).resolve().parents[2]
OUT = REPO_ROOT / "data" / "fixtures" / "firms_detections.json"

# Anchor the scenario at a fixed instant; the loader time-shifts it to "now"
# so the demo always shows a fire that is happening today.
ANCHOR = datetime(2024, 8, 11, 18, 0, tzinfo=UTC)

SATELLITES = ["viirs_noaa20", "viirs_noaa21", "viirs_snpp", "modis_terra"]


def overpasses(start: datetime, hours: int) -> list[datetime]:
    """Approximate VIIRS/MODIS revisit times over Greece.

    Four polar orbiters give roughly four useful passes a day, clustered around
    local mid-morning and early afternoon plus two night passes.
    """
    times: list[datetime] = []
    for day in range(hours // 24 + 2):
        base = (start + timedelta(days=day)).replace(minute=0, second=0, microsecond=0)
        for hour in (0, 1, 10, 11, 12, 21, 22):
            t = base.replace(hour=hour) + timedelta(minutes=random.randint(0, 55))
            if start <= t <= start + timedelta(hours=hours):
                times.append(t)
    return sorted(times)


PIXEL_KM = 0.375


def snap_to_sensor_grid(lat: float, lon: float) -> tuple[float, float]:
    """Quantise a position onto a ~375 m grid, as VIIRS pixel centres are."""
    d_lat = PIXEL_KM / 111.32
    d_lon = d_lat / math.cos(math.radians(lat))
    return (round(lat / d_lat) * d_lat, round(lon / d_lon) * d_lon)


def make_fire(
    origin: tuple[float, float],
    start_offset_h: float,
    duration_h: float,
    drift_bearing_deg: float,
    drift_km_per_h: float,
    peak_frp: float,
    pixels_per_pass: tuple[int, int],
    elongation: float = 2.5,
) -> list[dict]:
    """One fire: a front that drifts downwind and leaves a widening scar."""
    lat0, lon0 = origin
    ignition = ANCHOR - timedelta(hours=duration_h) + timedelta(hours=start_offset_h)
    detections: list[dict] = []

    for pass_time in overpasses(ignition, int(duration_h)):
        age_h = (pass_time - ignition).total_seconds() / 3600.0
        if age_h < 0:
            continue

        # Fires ramp up, plateau, then decay as they are contained.
        intensity = math.sin(math.pi * min(age_h / duration_h, 1.0)) ** 0.6
        if intensity < 0.05:
            continue

        # The head of the fire has moved downwind by now.
        travelled_km = drift_km_per_h * age_h
        bearing = math.radians(drift_bearing_deg)
        head_lat = lat0 + (travelled_km * math.cos(bearing)) / 111.32
        head_lon = lon0 + (travelled_km * math.sin(bearing)) / (
            111.32 * math.cos(math.radians(lat0))
        )

        count = int(random.randint(*pixels_per_pass) * intensity) + 1
        for _ in range(count):
            # Place pixels along the burnt corridor, denser near the head.
            along = random.betavariate(2.2, 1.4)          # 0 = origin, 1 = head
            spread_km = 0.4 + 0.9 * math.sqrt(max(age_h, 0.1))
            across = random.gauss(0, spread_km / elongation)

            lat = lat0 + ((head_lat - lat0) * along) + (across * math.cos(bearing + math.pi / 2)) / 111.32
            lon = lon0 + ((head_lon - lon0) * along) + (across * math.sin(bearing + math.pi / 2)) / (
                111.32 * math.cos(math.radians(lat0))
            )

            # FRP is heavy-tailed: most pixels modest, a few very hot at the head.
            frp = round(random.lognormvariate(math.log(peak_frp * 0.18), 0.85) * (0.4 + along), 1)
            frp = min(frp, peak_frp * 1.4)

            # Real VIIRS reports pixel centres on a fixed ~375 m grid, so
            # consecutive passes re-detect the *same* cells over a still-burning
            # area. Generating continuous positions instead would make every
            # detection a fresh cell and inflate burned-area estimates several
            # fold. Snapping here keeps the demo honest about that.
            lat, lon = snap_to_sensor_grid(lat, lon)

            is_day = 6 <= pass_time.hour <= 17
            detections.append(
                {
                    "latitude": round(lat, 5),
                    "longitude": round(lon, 5),
                    "observed_at": pass_time.isoformat(),
                    "satellite": random.choice(SATELLITES),
                    "frp_mw": frp,
                    "brightness_k": round(random.gauss(335 if is_day else 320, 18), 1),
                    "confidence": random.choices(
                        ["high", "nominal", "low"], weights=[0.55, 0.38, 0.07]
                    )[0],
                    "scan_km": 0.375 if "viirs" in SATELLITES[0] else 0.42,
                    "is_daytime": is_day,
                }
            )
    return detections


def make_noise(count: int) -> list[dict]:
    """Isolated false-positive-ish pixels: industry, agricultural burns, glint.

    These exist so DBSCAN has something to correctly reject. A demo where every
    pixel belongs to a fire teaches the wrong lesson about the data.
    """
    out: list[dict] = []
    for _ in range(count):
        out.append(
            {
                "latitude": round(random.uniform(35.2, 41.4), 5),
                "longitude": round(random.uniform(20.2, 28.2), 5),
                "observed_at": (ANCHOR - timedelta(hours=random.uniform(0, 40))).isoformat(),
                "satellite": random.choice(SATELLITES),
                "frp_mw": round(random.uniform(0.6, 7.5), 1),
                "brightness_k": round(random.gauss(305, 8), 1),
                "confidence": random.choices(["low", "nominal"], weights=[0.75, 0.25])[0],
                "scan_km": 0.375,
                "is_daytime": True,
            }
        )
    return out


SCENARIO = [
    # The headline event: a large Attica fire north of Marathon running
    # south-west on a northerly, threatening Marathon and Nea Makri.
    dict(origin=(38.2210, 23.9410), start_offset_h=0, duration_h=34,
         drift_bearing_deg=215, drift_km_per_h=0.62, peak_frp=430,
         pixels_per_pass=(28, 46), elongation=2.9),
    # A second, younger Attica fire, west of Parnitha. Deliberately placed far
    # enough from the Varnavas run that the two must stay separate clusters —
    # in the first draft it sat at Penteli and the drifting fronts overlapped,
    # so DBSCAN correctly (but unhelpfully) merged them into one incident.
    dict(origin=(38.1720, 23.4980), start_offset_h=19, duration_h=15,
         drift_bearing_deg=190, drift_km_per_h=0.34, peak_frp=180,
         pixels_per_pass=(9, 18), elongation=2.2),
    # Evia, moderate and slowing.
    dict(origin=(38.4180, 24.0510), start_offset_h=6, duration_h=27,
         drift_bearing_deg=140, drift_km_per_h=0.30, peak_frp=210,
         pixels_per_pass=(10, 20), elongation=2.4),
    # Western Peloponnese, near Ancient Olympia.
    dict(origin=(37.6510, 21.6480), start_offset_h=12, duration_h=21,
         drift_bearing_deg=95, drift_km_per_h=0.26, peak_frp=140,
         pixels_per_pass=(6, 13), elongation=2.0),
    # Rhodes, small and nearly out.
    dict(origin=(36.2480, 27.9310), start_offset_h=2, duration_h=17,
         drift_bearing_deg=250, drift_km_per_h=0.18, peak_frp=75,
         pixels_per_pass=(3, 8), elongation=1.8),
]


def main() -> None:
    detections: list[dict] = []
    for fire in SCENARIO:
        detections.extend(make_fire(**fire))
    detections.extend(make_noise(22))
    detections.sort(key=lambda d: d["observed_at"])

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(
        json.dumps(
            {
                "anchor": ANCHOR.isoformat(),
                "description": (
                    "Synthetic FIRMS-like active fire detections over Greece. "
                    "Five fires plus noise, structured around realistic satellite "
                    "overpass times. Time-shifted to the present on load."
                ),
                "detections": detections,
            },
            indent=1,
        )
    )
    print(f"wrote {len(detections)} detections -> {OUT.relative_to(REPO_ROOT)}")
    for fire in SCENARIO:
        print(f"  fire at {fire['origin']}  drift {fire['drift_bearing_deg']}deg")


if __name__ == "__main__":
    main()
