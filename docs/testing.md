# Development and testing

## Run the checks

For the lightweight regression suite, use a virtual environment with
`requirements-test.txt` installed:

```bash
python -m pip install -r requirements-test.txt ruff
python -m unittest discover -s tests -v
ruff check --select E9,F --ignore F401,F541,F841 overlay config tests
```

Native tests require GCC. Installer fixtures also use `jq` and `rsync`.
Tests that need the complete OpenHop packages may be skipped in this lightweight
environment; use the integration runner for those checks.

### Pinned-package integration

```bash
bash tests/upstream_integration.sh
```

The runner checks the revisions in [dependencies.conf](../config/dependencies.conf),
creates disposable core/repeater checkouts, applies the overlays and installs
them in a temporary environment. It runs local regressions, companion TCP,
HTTP, listener and simulated-daemon scenarios, followed by selected upstream
contracts. It prints and retains its fixture directory for inspection.
It does not install services or operate radio hardware.

To build the complete overlaid HAL as well:

```bash
WM1303_TEST_BUILD_HAL=1 bash tests/upstream_integration.sh
```

For an offline run, `WM1303_TEST_SOURCE_CACHE` can point to verified Git
checkouts named `core`, `repeater` and optionally `hal`. With uv and its dependency
cache available, `WM1303_TEST_OFFLINE=1` disables package-index access.
`WM1303_TEST_PYTHON_VERSION` selects the interpreter when the runner uses uv.
Revision and release-tag checks still apply to cached sources.

Once the pinned packages and overlays are installed in a test environment,
the integration scenarios can also be run individually:

```bash
python tests/companion_tcp_scenario.py -q
python tests/analytics_http_scenario.py -q
python tests/http_binding_scenario.py -q
python tests/daemon_smoke_scenario.py -q
python tests/package_recovery_scenario.py -q
```

These use temporary state and local sockets. Package recovery checks use
`pip --dry-run --no-index --no-deps` and do not install packages.

### Browser checks

Use Node.js 22, Chromium and the compiled Console HTML from the pinned repeater
checkout:

```bash
npm ci --ignore-scripts
export CHROMIUM_PATH=/path/to/chromium
node tests/dashboard_browser_scenario.cjs /path/to/repeater/web/html
```

The scenario copies and adapts the Console into a temporary directory, serves
fixture APIs and exercises both desktop and mobile layouts. It checks saves,
failed-save drafts, frequency precision, history intervals, scopes, spectrum
controls and accessible channel controls. No live device is contacted.

### systemd shutdown check

On a systemd host with GCC, run as a regular user with noninteractive sudo:

```bash
results_dir="$(mktemp -d)"
python3 tests/systemd_shutdown_scenario.py --output-dir "$results_dir"
```

This creates temporary services containing harmless fixture processes. It
reproduces interrupted reset cleanup with group-wide termination, verifies the
configured shutdown policy, and checks that leftover processes are killed.
It does not stop OpenHop or access GPIO, radio or application databases.
Use a new output directory for each run; journals and results are retained there.

## Validation checkpoint: 2026-11-05

The current pinned-package software checks pass:

| Check | Result |
| --- | --- |
| Local regressions | 440 tests |
| Companion TCP | 13 scenarios |
| Analytics/authentication HTTP | 7 scenarios |
| HTTP listener address updates | 1 scenario, with five configuration cases |
| Simulated daemon startup/shutdown | 1 scenario |
| Native HAL | Complete local build and targeted Raspberry Pi builds |

Native regressions compile actual C functions, blocks or transport files with
simulated hardware interfaces. They cover CAD timeouts and recovery failures,
slow-mode wait budgets, receiver startup and gain restoration, empty/failed RX
reads, IRQ cleanup, USB frame bounds and queue retries. They do not measure RF
performance. See [LBT and CAD](lbt_cad.md) for the radio behavior and timing rules.

Other regression coverage includes authentication errors, room/companion
activation and rollback, ACK priority and cancellation, atomic configuration
saves, legacy metrics migrations, history queries, disabled MQTT defaults and
frequency-preserving Manager saves.

### Device verification and pending deployment

Targeted radio, retention, GPS fallback, MQTT initialization and shutdown fixes
were deployed and checked on a Raspberry Pi using SPI and SF8/BW62.5. Checks
included live RX/TX, the first CAD scan after startup, receiver recovery after
TX, authenticated management APIs, SQLite integrity, clean GPIO shutdown and
preservation of configuration and identities.

**The HTTP listener address-reporting fix is not yet deployed.** It passed
isolated configuration and real CherryPy tests on the Pi using temporary state,
but network access was lost before installation. It saves a changed host/port
for restart without relabelling or interrupting the active listener, and respects
the dedicated `http` section over the legacy `web` fallback.

The full accumulated repository update has not been verified as one deployment
on that device. USB behavior and slow CAD modes were tested with simulated I/O;
no USB radio or slow-mode RF measurements were made. Device IP addresses,
network names and temporary backup paths are session-specific, not part of this
testing procedure.

## Compatibility contracts

Dependency revisions are authoritative in
[dependencies.conf](../config/dependencies.conf). The current integration uses
OpenHop core 1.1.3, OpenHop Repeater 1.1.4 and the pinned Semtech HAL.
Wire-format checks also reference
[MeshCore firmware at 0679dbe](https://github.com/meshcore-dev/MeshCore/tree/0679dbeffc504d562d2f09eb072fdc223f8ffc2a).

The upstream core runner explicitly excludes four contact/channel-store
assertions. This overlay uses detached contact objects, separate transient
contact capacity and preserved channel indices; local tests cover those
contracts. The exclusions are listed in `tests/upstream_integration.sh`.

The complete upstream repeater router suite is not a passing compatibility
check: optional policy/API expectations differ. Selected upstream contracts and
local routing/lifecycle scenarios are checked separately. Passing them does not
establish complete upstream feature parity.

## Validation limits

- Software fixtures do not establish over-the-air interoperability, RF timing,
  regulatory compliance or power-loss safety. Verify the intended channels,
  modulation, sync word, LBT behavior, long transmissions and recovery on the
  target hardware. GPS synchronization and beacon timing need hardware checks.
- Companion and room scenarios use temporary identities and local packet
  injection. They verify login, replies, ACK ownership and persisted delivery,
  rather than a mobile application's complete handshake or RF delivery.
- Explicit radio-to-radio bridge rules can copy packets transparently. The
  repeater stage separately applies routing and scoped-flood validation; these
  are different forwarding paths.
- Saved settings and active settings can differ until restart. Watchdog recovery
  retains the previous active radio configuration. Live reloads are sequential;
  a later failure can leave earlier reload steps applied.
- Historical summary averages and bucket boundaries can be approximate.
  Previously discarded measurements cannot be reconstructed, and missing noise
  readings remain unknown rather than zero. MQTT publisher completion does not
  confirm broker delivery.
- Upgrade rollback covers catchable failures. An interrupted migration can
  require retained originals; follow the
  [migration procedure](installation.md#migration-from-pymc-installation-paths).
  Legacy companion storage has a separate
  [ownership migration procedure](configuration.md#companion-storage-ownership).

Earlier audit notes and historical test counts are available in Git history.
