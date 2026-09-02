"""A seed gazetteer of Greek places and exposed elements.

Scope and honesty: this is a *curated seed*, not a national dataset. Coordinates
are settlement centroids good to roughly a hundred metres — fine for naming a
fire and for first-order distance work, not a substitute for authoritative data.

Replace it with the real thing via `python -m amonhen.ingest.import_gazetteer`,
which pulls populated places, hospitals, schools and Natura 2000 sites from
OpenStreetMap and the EEA and loads them into PostGIS. The seed exists so the
platform is useful the moment it starts, before any import has run.

Populations are 2021 census order-of-magnitude figures, used only to rank
exposure, never reported as fact in the UI.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from functools import lru_cache

from amonhen.domain.enums import ExposureKind


@dataclass(frozen=True)
class Place:
    name: str
    name_el: str
    latitude: float
    longitude: float
    kind: ExposureKind
    population: int | None = None
    region: str | None = None


#: Weighted toward the fire-prone regions: Attica, Evia, the Peloponnese,
#: Evros and the large islands.
SEED_PLACES: tuple[Place, ...] = (
    # ---- Attica ---------------------------------------------------------
    Place("Athens", "Αθήνα", 37.9838, 23.7275, ExposureKind.SETTLEMENT, 643452, "Attica"),
    Place("Marathon", "Μαραθώνας", 38.1553, 23.9636, ExposureKind.SETTLEMENT, 8882, "Attica"),
    Place("Varnavas", "Βαρνάβας", 38.2167, 23.9333, ExposureKind.SETTLEMENT, 1200, "Attica"),
    Place("Nea Makri", "Νέα Μάκρη", 38.0847, 23.9808, ExposureKind.SETTLEMENT, 16000, "Attica"),
    Place("Rafina", "Ραφήνα", 38.0225, 24.0064, ExposureKind.SETTLEMENT, 13091, "Attica"),
    Place("Mati", "Μάτι", 38.0728, 24.0022, ExposureKind.SETTLEMENT, 900, "Attica"),
    Place("Penteli", "Πεντέλη", 38.0500, 23.8667, ExposureKind.SETTLEMENT, 8500, "Attica"),
    Place("Kifisia", "Κηφισιά", 38.0736, 23.8103, ExposureKind.SETTLEMENT, 47000, "Attica"),
    Place("Kineta", "Κινέτα", 38.0083, 23.2333, ExposureKind.SETTLEMENT, 1500, "Attica"),
    Place("Megara", "Μέγαρα", 37.9958, 23.3439, ExposureKind.SETTLEMENT, 23032, "Attica"),
    Place("Lavrio", "Λαύριο", 37.7136, 24.0578, ExposureKind.SETTLEMENT, 7078, "Attica"),
    # ---- Central Greece & Evia -----------------------------------------
    Place("Chalkida", "Χαλκίδα", 38.4638, 23.5990, ExposureKind.SETTLEMENT, 59125, "Central Greece"),
    Place("Aliveri", "Αλιβέρι", 38.4092, 24.0378, ExposureKind.SETTLEMENT, 5279, "Central Greece"),
    Place("Istiaia", "Ιστιαία", 38.9522, 23.1497, ExposureKind.SETTLEMENT, 4796, "Central Greece"),
    Place("Lamia", "Λαμία", 38.9000, 22.4333, ExposureKind.SETTLEMENT, 52006, "Central Greece"),
    Place("Karpenisi", "Καρπενήσι", 38.9125, 21.7936, ExposureKind.SETTLEMENT, 7644, "Central Greece"),
    Place("Delphi", "Δελφοί", 38.4824, 22.4951, ExposureKind.CULTURAL_HERITAGE, None, "Central Greece"),
    # ---- Peloponnese ----------------------------------------------------
    Place("Patra", "Πάτρα", 38.2466, 21.7346, ExposureKind.SETTLEMENT, 173600, "Western Greece"),
    Place("Corinth", "Κόρινθος", 37.9407, 22.9573, ExposureKind.SETTLEMENT, 30176, "Peloponnese"),
    Place("Kalamata", "Καλαμάτα", 37.0389, 22.1142, ExposureKind.SETTLEMENT, 62409, "Peloponnese"),
    Place("Sparti", "Σπάρτη", 37.0736, 22.4297, ExposureKind.SETTLEMENT, 17408, "Peloponnese"),
    Place("Tripoli", "Τρίπολη", 37.5089, 22.3794, ExposureKind.SETTLEMENT, 30866, "Peloponnese"),
    Place("Pyrgos", "Πύργος", 37.6747, 21.4411, ExposureKind.SETTLEMENT, 24359, "Western Greece"),
    Place("Ancient Olympia", "Αρχαία Ολυμπία", 37.6383, 21.6300, ExposureKind.CULTURAL_HERITAGE, None, "Western Greece"),
    # ---- Northern Greece ------------------------------------------------
    Place("Thessaloniki", "Θεσσαλονίκη", 40.6401, 22.9444, ExposureKind.SETTLEMENT, 317778, "Central Macedonia"),
    Place("Alexandroupoli", "Αλεξανδρούπολη", 40.8476, 25.8744, ExposureKind.SETTLEMENT, 57812, "Eastern Macedonia and Thrace"),
    Place("Soufli", "Σουφλί", 41.1928, 26.2969, ExposureKind.SETTLEMENT, 3818, "Eastern Macedonia and Thrace"),
    Place("Kavala", "Καβάλα", 40.9396, 24.4019, ExposureKind.SETTLEMENT, 54027, "Eastern Macedonia and Thrace"),
    Place("Ioannina", "Ιωάννινα", 39.6650, 20.8537, ExposureKind.SETTLEMENT, 65574, "Epirus"),
    Place("Larissa", "Λάρισα", 39.6390, 22.4191, ExposureKind.SETTLEMENT, 148562, "Thessaly"),
    Place("Volos", "Βόλος", 39.3622, 22.9422, ExposureKind.SETTLEMENT, 86048, "Thessaly"),
    # ---- Islands --------------------------------------------------------
    Place("Rhodes", "Ρόδος", 36.4341, 28.2176, ExposureKind.SETTLEMENT, 50636, "South Aegean"),
    Place("Corfu", "Κέρκυρα", 39.6243, 19.9217, ExposureKind.SETTLEMENT, 24838, "Ionian Islands"),
    Place("Heraklion", "Ηράκλειο", 35.3387, 25.1442, ExposureKind.SETTLEMENT, 179302, "Crete"),
    Place("Chania", "Χανιά", 35.5138, 24.0180, ExposureKind.SETTLEMENT, 62196, "Crete"),
    Place("Apollona", "Απόλλωνα", 36.2500, 27.9333, ExposureKind.SETTLEMENT, 400, "South Aegean"),
    Place("Embonas", "Έμπωνας", 36.2167, 27.8500, ExposureKind.SETTLEMENT, 1200, "South Aegean"),
    Place("Laerma", "Λάερμα", 36.1333, 27.9667, ExposureKind.SETTLEMENT, 500, "South Aegean"),
    Place("Archangelos", "Αρχάγγελος", 36.2167, 28.1167, ExposureKind.SETTLEMENT, 5500, "South Aegean"),
    Place("Lindos", "Λίνδος", 36.0917, 28.0875, ExposureKind.CULTURAL_HERITAGE, 1100, "South Aegean"),
    Place("Samos", "Σάμος", 37.7547, 26.9772, ExposureKind.SETTLEMENT, 6251, "North Aegean"),
    Place("Zakynthos", "Ζάκυνθος", 37.7870, 20.8990, ExposureKind.SETTLEMENT, 11326, "Ionian Islands"),
    # ---- Protected forest -----------------------------------------------
    Place("Dadia-Lefkimi-Soufli Forest", "Δάσος Δαδιάς", 41.1167, 26.1667, ExposureKind.NATURA2000, None, "Eastern Macedonia and Thrace"),
    Place("Mount Parnitha National Park", "Εθνικός Δρυμός Πάρνηθας", 38.1667, 23.7333, ExposureKind.NATURA2000, None, "Attica"),
    Place("Mount Ainos National Park", "Εθνικός Δρυμός Αίνου", 38.1500, 20.6667, ExposureKind.NATURA2000, None, "Ionian Islands"),
    Place("Samaria Gorge", "Φαράγγι Σαμαριάς", 35.3000, 23.9500, ExposureKind.NATURA2000, None, "Crete"),
    Place("Mount Taygetos", "Ταΰγετος", 37.0333, 22.3500, ExposureKind.NATURA2000, None, "Peloponnese"),
    # ---- Critical infrastructure ----------------------------------------
    Place("Evangelismos Hospital", "Νοσοκομείο Ευαγγελισμός", 37.9756, 23.7469, ExposureKind.HOSPITAL, None, "Attica"),
    Place("Attikon Hospital", "Νοσοκομείο Αττικόν", 38.0186, 23.6675, ExposureKind.HOSPITAL, None, "Attica"),
    Place("Megalopoli Power Station", "ΑΗΣ Μεγαλόπολης", 37.4030, 22.1180, ExposureKind.POWER_INFRASTRUCTURE, None, "Peloponnese"),
    Place("Agios Dimitrios Power Station", "ΑΗΣ Αγίου Δημητρίου", 40.3833, 21.9333, ExposureKind.POWER_INFRASTRUCTURE, None, "Western Macedonia"),
)

EARTH_RADIUS_KM = 6371.0088


@lru_cache(maxsize=1)
def _settlements() -> tuple[Place, ...]:
    """Places suitable for *naming* a fire — a fire is not called 'Attikon Hospital'."""
    return tuple(p for p in SEED_PLACES if p.kind in (ExposureKind.SETTLEMENT, ExposureKind.NATURA2000))


def nearest_place(latitude: float, longitude: float, max_km: float = 25.0) -> Place | None:
    """The closest named place, or None if the fire is genuinely in the middle of nowhere."""
    best: Place | None = None
    best_distance = math.inf

    for place in _settlements():
        distance = haversine_km(latitude, longitude, place.latitude, place.longitude)
        if distance < best_distance:
            best, best_distance = place, distance

    return best if best_distance <= max_km else None


def places_within(latitude: float, longitude: float, radius_km: float) -> list[tuple[Place, float]]:
    """Every seeded place inside `radius_km`, nearest first, with its distance."""
    hits = [
        (place, haversine_km(latitude, longitude, place.latitude, place.longitude))
        for place in SEED_PLACES
    ]
    return sorted([(p, d) for p, d in hits if d <= radius_km], key=lambda item: item[1])


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance. Accurate enough at every scale we operate at."""
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    d_phi = math.radians(lat2 - lat1)
    d_lambda = math.radians(lon2 - lon1)
    a = math.sin(d_phi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(d_lambda / 2) ** 2
    return 2 * EARTH_RADIUS_KM * math.asin(math.sqrt(a))


def bearing_deg(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Initial compass bearing from point 1 to point 2, in degrees from north."""
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    d_lambda = math.radians(lon2 - lon1)
    x = math.sin(d_lambda) * math.cos(phi2)
    y = math.cos(phi1) * math.sin(phi2) - math.sin(phi1) * math.cos(phi2) * math.cos(d_lambda)
    return (math.degrees(math.atan2(x, y)) + 360.0) % 360.0


def compass_point(bearing: float) -> str:
    """16-point compass label — how responders actually describe direction."""
    points = (
        "N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE",
        "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW",
    )
    return points[int((bearing + 11.25) % 360 / 22.5)]
