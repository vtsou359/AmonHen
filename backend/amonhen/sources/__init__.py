"""The outside world. One module per external feed.

    firms.py       NASA FIRMS active fire detections (needs a free key)
    open_meteo.py  weather and forecast, and Copernicus DEM elevation
    elevation.py   terrain: slope and aspect, from Copernicus DEM GLO-90
    landcover.py   CORINE Land Cover 2018, and the fuel model it implies
    sentinel2.py   Sentinel-2 L2A imagery (needs the optional [eo] extra)
    overlays.py    the catalogue of Copernicus EFFIS raster map layers

`base.py` carries the rules every source follows — never crash the platform,
never require a credential to see the product, cache on disk — and documents
the contract for adding a new one.

All of these are keyless except FIRMS, which is deliberate: a fresh clone runs
against live data with at most one free signup.
"""
