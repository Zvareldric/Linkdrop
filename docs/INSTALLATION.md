# Installation Guide

**English** · [Bahasa Indonesia](INSTALLATION.id.md)

This guide installs Linkdrop for private use. It does not open the application to the public internet. Use only public media you own or are permitted to download.

## What every installation needs

- A 64-bit computer with a stable internet connection.
- At least 2 CPU cores, 4 GB RAM, and 20 GB free disk for occasional media processing. Higher-resolution video needs more temporary disk.
- Permission to install system packages or Docker, depending on the platform.
- Permission for the application user to write inside the repository's `downloads/` directory, or the container's temporary storage.
- A unique `DOWNLOAD_TOKEN_SECRET`. It is required for a secure deployment and must never be committed.

Linkdrop needs outbound internet access to public media sources. It does not need administrator access after its dependencies are installed. The browser or operating system, not Linkdrop, chooses the final save location for downloads.

## Recommended path: Windows

For a Windows computer, start with [Windows — Docker Desktop](#windows--docker-desktop-recommended). It is the supported Windows setup and keeps Linkdrop inside its Linux container. macOS and Linux users can use the native Python sections below.

## macOS — native Python

### 1. Install prerequisites

Install Homebrew if it is not already present, then install Python 3.12, FFmpeg, Deno, and Git:

```bash
brew install python@3.12 ffmpeg deno git
```

Homebrew may request your macOS administrator password while installing packages. Deno is used by `yt-dlp` for JavaScript challenges from some sources.

### 2. Install Linkdrop

```bash
git clone https://github.com/Zvareldric/Linkdrop.git
cd Linkdrop

python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt

export DOWNLOAD_TOKEN_SECRET="$(openssl rand -hex 32)"
PORT=5000 gunicorn --bind 127.0.0.1:5000 --workers 1 --threads 4 --timeout 0 app:app
```

Open `http://127.0.0.1:5000` on the same computer.

### 3. Private phone access (optional)

Install Tailscale on the Mac and phone, sign in to the same tailnet, then follow [Private Tailscale Access](TAILSCALE.md). Run Gunicorn on loopback instead of using the development server:

```bash
source .venv/bin/activate
export DOWNLOAD_TOKEN_SECRET="$(openssl rand -hex 32)"
PORT=5050 gunicorn --bind 127.0.0.1:5050 --workers 1 --threads 4 --timeout 0 app:app
```

## Linux — native Python

These commands target Ubuntu 24.04 or another distribution that provides Python 3.12. Use the equivalent package manager commands on other distributions.

### 1. Install prerequisites

```bash
sudo apt update
sudo apt install --yes git python3.12 python3.12-venv ffmpeg
```

Install Deno 2.3+ or Node.js 22+ using your distribution's supported package source. Deno is recommended for source sites that require a JavaScript runtime.

### 2. Install Linkdrop

```bash
git clone https://github.com/Zvareldric/Linkdrop.git
cd Linkdrop

python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt

export DOWNLOAD_TOKEN_SECRET="$(openssl rand -hex 32)"
PORT=5000 gunicorn --bind 127.0.0.1:5000 --workers 1 --threads 4 --timeout 0 app:app
```

Open `http://127.0.0.1:5000` from the same computer. `sudo` is required only to install packages; do not run Linkdrop itself as root.

For private access from another device, install Tailscale and use the loopback Gunicorn command in the macOS section or follow [Private Tailscale Access](TAILSCALE.md).

## Windows — Docker Desktop (recommended)

Gunicorn is a Unix-oriented server, so Docker Desktop is the supported Windows route. It runs Linkdrop in the repository's Linux container and publishes it only to the local computer.

### 1. Install prerequisites

Install:

- [Git for Windows](https://git-scm.com/download/win)
- [Docker Desktop](https://www.docker.com/products/docker-desktop/), using its WSL 2 backend

Docker Desktop may request an administrator password and Windows may prompt for firewall access. Keep the Docker port bound to `127.0.0.1` as shown below; do not create a public inbound firewall rule for Linkdrop.

### 2. Build and run Linkdrop

Open PowerShell and run:

```powershell
git clone https://github.com/Zvareldric/Linkdrop.git
Set-Location Linkdrop

$secret = [Convert]::ToHexString((1..32 | ForEach-Object { Get-Random -Maximum 256 }))
docker build -t linkdrop .
docker run --detach --name linkdrop --restart unless-stopped `
  --publish 127.0.0.1:5050:8080 `
  --env "DOWNLOAD_TOKEN_SECRET=$secret" `
  --env MAX_MEDIA_BYTES=2147483648 `
  linkdrop
```

Open `http://127.0.0.1:5050`. Check the runtime with:

```powershell
Invoke-RestMethod http://127.0.0.1:5050/api/health
```

### 3. Private phone access (optional)

Install Tailscale on Windows and the phone, sign in to the same tailnet, then configure Tailscale Serve to proxy local port `5050`. Follow [Private Tailscale Access](TAILSCALE.md) for the security model and troubleshooting. The Docker command above already keeps Linkdrop off the LAN by binding it to loopback.

## Verify an installation

Before processing real media, confirm the health endpoint reports `"ok": true`, `"ffmpeg": true`, and a JavaScript runtime when one is installed:

```bash
curl http://127.0.0.1:5000/api/health
```

For Docker or Tailscale, replace `5000` with `5050`.

## Permissions and optional access

| Item | Required? | Why |
|---|---|---|
| Administrator or `sudo` | Only during dependency installation | Installs package managers, FFmpeg, Docker, or Tailscale. |
| Write access to temporary storage | Yes | Downloads and transcodes use temporary working files that are cleaned up. |
| Outbound internet | Yes | `yt-dlp` reads metadata and fetches public media from the selected source. |
| Browser download or share action | Yes, when saving | The browser or OS presents the final save location. |
| Tailscale login | Only for private remote access | Restricts access to devices in the same tailnet. |
| Source-site cookies | No, optional | Some sources may require a session; treat cookies as secrets and never use a primary account on a public server. |

Do not expose this default installation publicly. A public service needs HTTPS, authentication, rate limiting, CPU/RAM/disk limits, monitoring, and review of platform terms before it accepts untrusted users.
