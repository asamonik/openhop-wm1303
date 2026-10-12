#!/usr/bin/env bash
# Disposable real-package checks. Does not install services or operate hardware.
set -euo pipefail
WM1303_TEST_ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
source "$WM1303_TEST_ROOT/config/dependencies.conf"
source "$WM1303_TEST_ROOT/config/deploy_overlay.sh"
WM1303_TEST_DIR=$(mktemp -d "${TMPDIR:-/tmp}/wm1303-integration.XXXXXX")
printf 'Retained integration fixtures: %s\n' "$WM1303_TEST_DIR"

clone_pinned_fixture() {
    local repository="$1" revision="$2" version_tag="$3" destination="$4" verified
    if [ -n "${WM1303_TEST_SOURCE_CACHE:-}" ]; then
        repository="$WM1303_TEST_SOURCE_CACHE/${destination##*/}"
        git -C "$repository" cat-file -e "$revision^{commit}"
    fi
    git clone --no-checkout --no-tags "$repository" "$destination"
    git -C "$destination" fetch --no-tags origin "$revision"
    git -C "$destination" checkout --detach "$revision"
    verified=$(git -C "$destination" rev-parse HEAD)
    [ "$verified" = "$revision" ]
    if [ -n "$version_tag" ]; then
        git -C "$destination" fetch --no-tags origin "refs/tags/$version_tag:refs/tags/$version_tag"
        verified=$(git -C "$destination" rev-parse "refs/tags/$version_tag^{commit}")
        [ "$verified" = "$revision" ]
    fi
}

clone_pinned_fixture "$CORE_REPO" "$CORE_REVISION" "$CORE_TAG" "$WM1303_TEST_DIR/core"
clone_pinned_fixture "$REPEATER_REPO" "$REPEATER_REVISION" "$REPEATER_TAG" "$WM1303_TEST_DIR/repeater"
# Lightweight unit tests borrow only these unchanged upstream helpers. They are
# fixtures, never deployed over OpenHop; verify them against the selected release.
for fixture in "$WM1303_TEST_ROOT"/tests/fixtures/upstream/repeater/*.py; do
    cmp "$fixture" "$WM1303_TEST_DIR/repeater/repeater/${fixture##*/}"
done
deploy_overlay "$WM1303_TEST_ROOT/overlay/openhop_core/src/openhop_core" "$WM1303_TEST_DIR/core/src/openhop_core"
deploy_overlay "$WM1303_TEST_ROOT/overlay/openhop_repeater/repeater" "$WM1303_TEST_DIR/repeater/repeater"
if command -v uv >/dev/null 2>&1; then
    uv venv --python "${WM1303_TEST_PYTHON_VERSION:-python3}" "$WM1303_TEST_DIR/venv"
else
    python3 -m venv "$WM1303_TEST_DIR/venv"
fi
WM1303_TEST_PYTHON="$WM1303_TEST_DIR/venv/bin/python"
# The base tag is verified above. Mark the overlaid build explicitly instead
# of letting SCM guess that a dirty 1.1.4 checkout is a future 1.1.5 release.
export SETUPTOOLS_SCM_PRETEND_VERSION_FOR_OPENHOP_REPEATER="$REPEATER_TAG+wm1303"
if command -v uv >/dev/null 2>&1; then
    WM1303_TEST_UV_OPTIONS=()
    if [ "${WM1303_TEST_OFFLINE:-0}" = 1 ]; then WM1303_TEST_UV_OPTIONS+=(--offline); fi
    uv pip install "${WM1303_TEST_UV_OPTIONS[@]}" --python "$WM1303_TEST_PYTHON" -e "$WM1303_TEST_DIR/core" -e "$WM1303_TEST_DIR/repeater" pytest pytest-asyncio
else
    "$WM1303_TEST_PYTHON" -m pip install -e "$WM1303_TEST_DIR/core" -e "$WM1303_TEST_DIR/repeater" pytest pytest-asyncio
fi
"$WM1303_TEST_PYTHON" -I -c 'from openhop_core.hardware import WM1303Backend; import repeater.config; from repeater.main import RepeaterDaemon; assert WM1303Backend is not None'
"$WM1303_TEST_PYTHON" -m unittest discover -s "$WM1303_TEST_ROOT/tests" -q
"$WM1303_TEST_PYTHON" "$WM1303_TEST_ROOT/tests/companion_tcp_scenario.py" -q
"$WM1303_TEST_PYTHON" "$WM1303_TEST_ROOT/tests/analytics_http_scenario.py" -q
"$WM1303_TEST_PYTHON" "$WM1303_TEST_ROOT/tests/daemon_smoke_scenario.py" -q
# Four contact-store assertions in vanilla upstream require reference identity,
# anonymous contacts sharing permanent capacity, and omitted channel indices.
# WM1303 uses detached contacts, separate transient capacity and preserved indices;
# exercise those contracts. Every other upstream core test remains mandatory.
"$WM1303_TEST_PYTHON" -m pytest --rootdir="$WM1303_TEST_DIR/core" "$WM1303_TEST_DIR/core/tests" \
    --deselect="tests/test_companion_stores.py::TestContactStore::test_add_and_get_by_key" \
    --deselect="tests/test_companion_stores.py::TestContactStore::test_max_contacts" \
    --deselect="tests/test_companion_stores.py::TestContactStore::test_add_or_overwrite_never_evicts_for_or_via_anon" \
    --deselect="tests/test_companion_stores.py::TestChannelStore::test_set_and_get" -q
"$WM1303_TEST_PYTHON" -m pytest \
    "$WM1303_TEST_DIR/repeater/tests/test_acl_role_numbering.py" \
    "$WM1303_TEST_DIR/repeater/tests/test_login_reply_acl_wire.py" \
    "$WM1303_TEST_DIR/repeater/tests/test_identity_manager.py" \
    "$WM1303_TEST_DIR/repeater/tests/test_region_map_wiring.py" \
    "$WM1303_TEST_DIR/repeater/tests/test_radio_config.py" -q

if [ "${WM1303_TEST_BUILD_HAL:-0}" = 1 ]; then
    clone_pinned_fixture "$HAL_REPO" "$HAL_REVISION" "$HAL_TAG" "$WM1303_TEST_DIR/hal"
    deploy_overlay "$WM1303_TEST_ROOT/overlay/hal" "$WM1303_TEST_DIR/hal"
    make -C "$WM1303_TEST_DIR/hal" -j2
fi
