# galactic-unicorn

A galactic-unicorn project generated with genproj

## Capabilities

This project includes the following capabilities:

- **Docker**: Adds Docker support for containerised builds and tooling.
- **AI Coding Agents**: Sets up the AI coding agents in the devcontainer: goose (config, MCP servers and spec-first recipes) plus the Cursor and Antigravity CLIs.
- **Container Agent**: Every generated devcontainer brings up and registers its own a2a-goose agent (`<repo>-dev`), reached over the tailnet by the LiteLLM proxy; reuses the a2a-goose GitHub release channel, so the container has the same self-update path as a host.
- **Python DevContainer**: Sets up a VS Code DevContainer with Python environment.
- **MicroPython board**: Adds the MicroPython board toolchain (mpremote) plus the USB passthrough plumbing needed to drive an RP2-series board (RP2040 or RP2350) from inside the devcontainer. OrbStack forwards the board's CDC-ACM REPL into the Linux VM automatically, but a container only sees it when the device is granted explicitly - this capability emits that grant and a port auto-detect helper. The container keeps the macOS node name (/dev/tty.usbmodem<serial>), which changes with the USB port, so the port is discovered at runtime. Note the grant is deliberately broad: OrbStack assigns the node a dynamic character major, and the device-cgroup-rule grammar accepts only a single major or '*', so scoping the grant to USB serial is not expressible - the container can open any host character device. Access is exclusive: while the container holds the port, host tools such as Thonny cannot open it.
- **Ruff (Python code quality)**: Adds fast, zero-configuration Python linting with Ruff (rules live in pyproject.toml [tool.ruff]). Lint locally with `ruff check`. Requires a Python devcontainer.

## Setup

1. Clone the repository and open it in the devcontainer — the MicroPython
   toolchain lives inside it (there is no host-side install to keep in sync).
2. Run the linter over the firmware:

   ```bash
   ruff check .
   ```

Firmware runs on the board, not on the host, so there is no host test step —
see "MicroPython board" for flashing and running it.

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
