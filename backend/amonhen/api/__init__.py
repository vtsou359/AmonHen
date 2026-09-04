"""HTTP layer.

Deliberately thin. Routes fetch an already-built operational picture from
`services.operations`, flatten it into the response models in `api.schemas`,
and return it. No fire science happens here — if a route starts computing
something, that computation belongs in `services/`.
"""
