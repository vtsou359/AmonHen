/**
 * Stage MapLibre's web worker into public/.
 *
 * MapLibre GL v6 loads its worker as a separate ES module resolved from
 * `import.meta.url`. Turbopack does not emit that chunk, so the request 404s,
 * the dev server returns its HTML error page, and the browser refuses it for
 * having a "text/html" MIME type. The visible symptom is a map that reports no
 * error, draws its attribution and controls, and then renders nothing at all —
 * because vector tile parsing happens entirely in that worker.
 *
 * The fix is to serve the worker ourselves and point MapLibre at it with
 * `setWorkerUrl` (see components/MapCanvas.tsx). The worker imports
 * `./maplibre-gl-shared.mjs` relatively, so both files must be copied into the
 * same directory.
 *
 * Runs automatically before `dev` and `build`.
 */

import { copyFileSync, mkdirSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const from = join(here, "..", "node_modules", "maplibre-gl", "dist");
const to = join(here, "..", "public", "maplibre");

const FILES = ["maplibre-gl-worker.mjs", "maplibre-gl-shared.mjs"];

mkdirSync(to, { recursive: true });
for (const file of FILES) {
  copyFileSync(join(from, file), join(to, file));
}
console.log(`[maplibre] staged ${FILES.length} worker files -> public/maplibre/`);
