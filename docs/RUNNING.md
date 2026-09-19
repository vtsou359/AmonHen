# Running Amon Hen

This guide takes you from a computer with nothing installed to Amon Hen open in
your browser, on **macOS**, **Windows** or **Linux**.

You do not need Python, Node.js or any programming tools. Amon Hen runs inside
**Docker**, a free program that packages an application together with
everything it needs. Once Docker is installed, the commands are the same on all
three systems — the one exception, opening a text file, is spelled out where it
comes up.

> **Already have Docker?** Then this is all there is to it:
>
> ```bash
> git clone https://github.com/vtsou359/AmonHen.git
> cd AmonHen
> docker compose up -d
> ```
>
> and open **http://localhost:3000**.

---

## What you need

| | |
|---|---|
| **Computer** | macOS; Windows 10 (version 22H2) or Windows 11 (version 23H2 or later), 64-bit; or Linux. Intel, AMD and Apple processors all work. |
| **Memory** | 8 GB — Docker Desktop's own minimum on Windows. Amon Hen itself uses under 1 GB. |
| **Disk space** | About 5 GB free. Amon Hen's two images take 2.2 GB; the rest is room for Docker's downloads. |
| **Internet** | Needed while it runs — the map, the weather and the satellite data are all fetched online. |

---

## 1. Install Docker

### macOS

1. Download **Docker Desktop** from
   [Docker's Mac page](https://docs.docker.com/desktop/setup/install/mac-install/).
   Choose **Apple silicon** if your Mac has an Apple M-series chip, or **Intel
   chip** otherwise. (Apple menu → *About This Mac* tells you which.)
2. Open the downloaded file, drag Docker into Applications, and start it.
3. Wait until Docker Desktop says it is running.

Use the **Terminal** app for the commands below (press ⌘ Space and type
*Terminal*).

### Windows

1. Download **Docker Desktop** from
   [Docker's Windows page](https://docs.docker.com/desktop/setup/install/windows-install/),
   which also lists the exact Windows versions it supports.
2. Run the installer. When it asks, keep the **WSL 2** option selected — it is
   the recommended one.
3. Restart if asked, then start Docker Desktop and wait until it says it is
   running.

Use **PowerShell** for the commands below: open the Start menu, type
*PowerShell*, press Enter. (The older Command Prompt does not understand all of
them.)

### Linux

1. Install **Docker Engine** by following
   [Docker's instructions for your distribution](https://docs.docker.com/engine/install/).
   They install the **Compose plugin** along with it — you need both.
2. Let your own user run Docker without `sudo`
   ([why and details](https://docs.docker.com/engine/install/linux-postinstall/)):

   ```bash
   sudo usermod -aG docker $USER
   ```

   Then **log out and back in**, or it will not take effect.

If you would rather have the desktop app, Docker Desktop for Linux works too:
[install page](https://docs.docker.com/desktop/setup/install/linux/).

### Check that Docker works

```bash
docker compose version
```

You should see a line starting `Docker Compose version`. If you see an error
instead, find it under [If something goes wrong](#if-something-goes-wrong).

---

## 2. Download Amon Hen

**With Git** (recommended, because updating is then one command):

```bash
git clone https://github.com/vtsou359/AmonHen.git
cd AmonHen
```

Don't have Git? On macOS, typing `git` offers to install it. On Windows,
download it from [git-scm.com](https://git-scm.com/downloads). On Linux, use
your package manager, e.g. `sudo apt install git`.

**Without Git:** open https://github.com/vtsou359/AmonHen, click the green
**Code** button, choose **Download ZIP**, and unzip it. Then open your terminal
in the unzipped folder, which is called `AmonHen-main`:

- **macOS:** type `cd ` (with a space) in Terminal, drag the folder onto the
  window, press Enter.
- **Windows:** open the folder in File Explorer, right-click an empty space and
  choose **Open in Terminal**. On Windows 10, hold Shift while right-clicking
  and choose **Open PowerShell window here**.
- **Linux:** right-click inside the folder and choose **Open in Terminal**.

**Every command from here on is run inside this folder.**

---

## 3. Start it

```bash
docker compose up -d
```

The first time, Docker downloads and builds everything, which takes a few
minutes depending on your connection. After that, starting takes seconds. It is
ready when you see:

```
 Container amonhen-api Started
 Container amonhen-web Started
```

---

## 4. Open it

Go to **http://localhost:3000**.

You should see a list of fires on the left and a map of Greece. An amber bar
across the top saying **Demo data** is expected: until you add a NASA key (see
[Live fire data](#live-fire-data)), the fires are a built-in sample so you can
try everything. The weather, terrain and vegetation are live from the start.

Developers can find the API and its interactive documentation at
http://localhost:8000/docs.

---

## Everyday use

| To… | Run |
|---|---|
| Stop Amon Hen | `docker compose down` |
| Start it again | `docker compose up -d` |
| Check it is running | `docker compose ps` |
| Watch what it is doing | `docker compose logs -f` — press Ctrl+C to stop watching |

Amon Hen keeps running after you close the terminal, and starts again by itself
whenever Docker starts — until you stop it with `docker compose down`. Stopping
loses nothing: downloaded data stays in the `data` folder.

---

## Live fire data

Out of the box the fires are a sample. To see real fires as NASA's satellites
detect them, add a free NASA FIRMS key:

1. Request a key at https://firms.modaps.eosdis.nasa.gov/api/map_key/. It is
   free and issued straight away.

2. Create your settings file:

   ```bash
   cp .env.example .env
   ```

3. Open `.env` in a text editor:

   | Windows | macOS | Linux |
   |---|---|---|
   | `notepad .env` | `open -e .env` | `nano .env` |

   Find the line `AMONHEN_FIRMS_MAP_KEY=` and paste your key straight after the
   `=`, with no spaces:

   ```
   AMONHEN_FIRMS_MAP_KEY=paste-your-key-here
   ```

   Save the file.

4. Apply it:

   ```bash
   docker compose up -d
   ```

   Use exactly this. `docker compose restart` looks as if it should work, but
   it keeps the old settings and quietly ignores the key.

Reload the page. Once every feed is live, the amber bar disappears.

> **About the rest of `.env`.** It lists many more settings, but under Docker
> only two of them take effect: the key above, and the imagery switch below. The
> others are for developers running the backend directly.

---

## Satellite imagery (optional)

Amon Hen can measure each fire's burned area, and how dry the vegetation around
it is, from Sentinel-2 satellite images. This needs no key but adds about
165 MB, so it is off by default. To turn it on:

1. If you do not have a `.env` file yet, create one: `cp .env.example .env`
2. In `.env`, change `AMONHEN_INSTALL_EO=false` to `AMONHEN_INSTALL_EO=true`.
3. Rebuild and start:

   ```bash
   docker compose up -d --build
   ```

   `--build` is needed because this setting is built into Amon Hen rather than
   read when it starts. It takes a minute or two.

   Each time Amon Hen starts with imagery on, its first picture takes longer
   than usual — about 40 seconds with the demo fires — while it reads the
   satellite images for each fire. Until then the fire list says it is loading.

To check it worked, open http://localhost:8000/api/v1/system/status. The
`sentinel2` entry should say `"mode": "live"`.

---

## Updating

```bash
git pull
docker compose up -d --build --renew-anon-volumes
```

`--build` installs anything new the update depends on. `--renew-anon-volumes`
makes Docker reinstall the web interface's packages; without it, Docker keeps
the ones from before the update. Your `.env` and the `data` folder are kept.

If you downloaded a ZIP instead: run `docker compose down` in the old folder,
download and unzip the new version, copy your `.env` file across, and run the
command above in the new folder (skipping `git pull`).

---

## If something goes wrong

First, `docker compose ps`. Both `amonhen-api` and `amonhen-web` should say
**Up**. If one does not, `docker compose logs api` or `docker compose logs web`
usually says why.

> ⚠️ **The logs contain your NASA key**, because NASA's API puts it in every web
> address. Delete it before sharing logs with anyone.

With satellite imagery on, the log repeats `boto3 not available, falling back to
a DummySession`. It can be ignored.

### "port is already allocated"

Another program is already using port 3000 or 8000. Close it, or move Amon Hen
to a different port by editing `docker-compose.yml`. Change only the number on
the **left** of each pair, and always in **two places**:

- **Port 8000 is taken:** change `"127.0.0.1:8000:8000"` to
  `"127.0.0.1:8001:8000"`, **and** change
  `NEXT_PUBLIC_API_BASE: http://localhost:8000/api/v1` to
  `http://localhost:8001/api/v1`.
- **Port 3000 is taken:** change `"127.0.0.1:3000:3000"` to
  `"127.0.0.1:3001:3000"`, **and** change
  `AMONHEN_CORS_ORIGINS: '["http://localhost:3000"]'` to
  `'["http://localhost:3001"]'`. Then open http://localhost:3001 instead.

Then run `docker compose up -d`. If you change only the port, the page opens
with a red **Cannot reach the Amon Hen API** bar, because it is still looking
at the old address.

### "Cannot connect to the Docker daemon" or "error during connect"

Docker is not running. Start Docker Desktop and wait until it says it is
running, then try again. On Linux with Docker Engine: `sudo systemctl start docker`.

### "permission denied while trying to connect to the Docker daemon socket" (Linux)

Your user is not in the `docker` group yet. Run the `usermod` command from
[Linux](#linux) above, then log out and back in.

### "unknown command: docker compose" or "'compose' is not a docker command"

The Compose plugin is missing. On Linux, install the `docker-compose-plugin`
package. On macOS and Windows, reinstall Docker Desktop, which includes it.

### Docker Desktop on Windows complains about WSL or virtualization

Open PowerShell **as administrator** (right-click it in the Start menu), run
`wsl --update`, and restart. If the message says virtualization is disabled, it
has to be switched on in your computer's BIOS/UEFI settings — searching for
your computer model plus "enable virtualization" finds the steps.

### The amber bar says "Last live request failed (HTTP 400)"

NASA rejected your key. Open `.env`, check that `AMONHEN_FIRMS_MAP_KEY` holds
the whole key with no spaces, then run `docker compose up -d`.

Any other reason in brackets — `ConnectTimeout`, for example — means that
service could not be reached. Amon Hen keeps trying, and the bar clears as soon
as a request succeeds.

### The bar still says to set `AMONHEN_FIRMS_MAP_KEY` after you did

- You ran `docker compose restart`. Run `docker compose up -d` instead.
- The file is not really called `.env`. Windows Notepad can save it as
  `.env.txt`; check with `ls`.

### It is very slow on Windows

Docker [recommends](https://docs.docker.com/desktop/features/wsl/best-practices/)
keeping the project inside WSL's own Linux file system instead of on the `C:`
drive. Run `wsl --install -d Ubuntu`, open **Ubuntu** from the Start menu, and
do steps 2–4 there. In Docker Desktop, *Settings → Resources → WSL integration*
must be switched on for Ubuntu.

### "Permission denied" deleting files in `data/cache` (Linux)

Docker Engine runs Amon Hen as the `root` user, so the files it creates — in
`data/cache` and `frontend/public/maplibre` — belong to root. Delete them with
`sudo rm -rf`.

### "Permission denied" in the logs on Fedora, RHEL or another SELinux system

SELinux is stopping the containers reading the project folder. In
`docker-compose.yml`, add `z` to every volume line that starts with `./` — for
example `./backend/amonhen:/app/amonhen:ro,z` and `./frontend:/app:z` — then
run `docker compose up -d`.

### Starting over

If nothing else helps, remove Amon Hen's containers and rebuild them. Your
`.env` and the `data` folder are kept.

```bash
docker compose down -v
docker compose up -d --build
```

---

## Shortcuts for macOS and Linux

The project includes a `Makefile` with shorter names for common commands. `make`
comes with most Linux systems, and with Apple's Command Line Tools on macOS. It
is not available on Windows by default — the `docker compose` commands above do
the same jobs.

| Shortcut | Does |
|---|---|
| `make up` | Starts Amon Hen and prints its addresses |
| `make down` | Stops it |
| `make logs` | Shows what it is doing |
| `make status` | Shows the containers, and which feeds are live |
| `make build-eo` | Rebuilds with satellite imagery on. A later `--build` turns it off again unless `AMONHEN_INSTALL_EO=true` is in `.env`. |
| `make help` | Lists every shortcut |

---

## Removing Amon Hen

```bash
docker compose down -v --rmi local
```

This removes Amon Hen's containers and the images it built. Then delete the
folder. Docker itself uninstalls like any other application.

---

## Putting it on the internet

This guide is about your own machine. To deploy it for other people, see
[DEPLOYING.md](DEPLOYING.md).
