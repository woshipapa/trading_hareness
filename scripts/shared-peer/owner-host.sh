# The owner relay host, in one place. Sourced by every tool that reaches it:
#   . "<this directory>/owner-host.sh"
# It only fills what the environment does not set, so OWNER_PEER_HOST=... still overrides for one run.
# When the owner moves to another host this file is the one tracked line to change
# (docs/OWNER_HOST_SWITCH.md lists the rest: the workstation's secrets, the edge tunnel, the host itself).
: "${OWNER_PEER_HOST:=stockpeer@118.178.133.37}"
: "${OWNER_PEER_PORT:=22}"
: "${OWNER_PEER_SSH_KEY:=$HOME/.ssh/stockpeer_ed25519}"
