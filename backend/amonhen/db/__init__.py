"""Persistence. Not wired in yet.

The live operational picture is rebuilt in memory every cycle — a few hundred
objects, so a database round-trip buys nothing and the platform runs before
Postgres is even up. PostGIS is in `docker compose --profile persistence` and
this package is where history will live: growth curves, replay, after-action
review, and scoring the spread model against what actually happened.

See "Persistence" in the README roadmap.
"""
