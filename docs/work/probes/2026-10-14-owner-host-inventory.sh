# What the owner relay host holds that a replacement host needs. Read-only; prints names and sizes, never a secret value.
# Run on the owner host:  ssh <host> 'bash -s' < this file
echo "== system"; . /etc/os-release; echo "$PRETTY_NAME"; uname -m; nproc; free -m | sed -n 2,3p; df -h / "$HOME" 2>/dev/null | sed -n 2,3p
echo "== user"; id -un; loginctl show-user "$(id -un)" -p Linger 2>/dev/null
echo "== docker"; export XDG_RUNTIME_DIR="/run/user/$(id -u)" DOCKER_HOST="unix:///run/user/$(id -u)/docker.sock"
docker version --format 'client {{.Client.Version}} server {{.Server.Version}} rootless={{range .Server.Components}}{{end}}' 2>/dev/null; docker info --format '{{.SecurityOptions}} storage={{.Driver}} root={{.DockerRootDir}}' 2>/dev/null; docker compose version --short 2>/dev/null
echo "== images"; docker images --format '{{.Repository}}:{{.Tag}} {{.ID}} {{.Size}}' | sort | head -30
echo "== containers"; docker ps -a --format '{{.Names}} | {{.Image}} | {{.Status}} | {{.Ports}}' | cut -c1-200
echo "== volumes"; docker volume ls --format '{{.Name}}' | while read v; do echo "$v $(docker run --rm -v "$v":/v alpine sh -c 'du -sh /v | cut -f1' 2>/dev/null || echo '?')"; done 2>/dev/null | head -10
echo "== networks"; docker network ls --format '{{.Name}} {{.Driver}}' | grep -v -E '^(bridge|host|none) '
echo "== release tree"; readlink -f "$HOME/trading_hareness"; readlink -f "$HOME/wheelhouse" 2>/dev/null; ls "$HOME/.local/share/trading-hareness/releases" 2>/dev/null | tail -n 6; du -sh "$HOME/.local/share/trading-hareness" "$HOME/wheelhouse/" 2>/dev/null
echo "== hotfix overlay"; d="$HOME/trading_hareness/hotfix/quant-service"; readlink -f "$d/current" 2>/dev/null; ls "$d/releases" 2>/dev/null | tail -n 4
echo "== compose dir files"; ls -la "$HOME/trading_hareness/deploy/shared-peer" | awk '{print $1, $NF, $(NF-1), $(NF-2)}' | cut -c1-160
echo "== env files: key names only"; for f in "$HOME/trading_hareness/deploy/shared-peer/.env" "$HOME/trading_hareness/deploy/shared-peer/intraday-secrets.env"; do r="$(readlink -f "$f")"; echo "$f -> $r ($(stat -c '%a' "$r" 2>/dev/null)) $(grep -c '=' "$r" 2>/dev/null) keys"; grep -o '^[A-Z0-9_]*=' "$r" 2>/dev/null | tr -d '=' | tr '\n' ' '; echo; done
echo "== secrets dir (names)"; ls -la "$HOME/.secrets" "$HOME/.secrets/owner" 2>/dev/null | awk '{print $1, $NF}' | cut -c1-120 | head -30
echo "== certs / keys used by tunnels (names)"; ls -la "$HOME/trading_hareness/deploy/shared-peer/certs" 2>/dev/null | awk '{print $1, $NF}' | head; ls "$HOME/.ssh" | head -20; echo "authorized_keys lines: $(grep -c . "$HOME/.ssh/authorized_keys" 2>/dev/null)"
echo "== systemd user units"; systemctl --user list-unit-files 2>/dev/null | grep -E 'peer|quant|trading|tunnel|docker' | head -20; systemctl --user list-timers --all 2>/dev/null | grep -E 'peer|quant' | cut -c1-160
echo "== crontab"; crontab -l 2>/dev/null | grep -v '^#' | cut -c1-160 | head
echo "== listening"; ss -ltn 2>/dev/null | awk 'NR>1 {print $4}' | sort -u | tr '\n' ' '; echo
echo "== who connects in (established ssh, remote addresses only)"; ss -tn state established '( sport = :22 )' 2>/dev/null | awk 'NR>1 {split($4,a,":"); print a[1]}' | sort | uniq -c
echo "== outbound tunnels in compose (service: command summary)"; grep -n -E 'db-tunnel|db-batch-tunnel|PEER_.*(HOST|PORT)|REMOTE|LOCAL' "$HOME/trading_hareness/deploy/shared-peer/compose.yaml" | cut -c1-170 | head -40
