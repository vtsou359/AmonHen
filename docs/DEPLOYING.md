# Deploying Amon Hen

Amon Hen goes online as two pieces: the interface on **Vercel**, and the backend
as a **container** on a host that runs containers (Fly.io, Render, Railway,
Google Cloud Run). [RUNNING.md](RUNNING.md) covers running it on your own
machine; this is about putting it on the internet.

## Why the two pieces are split

Vercel runs backends as functions: started per request, frozen in between, with
a read-only filesystem apart from `/tmp`. The Amon Hen backend is the opposite
shape. It keeps a scheduler polling FIRMS every 15 minutes, holds the
operational picture in memory between rebuilds, and caches upstream responses to
disk.

It *can* be bent into a function — disable the scheduler, point the cache at
`/tmp`, ship `data/` inside the service — but then every cold instance rebuilds
the picture from scratch (about 3 seconds, or 40 with imagery on) and fetches
from NASA again on your key, because `/tmp` is not shared between instances. One
small always-on container keeps all of it working as designed, and costs about
as little.

The frontend is a Next.js app, which is exactly what Vercel is for.

---

## 1. The backend

### The image is self-contained

`backend/Dockerfile` builds **from the repository root**, which is what lets the
image carry `data/` — the Greece boundary polygon and the demo fixtures. Locally
`docker compose` mounts that directory instead, but a hosted container has no
mount, and without the polygon the boundary filter silently switches off: in
live data that meant ~92% of detections were fires in Turkey, Albania, North
Macedonia and Bulgaria.

Build and run it the way your host will — no volumes, no compose:

```bash
docker build -f backend/Dockerfile -t amonhen-api .
docker run --rm -p 8000:8000 amonhen-api
```

Then open http://localhost:8000/api/v1/system/status. If that works, the image
works anywhere.

### What to configure on the host

| Setting | Value |
|---|---|
| Dockerfile | `backend/Dockerfile` |
| Build context | the repository root, not `backend/` |
| Port | it listens on `$PORT` when the platform sets one, otherwise 8000 |
| Health check | `/api/v1/system/health` |
| Memory | 512 MB is enough — it used 191 MB at rest; give it 1 GB with imagery on |
| Instances | exactly one — see below |

Environment variables:

| Variable | Why |
|---|---|
| `AMONHEN_FIRMS_MAP_KEY` | Live fires. Without it you have deployed the demo, and the interface says so. |
| `AMONHEN_CORS_ORIGINS` | `["https://your-app.vercel.app"]` — only needed for option B below. |

Satellite imagery is a **build argument**, not an environment variable: pass
`INSTALL_EO=true` in your platform's build-args field. It adds 165 MB.

### Deploying to Fly.io

[`fly.toml`](../fly.toml) in the repo root carries all of the above. Fly keeps the
build context at the repo root even though the Dockerfile is in `backend/`, which
is what lets the image pick up `data/`.

```bash
fly auth login
fly apps create amon-hen-api            # app names are global; pick a free one,
                                        # then set it as `app` in fly.toml
fly secrets set AMONHEN_FIRMS_MAP_KEY=your-key-here
fly deploy
```

`fly deploy` prints the hostname — `https://amon-hen-api.fly.dev` — which is what
the frontend needs in the next section. Check it before wiring anything up:

```bash
curl https://amon-hen-api.fly.dev/api/v1/system/status
```

`boundary_filter_active` must be `true` and `nasa_firms` should read `"live"`.

Other hosts work the same way — Render, Railway and Cloud Run all take a
Dockerfile path plus a build context, and need the same environment variables.
The thing to check on any of them is the idle behaviour: free tiers usually sleep
after a few minutes, which stops the scheduler and makes the first request after
an idle period slow.

### Run exactly one instance

The scheduler and the in-memory picture both assume a single long-lived process.
Two instances means two schedulers fetching from NASA on the same key, two
different pictures, and a user's Refresh landing on whichever one answers. Set
minimum and maximum instances to 1.

On a platform that scales to zero, the first request after an idle period takes
a few seconds while the picture is rebuilt. That is the design working — the
picture is rebuilt on demand whenever it is older than ten minutes.

### Before you expose it

`POST /api/v1/incidents/refresh` is unauthenticated, forces a full rebuild and
deliberately bypasses every cache — which is why `docker-compose.yml` binds the
API to loopback rather than to every interface.

It cannot be protected with a key, because the Refresh button calls it from the
browser and anything the browser holds is public. Instead it is **rate-limited**:
one forced refresh per `AMONHEN_REFRESH_MIN_INTERVAL_SECONDS` (default 60),
with HTTP 429 and a `Retry-After` header for the rest, so the worst a visitor can
do is one fetch cycle a minute. The scheduled 15-minute ingest is never
throttled, and the interface turns the 429 into "Refreshed moments ago — try
again shortly."

Set it to `0` to switch the limit off, or raise it if your NASA quota is tight.
If you need the endpoint properly closed, put the whole backend behind your
platform's access control.

---

## 2. The frontend on Vercel

[`vercel.json`](../vercel.json) declares a single service, so Vercel builds only
`frontend/`. Push, and the deployment works with no dashboard changes.

> If you would rather not use Vercel's services feature at all, delete
> `vercel.json` and set **Root Directory** to `frontend` in the project settings.
> Same result.

Then tell the interface where the backend is. `NEXT_PUBLIC_API_BASE` is read
**at build time**, so set it in Vercel's environment variables and redeploy —
changing it without a rebuild does nothing.

### Option A — proxy through Vercel (recommended)

Put a rewrite above the catch-all in `vercel.json`:

```json
"rewrites": [
  { "source": "/api/v1/(.*)", "destination": "https://YOUR-BACKEND-HOST/api/v1/$1" },
  { "source": "/(.*)", "destination": { "service": "frontend" } }
]
```

and set `NEXT_PUBLIC_API_BASE=/api/v1`. The browser then talks to its own origin:
no CORS to configure, and the backend's address never appears in the page.

### Option B — call the backend directly

Set `NEXT_PUBLIC_API_BASE=https://your-backend-host/api/v1` on Vercel, and
`AMONHEN_CORS_ORIGINS=["https://your-app.vercel.app"]` on the backend. Add your
preview domains to that list too, or previews will load with no data.

---

## After deploying, check three things

1. **`/api/v1/system/status`** — `live_sources` should be `true`, and
   `boundary_filter_active` **must** be `true`. False means the polygon is
   missing and the map will fill with fires from neighbouring countries.
2. **The amber bar.** "Demo data" means no key reached the backend. "Last live
   request failed (HTTP 400)" means NASA rejected the key.
3. **The fire list.** It says "Loading the current picture…" until the first
   rebuild finishes, then lists fires — or reports that it cannot reach the API.
