# syntax=docker/dockerfile:1
FROM python:3.12-slim AS base

# uv resolves and installs an order of magnitude faster than pip, which matters
# because scikit-learn and shapely pull a lot of wheels.
COPY --from=ghcr.io/astral-sh/uv:0.5.11 /uv /usr/local/bin/uv

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_SYSTEM_PYTHON=1 \
    UV_COMPILE_BYTECODE=1

# Apply outstanding Debian security updates. The slim base carries a handful of
# HIGH advisories (perl, tar) that Debian has patched but not yet rebuilt into
# the published image. None are reachable from this application, but an
# emergency-response tool should not ship known-fixable CVEs.
RUN apt-get update \
 && apt-get upgrade -y --no-install-recommends \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Dependencies first, in their own layer: source edits then rebuild in seconds
# instead of re-resolving the whole tree.
#
# Installed straight from pyproject.toml rather than a hand-written list, so
# adding a dependency in one place cannot silently leave the image behind.
# The [eo] raster extra is deliberately excluded — see pyproject.toml.
COPY pyproject.toml ./
# Install with uv, then remove pip entirely.
#
# pip is never invoked in this image — uv does the installing at build time and
# the runtime process is uvicorn. Leaving it in ships a package installer, plus
# its vendored copies of msgpack and setuptools, both of which carry HIGH
# advisories that scanners will keep reporting forever. A runtime container has
# no business being able to fetch and execute arbitrary packages anyway.
#
# Note for later: `uv pip` is a standalone binary and does NOT depend on pip, so
# `make test` (which installs pytest into the container) still works.
RUN uv pip install --system --no-cache -r pyproject.toml \
 && uv pip uninstall --system pip 2>/dev/null || true \
 && rm -rf /usr/local/lib/python3.12/site-packages/pip \
           /usr/local/lib/python3.12/site-packages/pip-*.dist-info \
           /usr/local/bin/pip /usr/local/bin/pip3*

COPY amonhen ./amonhen
COPY scripts ./scripts

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=3s --start-period=20s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://localhost:8000/api/v1/system/health',timeout=2).status==200 else 1)"

CMD ["uvicorn", "amonhen.main:app", "--host", "0.0.0.0", "--port", "8000"]
