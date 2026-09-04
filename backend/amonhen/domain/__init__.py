"""The shared vocabulary.

`entities.py` holds plain Pydantic models with no storage or HTTP concerns, and
`enums.py` the controlled vocabularies. Both are the single source of truth for
what a word like "contained" or "major" means, and the enums are exported to the
frontend at /api/v1/system/ontology so the two sides cannot drift apart.

Storage and transport derive from these, never the other way round.
"""
