"""Scheduled ingestion.

`scheduler.py` runs the APScheduler job that rebuilds the operational picture on
a timer, so the map is current without anyone pressing Refresh. It calls the
same `services.operations.rebuild()` the API does — there is one code path, and
the button and the timer both use it.
"""
