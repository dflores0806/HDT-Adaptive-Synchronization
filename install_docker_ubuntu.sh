#!/usr/bin/env bash
# Installs official Docker Engine + Compose plugin on a clean supported Ubuntu host.
# Requires sudo. Does not remove existing Docker packages, volumes, or containers.
set -euo pipefail
if [[ $(id -u) -eq 0 ]]; then
  echo 'Execute as a normal sudo-capable user (not root).'; exit 2
fi
if [[ ! -r /etc/os-release ]]; then echo 'Unsupported system'; exit 2; fi
. /etc/os-release
if [[ "$ID" != 'ubuntu' ]]; then echo "Only supported on Ubuntu; detected $ID"; exit 2; fi
if command -v docker >/dev/null 2>&1; then
  echo 'Docker command already installed. Not replacing it or uninstalling conflicting packages.'
  docker --version || true
  if docker compose version >/dev/null 2>&1; then
    echo 'Docker Compose plugin available.'
  else
    echo 'Docker Compose plugin missing: inspect current installation before using this installer.'
  fi
  echo 'To start existing Docker: sudo systemctl enable --now docker'
  exit 0
fi
if dpkg-query -W -f='${Status}' docker.io podman-docker containerd runc 2>/dev/null | grep -q 'install ok installed'; then
  echo 'Conflicting container runtime packages present. No automatic removal has been performed.'
  echo 'Follow the official Docker Ubuntu installation guidance to resolve the conflict manually.'
  exit 3
fi
sudo apt-get update
sudo apt-get install -y ca-certificates curl
sudo install -m 0755 -d /etc/apt/keyrings
sudo curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
sudo chmod a+r /etc/apt/keyrings/docker.asc
ARCH=$(dpkg --print-architecture)
CODENAME=${UBUNTU_CODENAME:-${VERSION_CODENAME}}
# Avoid passing user-controlled untrusted sources; /etc/os-release is host-controlled.
printf 'Types: deb\nURIs: https://download.docker.com/linux/ubuntu\nSuites: %s\nComponents: stable\nArchitectures: %s\nSigned-By: /etc/apt/keyrings/docker.asc\n' "$CODENAME" "$ARCH" | sudo tee /etc/apt/sources.list.d/docker.sources >/dev/null
sudo apt-get update
sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
sudo systemctl enable --now docker
sudo docker version
sudo docker compose version
printf '\nDocker installed. For NON-ROOT use (security-sensitive: docker group grants root-equivalent access):\n'
printf '    sudo usermod -aG docker "$USER"\n'
printf 'Then close ALL SSH sessions and reconnect; run: docker info and docker compose version.\n'
