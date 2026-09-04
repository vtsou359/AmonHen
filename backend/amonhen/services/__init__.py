"""The reasoning layer. Everything here is testable without a network.

Roughly in the order the operational picture is built:

    clustering.py     satellite detections -> named incidents (ST-DBSCAN)
    validation.py     is this a wildfire at all, or a factory?
    fire_weather.py   the Canadian FWI system, the one Copernicus EFFIS runs on
    spread.py         FBP rate of spread, with slope added to wind as a vector
    projection.py     a nine-case ensemble of where a fire could go
    exposure.py       what is downwind, how far, how soon
    burn_scar.py      burned area and severity measured from Sentinel-2 dNBR
    fuel_moisture.py  live fuel moisture from Sentinel-2 NDMI
    boundary.py       clipping detections to the actual country polygon
    gazetteer.py      places, distances and bearings

    operations.py     the spine that runs all of the above, in order

Two conventions worth knowing before editing anything here. Modules state their
own limits in their docstrings, including the ones that produced wrong answers
in the past — those notes are load-bearing, not history. And anything the
platform cannot measure is reported as unavailable rather than guessed, because
a fabricated value propagates silently into every number downstream.
"""
