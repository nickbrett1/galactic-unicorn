# galactic-unicorn

A galactic-unicorn project generated with genproj

## Capabilities

This project includes the following capabilities:

- **Editor Configuration**: Shared VS Code extensions and workspace settings for consistent tooling across the team.
- **Shell & Terminal**: Zsh shell with the Powerlevel10k prompt and productivity plugins.
- **Doppler Secrets Management**: Integrates Doppler for secure secrets management. Enables the various MCP servers that rely on privileged tokens to access their services (e.g. CircleCI, GitHub, SonarQube).
- **AI Coding Agents**: Sets up the AI coding agents in the devcontainer: goose (config, MCP servers and spec-first recipes) plus the Antigravity CLI.
- **Container Agent**: Every generated devcontainer brings up and registers its own a2a-goose agent (`<repo>-dev`), reached over the tailnet by the LiteLLM proxy; reuses the a2a-goose GitHub release channel, so the container has the same self-update path as a host.
- **Docker**: Adds Docker support for containerised builds and tooling.
- **Python DevContainer**: Sets up a VS Code DevContainer with Python environment.
- **Ruff (Python code quality)**: Adds fast, zero-configuration Python linting with Ruff (rules live in pyproject.toml [tool.ruff]). Lint locally with `ruff check`. Requires a Python devcontainer. The generated CI pipeline also runs `ruff check`.
- **MicroPython board**: Adds the MicroPython toolchain (mpremote) and USB passthrough for driving an RP2-series board (RP2040 or RP2350) from the devcontainer.
- **Buildkite Integration**: Runs CI on a self-hosted Buildkite agent (Apple silicon) instead of a metered cloud fleet. The pipeline and its GitHub webhook are created during generation, so there is no manual "set up project" step. Can run alongside CircleCI, so a repository can migrate without a flag day.
- **GitHub Releases**: Publishes a GitHub Release with attached artifacts when a version is cut. The release step lives in the Buildkite pipeline, so it runs only after build and test passed on that commit: the version is bumped from the last tag, the tag is created by CI, the artifacts named by the project's own scripts/release-artifacts.sh are attached, and the release is published with generated notes. No version bookkeeping, and no PAT for a human to paste in.

## Setup

1. Clone the repository and open it in the devcontainer — the MicroPython
   toolchain lives inside it (there is no host-side install to keep in sync).
2. Run the linter over the firmware:

   ```bash
   ruff check .
   ```

Firmware runs on the board, not on the host, so there is no host test step —
see "MicroPython board" for flashing and running it.

## Doppler

This project uses Doppler for secrets from the shared `common` project
(config `dev`) — no per-repo Doppler project is created. First use (links
the shared project and `dev` config):

```bash
doppler setup --project common --config dev
```

If your repo needs app-specific secrets that shouldn't live in the shared
`common` project, regenerate it with the doppler capability set to
`projectStrategy: "new"` to get a dedicated project.

The Doppler CLI is installed in the devcontainer — it must be on PATH for the
VS Code extension and `doppler run` to work. Auth is persisted via the host
`~/.doppler` bind-mount.

### Env-var precedence (read this if `doppler run` hits the wrong project)

Doppler resolves its target as **environment variables > `doppler.yaml` >
`~/.doppler` scoped config**. If your shell — or the session that launched
the devcontainer (e.g. an agent runtime) — exports `DOPPLER_PROJECT` /
`DOPPLER_CONFIG` / `DOPPLER_ENVIRONMENT`, those silently override this
repo's `doppler.yaml` and every `doppler` command targets the wrong
project. The devcontainer's post-create setup pins this repo's context
(`common`/`dev`) in `~/.bashrc` and `~/.zshrc` and warns at
setup if resolution still mismatches. To force the correct context manually:

```bash
unset DOPPLER_PROJECT DOPPLER_CONFIG DOPPLER_ENVIRONMENT
doppler setup --no-interactive --project common --config dev
```

## The container's agent

This devcontainer brings up its own `a2a-goose` agent, registered in the hub as
`galactic-unicorn-dev` - one agent per repo, so a restart reclaims the same entry
instead of adding a second one. Turns are billed through the LiteLLM proxy
configured in Doppler (`LITELLM_BASE_URL`).

```bash
scripts/agent-dev.sh start    # write secrets + config, fetch the launcher, run it
scripts/agent-dev.sh status   # running or not, the card URL, the log tail
scripts/agent-dev.sh stop     # SIGTERM, wait for a clean deregister, confirm gone
```

`start` runs from the devcontainer's post-start hook, so the agent is normally
already up when you arrive. It fails open: with no network on a first start it
prints why it did not start and leaves the project usable. Secrets come from
Doppler into `~/.config/a2a-goose/env` (mode 0600) and never into the image or
`containerEnv`.

## MicroPython board

This repo targets the **Pimoroni Galactic Unicorn**. Its firmware lives at https://github.com/pimoroni/unicorn. The Galactic Unicorn has shipped with both RP2040 and RP2350 silicon, so this product name does **not** decide the chip - that is recorded separately below.

**Chip / build:** **RP2040** - install the **Pico W / RP2040** MicroPython build (`RPI_PICO_W`). A Pico 2 W (RP2350) build will not run on it.

### Read the chip from the board, not the cable

The RP2 variant is the one thing here that must not be guessed. It is **not**
in the product name, it is **not** implied by the USB PID - `2e8a:0005`
identifies the MicroPython CDC firmware class and says nothing about whether
the silicon is RP2040 or RP2350. Ask the board:

```bash
mpremote connect "$(scripts/find-board.sh)" exec 'import os; print(os.uname().machine)'
# e.g. "Raspberry Pi Pico W with RP2040"
```

If that line and the `chip` in this repo's generator configuration disagree,
believe the board. Choosing the wrong `.uf2` fails at flash time with no
warning that the product name was ever the cause.

The toolchain lives **inside the devcontainer** — there is no host-side install
to keep in sync. OrbStack forwards the board's CDC-ACM REPL into the Linux VM
**automatically**, so you do **not** need `orb usb attach` for this device. A
container, however, only sees the node when the device is granted explicitly,
which this devcontainer does.

The board keeps its macOS node name inside the container
(`/dev/tty.usbmodem<serial>`), which **changes with the USB port**, so never
hard-code it. Resolve it at runtime:

```bash
mpremote connect "$(scripts/find-board.sh)" exec 'print(1+1)'   # smoke test -> 2
```

`scripts/find-board.sh` enumerates candidate ports and **probes** each one (a
MicroPython REPL answers immediately) rather than guessing from a count; more
than one `tty.usbmodem` node can be present.

### Access is exclusive

While the container holds the port, host tools such as Thonny (or a host-side
`mpremote`) cannot open it, and vice versa. Close Thonny before probing from
the container. The port is released when the container stops.

### The device grant is deliberately broad

This devcontainer grants the whole character-device cgroup class
(`--device-cgroup-rule=c *:* rmw`) and binds the host `/dev` in
(`--volume=/dev:/dev`). That is **not** scoped to the serial port: the
container can open any host character device. Scoping it is not expressible —
OrbStack assigns the node a *dynamic* character major, and the rule grammar
accepts only a single major or `*`, never a range or list — so a guessed
major fails with a silent `EPERM` on a node that looks perfectly present. It
is still strictly narrower than `--privileged`; set `deviceAccess:
"privileged"` only if the cgroup-rule mechanism stops working on a future
OrbStack. To pin a single node by hand (and accept that the devcontainer only
opens while the board is attached), replace the two runArgs with
`--device=/dev/tty.usbmodem<serial>`.

## Weather on the idle screen

When no routine is running, the panel shows the current NYC weather: a
condition glyph plus the temperature in Celsius — e.g. a cloud and `14C`.

- **Source:** Open-Meteo over **plain HTTP** (`config.WEATHER_URL`), by lat/lon
  (Manhattan); change `WEATHER_LATITUDE`/`WEATHER_LONGITUDE` to move it. The
  board cannot complete a TLS handshake, so the source must speak plain HTTP;
  Open-Meteo needs no API key and answers with a ~200-byte JSON body.
- **Cadence:** refreshed every `WEATHER_POLL_MS` (15 min); a failure retries
  after `WEATHER_RETRY_MS` and is written to `weather.log` on the device.
- **Idle only:** the fetch (which may join WiFi) runs only while the panel is
  idle — never during PROMPT, COUNTDOWN or HANDOFF — so it can never share the
  thread with a running timer. It is bounded (socket timeout + byte cap) and
  joins WiFi at most once per attempt, exactly like the remote poll.
- **Degrades cleanly:** with no reading yet, or with `WEATHER_ENABLED = False`,
  the clock shows instead (fallback only).
- **Survives the dark-room rule:** the clock and the breathing pixel go dark in
  a dark room, but the weather stays (with the power lamp). The phototransistor
  reads a shaded desk as dark while the room is lit — this board measures a
  steady `light() = 17` against `LIGHT_DARK = 40`, which is what once made a
  working feature look dead on the wall. Set `AMBIENT_WEATHER_IN_DARK = False`
  to restore "dark room = lamp only".
- **Brightness:** the weather draws at `BRIGHTNESS_WEATHER` (0.55), not the
  dimmer AMBIENT (0.10) used for the clock and the status pixel — 0.10 was too
  dim to read across a room.
- **Dims after sunset:** the weather is on the panel permanently, so at the
  daytime level it is a small lamp glowing in a dark room all evening. Once the
  sun is down it drops to `BRIGHTNESS_WEATHER_NIGHT` (0.22). "After dark" is the
  sun, not the phototransistor — the sensor reads a shaded desk as dark in a lit
  room, so keying off it would dim the panel at noon behind a cushion. The board
  asks the weather source instead (`is_day` in `config.WEATHER_URL`), which is
  the real sunset for the location and rides along free in the request the board
  already makes. A reading without the flag is treated as day, so a missing
  field can only ever leave the panel at its normal brightness.
- **Night sky after sunset:** once the sun is down, the two conditions whose
  glyph is a *light source* — `sun` and `partly` — swap their sun for a moon
  (`_NIGHT`: a crescent with a scatter of stars; `_PARTLY_NIGHT`: the same
  crescent behind the cloud). Open-Meteo reports code 0 (clear) all night, so
  without this the panel drew a gold sun at midnight. Every other condition
  (cloud, rain, snow, fog, thunder) is already honest after dark and keeps its
  glyph — a wet night is still rain. Set `AMBIENT_NIGHT_SKY = False` to get the
  sun back.

The conditions are drawn from `lib/icons.py` (`WEATHER_ICONS`), mapped from
Open-Meteo's WMO codes by `lib/weather.py:classify` — sun, partly cloudy,
cloud, fog, rain, snow and thunder, plus the two after-sunset glyphs (`night`
and `partly-night`) the renderer reaches for once `is_day` is 0. All are drawn
as filled silhouettes, and
every cloud shares one rule — a **flat base with humps on top**. A bare
silhouette is hard to read at 11 px: an oval tapers into a teardrop, a slab
reads as a brick, and the rain cloud without its drops reads as a blob.

Every glyph is **shaded** the same way, with the same ink vocabulary: a mid
**body** (`'X'`), a **lit** top (`'L'`) and a **shaded** underside (`'S'`), plus
an **accent** ink (`'O'`) for the one element that needs a different colour —
the rim on the cloud, the sun in `partly`, the drops in `rain`, the flakes in
`snow`, the bolt in `thunder`, the stars in `night`. Each condition has its own
small palette in `ambient.WEATHER_PENS` (body, lit, shade, accent), so the icons
read as a lit, colour-coded set rather than a row of flat blue stamps. The two
night glyphs have their palettes in `ambient.NIGHT_PENS` — the moon is a cool
blue-white rather than the sun's gold, so the panel reads as night at a glance;
`ambient.pens_for` is the lookup that both tables go through.

The exception is the **sun**, which is flat: all four of its inks are the same
gold. A light source should not be lit and shaded like a solid object, and a
shaded underside on the disc read as a stripe across the middle of the sun.

`cloud` is the signature glyph — the blocky, stair-stepped kind of pixel-art
cloud. Its silhouette is uneven **terraces** rather than smooth curves: a
taller left bump and a smaller right bump over a broad base, with the bottom
row stepping in one column per side so the base is not a sheer wall. On a dark
panel a cloud is recognised by its **tone**, not its outline: the lit top and
shadowed base give it volume, where a single flat colour read as a blob or a
hill.

That underside shade is a **lens, not a band**. It first filled the bottom two
rows edge to edge, and on the panel that read as a slab the cloud was sitting
on — a hard dark bar under a lit shape, more like a waterline than weather.
Real cloud shading is graded: darkest under the thickest part, thinning to
nothing where the cloud thins out. So the shade now steps outward as it
descends — 4 cells wide, then 8, then 16 in a 20-cell base — and stops short of
the silhouette on every row, leaving body ink at the flanks. The stepping *is*
the blend: three widths down three rows reads as a gradient at viewing
distance, where a single step reads as a bar.

`icons._draw_frame` paints each ink with its own pen (falling back to the body
pen when one is not supplied) and `ambient._draw_weather` picks the palette by
condition. The temperature digits stay on `WEATHER_RGB`.

The cloud palette is a **soft blue-grey, not white**. A white body was tried
and blew out the dark room once `BRIGHTNESS_WEATHER` scales it toward 140; the
cloud now lands around 60–90 on the panel — the clearest thing on the idle
screen, but still furniture. The lit top (`CLOUD_LIT_RGB`) was dialled down
repeatedly - 205/230/240 (panel ~112) → 170/195/205 (~93) → 150/178/190 (~82) →
140/170/187 (~77) - and each pass still drew "a touch hot". Dimming the lit any
further only closes the gap to the body, so the last pass scales the **whole**
cloud (~0.88), body and shade included. The rim was the story
of the earlier passes (white → 150 → 110 → 85, each "too bright"); with the tone
split doing the work, it is now just a quiet edge rather than the brightest
element.

All are single static frames except **rain**, which animates: a short cloud with
three streams of drops falling through it (`_rain_frame`, cycled on
`icons.WEATHER_FRAME_MS` = 300 ms). A still frame of drops-on-a-cloud did not
read as rain on the wall.

### Seeing the glyphs

The condition comes from Open-Meteo, so the panel only ever shows the one the
weather actually is — there is no "show me rain". To look at the whole set on
the real panel:

```sh
.venv/bin/mpremote connect /dev/tty.<board> run scripts/preview-icons.py
```

It paints all nine (the seven conditions plus the two night glyphs) in turn at
the idle screen's own palettes and brightness,
then soft-resets so `main.py` takes the panel back (you do not have to restart
it). `mpremote run` takes no script arguments, so the pace and a single-glyph
mode are set by a global in the REPL namespace instead, both in one invocation:

```sh
# 9 s per glyph
.venv/bin/mpremote connect /dev/tty.<board> \
  exec "PREVIEW_HOLD_MS=9000" run scripts/preview-icons.py

# hold just one glyph
.venv/bin/mpremote connect /dev/tty.<board> \
  exec "PREVIEW_CONDITION='rain'" run scripts/preview-icons.py
```

Note it feeds the watchdog while it runs, because `main.py` is the thing
that normally does and it is not running: without that the ~8 s fuse resets the
board partway through the set.

For a render that needs no board at all, `tests/`-style host scripts can
compose the same frames through a fake display (that is how the palettes above
were chosen).

## Linting firmware

Firmware is linted with `ruff check .`, which covers the
repository root (`main.py`, `config.py`) and `lib/`.

Ruff cannot be told to target the board's MicroPython version. This board runs
**MicroPython 1.19.1**, whose language is roughly **CPython 3.4**, but ruff's
lowest `target-version` is **py37** — that is what `pyproject.toml` sets. The
lint is therefore a **floor, not a guarantee**:

- ruff **does** reject syntax newer than py37 — the walrus operator (`:=`),
  positional-only `/` parameters, and `match` statements.
- ruff **does not** reject 3.5–3.7 constructs the board may not parse —
  f-strings, `async`/`await`, variable annotations, and numeric underscores.

Keep firmware inside MicroPython's supported subset: a green ruff run alone
does not prove the board will parse the code.

## Generated by genproj

This project was generated using the genproj tool.
