# Automatic Updates

**English** · [Bahasa Indonesia](AUTO_UPDATE.id.md)

Linkdrop on your computer can update itself whenever the GitHub repository changes. Tailscale Serve and Cloudflare Tunnel keep forwarding to the same local port, so your private or public address shows the new version without any other change.

## How it works

1. After every change to `main` passes the tests, GitHub Actions publishes a Docker image to `ghcr.io/zvareldric/linkdrop:latest`. The image is also rebuilt every Monday, so it picks up new yt-dlp releases even when the code has not changed.
2. An update script on your computer runs every 15 minutes. It pulls the image and, only when a newer one exists, replaces the `linkdrop` container with the same settings and port.
3. If the new version does not answer its health check within a minute, the script starts the previous version again and skips that image until a newer one is published.

A new version usually reaches your computer within about 30 minutes of being merged: around 10–15 minutes for the image build, plus up to 15 minutes until the next scheduled check.

## Requirements

- Docker Desktop (Windows, macOS) or Docker Engine (Linux), running while you are signed in
- A clone of this repository, which contains the update scripts

If you currently run Linkdrop natively with `gunicorn`, stop it first (press Ctrl+C in its terminal). The update script then starts Linkdrop in Docker on the same port, `5050`, so `tailscale serve` keeps working.

## 1. Make the image public (repository owner, once)

The first publish creates the package `linkdrop` under the owner's GitHub profile. If `docker pull ghcr.io/zvareldric/linkdrop:latest` reports `denied` or `unauthorized`, open the package on GitHub (profile → **Packages** → **linkdrop**), choose **Package settings** → **Change visibility**, and set it to **Public**.

## 2. Run the update script once

Update your clone first so it contains the scripts.

Windows (PowerShell, in the `Linkdrop` folder):

```powershell
git pull
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\update-linkdrop.ps1
```

macOS and Linux:

```bash
git pull
./scripts/update-linkdrop.sh
```

On the first run the script copies the settings of an existing `linkdrop` container (token secret, password, size limit, port, and similar) into `~/.linkdrop/linkdrop.env` (`%USERPROFILE%\.linkdrop\linkdrop.env` on Windows). Without an existing container it creates that file with a new token secret and port `5050`. The file holds secrets, so keep it private.

The script prints `Linkdrop diperbarui ke versi …` when it installs a version and `Linkdrop sudah versi terbaru.` when nothing changed.

## 3. Schedule it every 15 minutes

Windows (PowerShell, in the `Linkdrop` folder):

```powershell
$script = (Resolve-Path .\scripts\update-linkdrop.ps1).Path
$action = New-ScheduledTaskAction -Execute "powershell.exe" `
  -Argument "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$script`""
$trigger = New-ScheduledTaskTrigger -Once -At (Get-Date) -RepetitionInterval (New-TimeSpan -Minutes 15)
Register-ScheduledTask -TaskName "Linkdrop auto-update" -Action $action -Trigger $trigger `
  -Description "Updates Linkdrop from ghcr.io"
```

The task runs while you are signed in, which is also when Docker Desktop runs. A PowerShell window may flash briefly at each run. Messages are written to `%USERPROFILE%\.linkdrop\update.log`.

macOS and Linux: run `crontab -e` and add this line, replacing the path with your clone:

```text
*/15 * * * * /path/to/Linkdrop/scripts/update-linkdrop.sh >> "$HOME/.linkdrop/update.log" 2>&1
```

## Check which version is running

Open `/api/health` on your Linkdrop address, for example `https://linkdrop.your-tailnet.ts.net/api/health`. The `version` field shows the Git commit the image was built from; it matches the latest commit on `main` once the update has arrived. A locally built image reports `dev`.

## Change a setting

Edit `~/.linkdrop/linkdrop.env` (for example `ACCESS_PASSWORD`), then remove the container and run the script once so it starts again with the new settings:

```bash
docker rm --force linkdrop
./scripts/update-linkdrop.sh
```

On Windows, run the `.ps1` script instead. `LINKDROP_PORT` in the same file sets the local port.

## Turn it off

- Windows: `Unregister-ScheduledTask -TaskName "Linkdrop auto-update" -Confirm:$false`
- macOS and Linux: remove the line with `crontab -e`

The running container keeps working; it simply stops receiving updates.

## Notes

- GitHub pauses scheduled workflows in repositories without activity for 60 days. Merges keep publishing either way, and **Actions → CI → Run workflow** publishes a fresh image on demand.
- The image is built for `linux/amd64` and `linux/arm64`, so it runs on Intel and AMD PCs and on Apple Silicon Macs.
- If your computer sleeps, the next check runs after it wakes up.
