#!/usr/bin/env bash
# Bootstrap a fresh Ubuntu 26.04 server for the Getnet multi-agent support system.
#   sudo ./scripts/bootstrap-ubuntu.sh
# Installs: base prerequisites, Docker Engine + Compose plugin from Docker's OFFICIAL apt repository (no distro
# docker.io, no legacy docker-compose), and uv (for host-side tests). Idempotent and fail-fast; opens no ports.
# Flags: --skip-docker-verify   (CI containers without a Docker daemon)   --user <name> (add to the docker group)
set -Eeuo pipefail
trap 'echo "bootstrap failed at line $LINENO" >&2' ERR

SKIP_VERIFY=0
TARGET_USER="${SUDO_USER:-}"
while [ $# -gt 0 ]; do
  case "$1" in
    --skip-docker-verify) SKIP_VERIFY=1 ;;
    --user) TARGET_USER="$2"; shift ;;
    *) echo "unknown flag: $1" >&2; exit 2 ;;
  esac
  shift
done

log() { printf '\033[1;34m[bootstrap]\033[0m %s\n' "$*"; }

[ "$(id -u)" -eq 0 ] || { echo "run as root: sudo $0" >&2; exit 1; }
. /etc/os-release
if [ "${ID:-}" != "ubuntu" ]; then echo "this script supports Ubuntu (found ${ID:-unknown})" >&2; exit 1; fi
if [ "${VERSION_ID:-}" != "26.04" ]; then log "WARNING: tested on Ubuntu 26.04; found ${VERSION_ID:-?} — continuing"; fi
CODENAME="${UBUNTU_CODENAME:-${VERSION_CODENAME:-}}"
ARCH="$(dpkg --print-architecture)"

log "updating package metadata and installing prerequisites"
export DEBIAN_FRONTEND=noninteractive
apt-get update -y -qq
apt-get install -y -qq --no-install-recommends ca-certificates curl gnupg git make jq >/dev/null

if ! command -v docker >/dev/null 2>&1 || ! docker compose version >/dev/null 2>&1; then
  log "installing Docker Engine + Compose plugin from download.docker.com"
  # Remove conflicting distro packages if present (no-op on a fresh server).
  for pkg in docker.io docker-doc docker-compose docker-compose-v2 podman-docker containerd runc; do
    dpkg -s "$pkg" >/dev/null 2>&1 && apt-get remove -y -qq "$pkg" >/dev/null || true
  done
  install -m 0755 -d /etc/apt/keyrings
  curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
  chmod a+r /etc/apt/keyrings/docker.asc
  # Docker publishes a suite per Ubuntu codename; fall back to the latest LTS suite if 26.04's is not live yet.
  SUITE="$CODENAME"
  if ! curl -fsSI "https://download.docker.com/linux/ubuntu/dists/${SUITE}/Release" >/dev/null 2>&1; then
    log "Docker suite '${SUITE}' not published yet; using 'noble' (24.04) packages"
    SUITE="noble"
  fi
  cat > /etc/apt/sources.list.d/docker.sources <<SRC
Types: deb
URIs: https://download.docker.com/linux/ubuntu
Suites: ${SUITE}
Components: stable
Architectures: ${ARCH}
Signed-By: /etc/apt/keyrings/docker.asc
SRC
  apt-get update -y -qq
  apt-get install -y -qq docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin >/dev/null
else
  log "Docker Engine and Compose plugin already installed"
fi

if command -v systemctl >/dev/null 2>&1 && [ -d /run/systemd/system ]; then
  systemctl enable --now docker >/dev/null
fi

if [ -n "$TARGET_USER" ] && id "$TARGET_USER" >/dev/null 2>&1; then
  usermod -aG docker "$TARGET_USER"
  log "added $TARGET_USER to the docker group (log out/in to take effect)"
fi

if ! command -v uv >/dev/null 2>&1; then
  log "installing uv (host-side tests)"
  curl -LsSf https://astral.sh/uv/install.sh | env UV_INSTALL_DIR=/usr/local/bin INSTALLER_NO_MODIFY_PATH=1 sh >/dev/null
fi

log "verifying"
docker --version
docker compose version
uv --version
if [ "$SKIP_VERIFY" -eq 0 ]; then
  docker run --rm hello-world >/dev/null && log "docker daemon OK (hello-world)"
fi

cat <<'NEXT'

Next steps:
  1. cp .env.example .env            # add MINIMAX_API_KEY (and WEB_SEARCH_API_KEY)
  2. make verify                     # build, start, migrate, seed, snapshot, all suites, eval
  3. open http://<server>:8000/ui and /operator   (dev demo pages; disabled in production)
  4. make ingest                     # optional: live crawl of the official Getnet sites
NEXT
