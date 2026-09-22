#!/bin/sh
set -eu

# Keep the systemd unit stable while allowing the source-only edge release to
# move atomically.  The checked-in /opt/larkagentx/bridge.py remains the
# recovery fallback if the overlay has not been installed yet.
overlay_root=${LARKX_BRIDGE_HOTFIX_ROOT:-/opt/feishu-relay-edge/hotfix}
overlay=$overlay_root/current/bridge/bridge.py
base=/opt/larkagentx/bridge.py
python_bin=/opt/supervisor/.venv/bin/python

if [ -r "$overlay" ]; then
	export LARKX_BRIDGE_SOURCE_MODE=source-overlay
	bridge_release=$(basename "$(readlink -f "$overlay_root/current")")
	export LARKX_BRIDGE_RELEASE="$bridge_release"
	exec "$python_bin" "$overlay"
fi
export LARKX_BRIDGE_SOURCE_MODE=base
export LARKX_BRIDGE_RELEASE=base
exec "$python_bin" "$base"
