"""Amon Hen — fire intelligence for Greece, from open Earth observation.

The package is layered, and the dependency arrows only ever point downward:

    api/        HTTP. Thin: it flattens domain objects onto the wire and does
                no reasoning of its own.
    services/   The reasoning. Clustering, fire weather, spread, projection,
                exposure, burned area, plausibility. This is where the fire
                science lives and where most questions are answered.
    sources/    The outside world. One module per external feed, each of which
                degrades to a bundled fixture rather than failing.
    domain/     The shared vocabulary — entities and enums that every layer
                above agrees on.
    core/       Configuration and logging.

`services/operations.py` is the spine: it runs the whole chain from satellite
detections to a finished operational picture, and reading it top to bottom is
the fastest way to understand how the parts fit together.

Start with docs/ARCHITECTURE.md for the design decisions and
docs/DATA_SOURCES.md for what every feed is and why it was chosen.
"""
