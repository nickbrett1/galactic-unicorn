# galactic-unicorn

A galactic-unicorn project generated with genproj

## Capabilities

This project includes the following capabilities:

- **Docker**: Adds Docker support for containerised builds and tooling.
- **Python DevContainer**: Sets up a VS Code DevContainer with Python environment.
- **MicroPython board**: Adds the MicroPython board toolchain (mpremote) plus the USB passthrough plumbing needed to drive an RP2-series board (RP2040 or RP2350) from inside the devcontainer. OrbStack forwards the board's CDC-ACM REPL into the Linux VM automatically, but a container only sees it when the device is granted explicitly - this capability emits that grant and a port auto-detect helper. The container keeps the macOS node name (/dev/tty.usbmodem<serial>), which changes with the USB port, so the port is discovered at runtime. Note the grant is deliberately broad: OrbStack assigns the node a dynamic character major, and the device-cgroup-rule grammar accepts only a single major or '*', so scoping the grant to USB serial is not expressible - the container can open any host character device. Access is exclusive: while the container holds the port, host tools such as Thonny cannot open it.
- **Ruff (Python code quality)**: Adds fast, zero-configuration Python linting with Ruff (rules live in pyproject.toml [tool.ruff]). Lint locally with `ruff check`. Requires a Python devcontainer.
- **Doppler Secrets Management**: Integrates Doppler for secure secrets management. Enables the various MCP servers that rely on privileged tokens to access their services (e.g. CircleCI, GitHub, SonarQube).
- **AI Coding Agents**: Sets up the AI coding agents in the devcontainer: goose (config, MCP servers and spec-first recipes) plus the Cursor and Antigravity CLIs.

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

**Exception — flashing.** That automatic forwarding covers the CDC-ACM REPL
only. In BOOTSEL mode the board enumerates as a USB **mass-storage** device
(`2e8a:0003`, "RP2 Boot"), which OrbStack does **not** forward on its own, so
neither `picotool` nor a host-mounted volume is reachable until you
`orb usb attach <id>` (`orb usb list` prints the id). That path is fragile:
`orb usb detach/attach` can wedge and take OrbStack down with it. The robust
route is to flash from the **host** — put the board in BOOTSEL (`mpremote
bootloader`) and copy the `.uf2` onto the `RPI-RP2` volume that macOS mounts,
then let the board reboot. Note the first re-enumeration after a flash may
leave the container without a REPL node; a physical replug restores it.

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

## Linting firmware

Firmware is linted with `ruff check .`, which covers the
repository root (`main.py`, `config.py`) and `lib/`.

Ruff cannot be told to target the board's MicroPython version. This board was
reflashed to **MicroPython v1.29.0** on 2026-09-19 (up from the stock 1.19.1),
but ruff's lowest `target-version` is **py37** — that is what `pyproject.toml`
sets. The lint is therefore a **floor, not a guarantee**:

- ruff **does** reject syntax newer than py37 — the walrus operator (`:=`),
  positional-only `/` parameters, and `match` statements.
- ruff **does not** reject 3.5–3.7 constructs the board may not parse —
  `async`/`await`, variable annotations, and numeric underscores.

Keep firmware inside MicroPython's supported subset: a green ruff run alone
does not prove the board will parse the code. (Measured on the board:
f-strings, `.format()`, the walrus operator and numeric underscores **do**
parse, on both 1.19.1 and v1.29.0. What does **not** parse is `/`
positional-only parameters and `match`.)

## WiFi and the ambient clock

WiFi exists in phase 1 for **one** reason: NTP, so the idle screen can show a
clock. Everything else — including the countdown — is locally timed and runs
with WiFi switched off. If NTP never lands, AMBIENT degrades to a slow
breathing status pixel, so a network problem looks "quiet", never "broken".

Credentials live in `config_secrets.py`, which is **gitignored** and absent by
default. **The source of truth is Doppler** (project `common`, config `dev`),
so the secret survives a container rebuild — regenerate it with:

```bash
./scripts/gen-secrets.sh        # writes config_secrets.py from Doppler
```

`config_secrets.example.py` is the committed shape of the file if you'd rather
fill it in by hand.

With `WIFI_ENABLED = True` in `config.py` but **no** credentials, `sync_ntp()`
skips immediately, so the switch is safe to leave on — it costs nothing until
the secrets file exists.

Two things that bite:

- **2.4 GHz only.** The Pico W has no 5 GHz radio. Use a 2.4 GHz SSID.
- **`UTC_OFFSET_S` is a fixed offset, not a timezone.** There is no tz database
  on the board, so it must be edited by hand at the DST switch (Eastern is
  `-4 * 3600` in summer, `-5 * 3600` in winter).

`NTP_ATTEMPTS` / `NTP_RETRY_MS` exist because the **first** query after
association can time out — cold DNS/route, and `ntptime`'s built-in timeout is
only 1 s. Without the retry the clock silently degrades on a cold boot.

> **Secrets never live in this repo.** `scripts/gen-secrets.sh` pulls them from
> Doppler into the gitignored `config_secrets.py`, so the repo and a rebuilt
> container both start with no copy on disk. The board still needs a file, so
> the generated output is deployed with `mpremote fs cp config_secrets.py :`.

## Generated by genproj

This project was generated using the genproj tool.
