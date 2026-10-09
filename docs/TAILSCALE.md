# Private Access with Tailscale

**English** · [Bahasa Indonesia](TAILSCALE.id.md)

Tailscale Serve is the recommended way to use Linkdrop privately from a phone, tablet, or another computer. Linkdrop continues to run on your own computer, while Tailscale provides an encrypted HTTPS address that is available only to devices in the same tailnet.

This setup is intended for personal use. It is not a public deployment, does not copy Linkdrop to a cloud server, and does not keep the application online when the host computer is off or asleep.

At the time this guide was updated, Tailscale's Personal plan is available at no cost for non-commercial personal use. Review the current [Tailscale plan terms](https://tailscale.com/pricing), because service limits can change.

## What you need

- Linkdrop installed and working locally
- FFmpeg and ffprobe available on the host
- A Tailscale account
- Tailscale installed on the host and each client device
- The devices signed in to the same tailnet

## 1. Install Tailscale

On macOS, install the desktop application:

```bash
brew install --cask tailscale-app
open -a Tailscale
```

Sign in from the Tailscale menu-bar application. Install Tailscale from the App Store or Play Store on the phone and sign in with the same account.

Official installers for other operating systems are available from [tailscale.com/download](https://tailscale.com/download).

## 2. Start Linkdrop

From the repository directory:

```bash
source .venv/bin/activate
export DOWNLOAD_TOKEN_SECRET="$(openssl rand -hex 32)"
PORT=5050 gunicorn \
  --bind 127.0.0.1:5050 \
  --workers 1 \
  --threads 4 \
  --timeout 0 \
  --access-logfile - \
  app:app
```

Keep this terminal open. Confirm the local server is healthy:

```bash
curl http://127.0.0.1:5050/api/health
```

The response should include `"ok": true` and `"ffmpeg": true`.

## 3. Enable private HTTPS access

Open another terminal and run:

```bash
tailscale serve --bg 5050
tailscale serve status
```

The first command may print a Tailscale consent URL. Open it once to enable Serve for the tailnet, then repeat the command. The status output will show an address similar to:

```text
https://your-device.your-tailnet.ts.net
|-- / proxy http://127.0.0.1:5050
```

Open that HTTPS address on any device connected to the same tailnet.

## 4. Use Linkdrop on a phone

1. Open the Tailscale application.
2. Confirm its status is **Connected**.
3. Open the HTTPS address reported by `tailscale serve status`.
4. Paste a permitted public media link and choose a format.
5. Wait until processing and transfer reach 100%.
6. In the save dialog, keep the default filename or edit it; Linkdrop retains the original extension. Select **Simpan sekarang**. On mobile browsers, choose **Save to Files** or the equivalent action from the share sheet.

The phone does not need to use the same Wi-Fi as the host. Mobile data works as long as both devices are connected to the same tailnet.

## When an always-on server is needed

Use a VPS or persistent container host if the personal computer cannot stay powered on. A VPS has provider costs and must be operated like a server: use a unique `DOWNLOAD_TOKEN_SECRET`, enough temporary disk, HTTPS, rate limiting, and system updates. Tailscale can still run on the VPS to keep access private instead of exposing Linkdrop to the public internet. Read the [Deployment Guide](DEPLOYMENT.md) before choosing this option.

## Daily operation

Tailscale Serve keeps its proxy configuration, but Linkdrop itself must be running. After restarting the computer:

```bash
cd /path/to/Linkdrop
source .venv/bin/activate
PORT=5050 gunicorn --bind 127.0.0.1:5050 --workers 1 --threads 4 --timeout 0 app:app
```

Also confirm that the Tailscale application is connected. Prevent the host from sleeping during long downloads or video transcoding.

## Stop private access

Stop the Linkdrop process with `Ctrl+C`. To remove the Tailscale Serve configuration as well, run:

```bash
tailscale serve reset
```

Verify the result with:

```bash
tailscale serve status
```

## Security notes

- `tailscale serve` is private to the tailnet. Do not replace it with `tailscale funnel` unless you intentionally want a public internet service.
- Bind Gunicorn to `127.0.0.1` when using Serve so the application is not exposed directly to the local network.
- Use a random `DOWNLOAD_TOKEN_SECRET` and never commit it.
- Keep cookies, `.env`, and exported login sessions outside Git.
- Only download media you own or are permitted to download.

## Troubleshooting

### The HTTPS address does not open

Check each layer in order:

```bash
curl http://127.0.0.1:5050/api/health
tailscale status
tailscale serve status
```

Confirm that Linkdrop is still running, Tailscale is connected on both devices, both devices belong to the same tailnet, and the host is awake.

### `ERR_ADDRESS_UNREACHABLE`

The client is usually not connected to Tailscale, is signed in to another tailnet, or is trying to open a LAN address such as `10.x.x.x`. Connect the client to Tailscale and use the `https://...ts.net` address from `tailscale serve status`.

### The page opens but downloads stop

Keep the browser page open and prevent the host from sleeping. High-resolution VP9 or AV1 sources may require an additional H.264 compatibility conversion, so progress can remain in the processing stage for a while.

### The video downloads but an older file will not play

Files downloaded before the compatibility fix may still contain VP9 inside an MP4 container. Download the media again so Linkdrop can validate and, when necessary, convert it to H.264/AAC.
