#!/usr/bin/env bash
# Move the owner relay host: what the new host must have before the switch, and the copy that gets it there
# without building anything. Runs on the operator workstation. docs/OWNER_HOST_SWITCH.md is the procedure;
# this is its tool.
#
#   owner-host-switch.sh inventory                         what the current host holds (read-only)
#   owner-host-switch.sh preflight <user@new-host> [port]  is the new host ready? (read-only; exit 1 when not)
#   owner-host-switch.sh copy <user@new-host> [port]       what would be copied from the current host
#   owner-host-switch.sh copy <user@new-host> [port] --apply
#
# The database stays on the owner's Windows workstation, which reaches the relay host by reverse SSH: its
# forwards (loopback 15432, 15433, 15681 on the relay host) are the owner's to point at the new host, and
# `preflight` only reports whether they have arrived. The current host is never changed by this tool: `copy`
# reads from it and writes to the new one. Nothing here stops or starts a service; the switch itself is the
# procedure's stage 4. No secret value is printed: the env file travels host to host, unread.
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
. "$here/owner-host.sh"
ssh_options=(-o BatchMode=yes -o IdentitiesOnly=yes -o StrictHostKeyChecking=yes -o ConnectTimeout=15 -i "$OWNER_PEER_SSH_KEY")
IMAGES=(trading-hareness-peer-quant-research:latest trading-hareness-peer-db-tunnel:latest trading-hareness-peer-db-batch-tunnel:latest)
VOLUMES=(trading-hareness-peer_peer_quant_data trading-hareness-peer_peer_quant_research_data)
# The rootless Docker of the service user.
docker_env='export XDG_RUNTIME_DIR="/run/user/$(id -u)" DOCKER_HOST="unix:///run/user/$(id -u)/docker.sock"'

usage() { sed -n '5,8p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//' >&2; exit 2; }
old() { ssh "${ssh_options[@]}" -p "$OWNER_PEER_PORT" "$OWNER_PEER_HOST" "$@"; }
new() { ssh "${ssh_options[@]}" -p "$new_port" "$new_host" "$@"; }

verb="${1:-}"; [ -n "$verb" ] || usage; shift
if [ "$verb" = preflight ] || [ "$verb" = copy ]; then
  new_host="${1:-}"; [ -n "$new_host" ] || usage; shift
  new_port=22; apply=false
  while (($#)); do
    case "$1" in
      --apply) apply=true ;;
      [0-9]*) new_port="$1" ;;
      *) usage ;;
    esac
    shift
  done
  [ "$new_host" != "$OWNER_PEER_HOST" ] || { echo "refusing: $new_host is the current owner host" >&2; exit 2; }
fi
case "$verb" in
  inventory)
    old "$docker_env"'
      echo "host: $(hostname) $(hostname -I)"; free -m | sed -n 2p; df -h "$HOME" | sed -n 2p
      echo "release tree: $(readlink -f "$HOME/trading_hareness")"
      echo "overlay: $(basename "$(readlink -f "$HOME/trading_hareness/hotfix/quant-service/current")")"
      echo "env file: $(readlink -f "$HOME/trading_hareness/deploy/shared-peer/.env")"
      docker images --format "image {{.Repository}}:{{.Tag}} {{.ID}} {{.Size}}" | sort
      docker ps --format "container {{.Names}} {{.Status}}"
      docker volume ls --format "volume {{.Name}}"
      systemctl --user list-unit-files 2>/dev/null | grep -E "^peer-" | sed "s/^/unit /"
      echo "authorized keys: $(awk "{print \$NF}" "$HOME/.ssh/authorized_keys" | tr "\n" " ")"
      echo "loopback listeners: $(ss -ltn | awk "NR>1 {print \$4}" | grep "^127.0.0.1:1" | sort | tr "\n" " ")"' ;;
  preflight)
    # Each line is "ok|MISSING|note  <what>"; MISSING makes the exit code 1.
    report="$(new "$docker_env"'
      say() { printf "%-8s %s\n" "$1" "$2"; }
      say note "host $(hostname) $(hostname -I | cut -d" " -f1), $(. /etc/os-release; echo "$PRETTY_NAME") $(uname -m)"
      total="$(awk "/^MemTotal:/ {print int(\$2/1024)}" /proc/meminfo)"
      [ "$total" -ge 3000 ] && say ok "memory ${total} MB" || say MISSING "memory ${total} MB: under 3000 MB (894 MB stalled on 2026-10-14)"
      free_gb="$(df -BG --output=avail "$HOME" | tail -n 1 | tr -dc 0-9)"
      [ "$free_gb" -ge 10 ] && say ok "disk ${free_gb} GB free" || say MISSING "disk ${free_gb} GB free: under 10 GB"
      loginctl show-user "$(id -un)" -p Linger 2>/dev/null | grep -q "=yes" && say ok "linger is on" || say MISSING "linger is off: sudo loginctl enable-linger $(id -un)"
      docker info >/dev/null 2>&1 && say ok "rootless docker $(docker version --format "{{.Server.Version}}")" || say MISSING "rootless docker does not answer"
      docker compose version >/dev/null 2>&1 && say ok "docker compose $(docker compose version --short)" || say MISSING "docker compose plugin"
      for image in '"${IMAGES[*]}"'; do
        docker image inspect "$image" >/dev/null 2>&1 && say ok "image $image $(docker image inspect -f "{{.Id}}" "$image" | cut -c8-19)" || say MISSING "image $image (copy)"
      done
      [ -f "$HOME/trading_hareness/deploy/shared-peer/compose.yaml" ] && say ok "release tree $(readlink -f "$HOME/trading_hareness")" || say MISSING "release tree ~/trading_hareness (copy)"
      [ -e "$HOME/trading_hareness/hotfix/quant-service/current/app/main.py" ] && say ok "overlay $(basename "$(readlink -f "$HOME/trading_hareness/hotfix/quant-service/current")")" || say MISSING "source overlay (copy)"
      env="$HOME/trading_hareness/deploy/shared-peer/.env"
      [ -L "$env" ] && [ -s "$(readlink -f "$env")" ] && say ok "env file is a symlink to $(readlink -f "$env") ($(stat -c %a "$(readlink -f "$env")"))" || say MISSING "env file behind the .env symlink (copy)"
      [ -f "$HOME/.ssh/peer_tunnel_ed25519" ] && say ok "self-tunnel key" || say MISSING "self-tunnel key: scripts/shared-peer/configure-peer-self-tunnel.sh <this host internal address> 22"
      for unit in peer-session-guard-heal.timer peer-session-guard-preopen.timer peer-daily-backfill.timer; do
        systemctl --user is-enabled "$unit" >/dev/null 2>&1 && say ok "timer $unit" || say MISSING "timer $unit (copy installs it, disabled until the switch)"
      done
      for port in 15432 15433 15681; do
        ss -ltn | grep -q "127.0.0.1:$port " && say ok "the Windows forward on $port has arrived" || say MISSING "nothing listens on 127.0.0.1:$port: the owner has not pointed the Windows reverse tunnel here"
      done
      for port in 15682 15683; do
        ss -ltn | grep -q "127.0.0.1:$port " && say note "127.0.0.1:$port is in use: the service already runs here" || say ok "127.0.0.1:$port is free"
      done
      say note "authorized keys: $(awk "{print \$NF}" "$HOME/.ssh/authorized_keys" 2>/dev/null | tr "\n" " ")"')"
    printf '%s\n' "$report"
    ! grep -q '^MISSING' <<<"$report" || { echo "not ready: $(grep -c '^MISSING' <<<"$report") thing(s) missing" >&2; exit 1; } ;;
  copy)
    release="$(old 'readlink -f "$HOME/trading_hareness"')"
    label="$(basename "$(dirname "$release")")"
    echo "from $OWNER_PEER_HOST to $new_host:"
    echo "  images: ${IMAGES[*]} (saved on the old host, loaded on the new one; nothing is built)"
    echo "  release tree: $release (with its overlays), as ~/.local/share/trading-hareness/releases/$label and ~/trading_hareness"
    echo "  env file: the file behind .env, to the same path, mode 600, unread"
    echo "  volumes: ${VOLUMES[*]}"
    echo "  systemd user units: deploy/shared-peer/systemd/* installed, timers left disabled until the switch"
    echo "not copied: the self-tunnel key and known_hosts (made on the new host by configure-peer-self-tunnel.sh);"
    echo "  authorized_keys (the owner and the operator add their keys); nginx and anything outside the service user"
    [ "$apply" = true ] || { echo "dry run: nothing copied; append --apply"; exit 0; }
    new "$docker_env"'; docker info >/dev/null' || { echo "refusing: rootless docker does not answer on $new_host" >&2; exit 1; }
    old "$docker_env; docker save ${IMAGES[*]}" | new "$docker_env; docker load"
    new "mkdir -p \"\$HOME/.local/share/trading-hareness/releases/$label\" \"\$HOME/.secrets/owner\" && chmod 700 \"\$HOME/.secrets/owner\""
    old "tar -C '$(dirname "$release")' -cf - trading_hareness" | new "tar -C \"\$HOME/.local/share/trading-hareness/releases/$label\" -xf -"
    new "ln -sfn \"\$HOME/.local/share/trading-hareness/releases/$label/trading_hareness\" \"\$HOME/trading_hareness.next\" && mv -Tf \"\$HOME/trading_hareness.next\" \"\$HOME/trading_hareness\""
    old 'cat "$(readlink -f "$HOME/trading_hareness/deploy/shared-peer/.env")"' | new 'umask 077; cat > "$HOME/.secrets/owner/.env.owner"; chmod 600 "$HOME/.secrets/owner/.env.owner"'
    for volume in "${VOLUMES[@]}"; do
      old "$docker_env; docker run --rm --entrypoint tar -v $volume:/v:ro ${IMAGES[0]} -C /v -cf - ." \
        | new "$docker_env; docker volume create $volume >/dev/null; docker run --rm -i --entrypoint tar -v $volume:/v ${IMAGES[0]} -C /v -xf -"
    done
    new 'mkdir -p "$HOME/.config/systemd/user" && cp "$HOME"/trading_hareness/deploy/shared-peer/systemd/* "$HOME/.config/systemd/user/" && systemctl --user daemon-reload'
    echo "copied. Next: the procedure's stage 3 (self tunnel, the host's own address in the env file), then preflight." ;;
  *) usage ;;
esac
