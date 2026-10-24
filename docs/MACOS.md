# Linkdrop on macOS without Docker

**English** · [Bahasa Indonesia](MACOS.id.md)

On a Mac, Linkdrop is lightest when it runs directly with Python, without Docker. Docker Desktop runs a Linux virtual machine that reserves several GB of RAM, while Linkdrop itself uses about 100–200 MB when idle.

`scripts/macos-native.sh` installs Linkdrop as a macOS background service (launchd):

- Linkdrop runs on `http://127.0.0.1:5050`, starts at login, and restarts automatically if it stops. Tailscale Serve or Cloudflare Tunnel keep pointing at the same port.
- Every 15 minutes it checks GitHub and updates itself. If a new version fails to start, the previous version is started again and the failed version is skipped until a fix is published.
- yt-dlp is upgraded once a day, because video platforms change often.

There is no Dock or menu bar icon; Linkdrop runs quietly in the background.

## Requirements

- [Homebrew](https://brew.sh)
- A clone of this repository, for example in `~/development/Linkdrop`

Python 3.12, FFmpeg, and Deno are installed through Homebrew when missing.

## Install

If an older Linkdrop is still listening on port 5050 (for example a launchd service you created yourself or `gunicorn` in Terminal), stop it first. Check with:

```bash
lsof -nP -iTCP:5050 -sTCP:LISTEN
```

Then, from the repository folder:

```bash
git pull
./scripts/macos-native.sh install
```

This prepares an application copy in `~/.linkdrop/app`, a Python virtualenv in `~/.linkdrop/venv`, and uses the settings in `~/.linkdrop/linkdrop.env` (created when missing). If you previously ran Linkdrop with Docker, the `linkdrop` container and the Docker-based automatic updates are stopped and their settings reused. You can then quit Docker Desktop, and turn off **Start Docker Desktop when you sign in** if you do not need it for anything else.

Expected output:

```text
Linkdrop berjalan tanpa Docker di http://127.0.0.1:5050 (versi 1a2b3c4).
Pembaruan otomatis aktif setiap 15 menit. Log: ~/.linkdrop/logs/update.log
```

## Other commands

```bash
./scripts/macos-native.sh status      # running version and launchd services
./scripts/macos-native.sh update      # update now instead of waiting 15 minutes
./scripts/macos-native.sh uninstall   # stop and remove the services
```

Logs are in `~/.linkdrop/logs/server.log` (application) and `~/.linkdrop/logs/update.log` (updates).

## Change a setting

Edit `~/.linkdrop/linkdrop.env` (for example `ACCESS_PASSWORD` or `MAX_MEDIA_BYTES`), then restart Linkdrop:

```bash
launchctl kickstart -k gui/$(id -u)/com.linkdrop.server
```

`LINKDROP_PORT` in the same file sets the port; run `./scripts/macos-native.sh install` again after changing it.

## Daily use

Just sign in to the Mac. Linkdrop and Tailscale start on their own (turn on **Launch at login** in the Tailscale menu). Keep the Mac awake while you want to use it from other devices: in **System Settings → Battery**, turn on **Prevent automatic sleeping when the display is off** for the power adapter.
