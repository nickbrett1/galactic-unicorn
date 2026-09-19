# galactic-unicorn

A galactic-unicorn project generated with genproj

## Capabilities

This project includes the following capabilities:

- **Docker**: Adds Docker support for containerised builds and tooling.
- **AI Coding Agents**: Sets up the AI coding agents in the devcontainer: goose (config, MCP servers and spec-first recipes) plus the Cursor and Antigravity CLIs.
- **Container Agent**: Every generated devcontainer brings up and registers its own a2a-goose agent (`<repo>-dev`), reached over the tailnet by the LiteLLM proxy; reuses the a2a-goose GitHub release channel, so the container has the same self-update path as a host.
- **Python DevContainer**: Sets up a VS Code DevContainer with Python environment.
- **MicroPython / RP2040**: Adds the MicroPython board toolchain (mpremote) plus the USB passthrough plumbing needed to drive an RP2040 from inside the devcontainer. OrbStack forwards the board's CDC-ACM REPL into the Linux VM automatically, but a container only sees it when the device is granted explicitly - this capability emits that grant and a port auto-detect helper. The container keeps the macOS node name (/dev/tty.usbmodem<serial>), which changes with the USB port, so the port is discovered at runtime. Note the grant is deliberately broad: OrbStack assigns the node a dynamic character major, and the device-cgroup-rule grammar accepts only a single major or '*', so scoping the grant to USB serial is not expressible - the container can open any host character device. Access is exclusive: while the container holds the port, host tools such as Thonny cannot open it.
- **Ruff (Python code quality)**: Adds fast, zero-configuration Python linting with Ruff (rules live in pyproject.toml [tool.ruff] and the CI test job runs `ruff check src tests`). Requires a Python devcontainer.

## Setup

1. Clone the repository
2. Create a virtualenv and install the package with dev extras:

   ```bash
   python3 -m venv .venv
   . .venv/bin/activate
   pip install -e ".[dev]"
   ```

3. Run the checks:

   ```bash
   ruff check src tests
   pytest -v
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

This repo targets the **Pimoroni Galactic Unicorn** (RP2040). Its firmware lives at https://github.com/pimoroni/unicorn.

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

## Generated by genproj

This project was generated using the genproj tool.
