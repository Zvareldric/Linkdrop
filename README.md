# Linkdrop

[![CI](https://github.com/Zvareldric/Linkdrop/actions/workflows/ci.yml/badge.svg)](https://github.com/Zvareldric/Linkdrop/actions/workflows/ci.yml)
![Python](https://img.shields.io/badge/Python-3.12-3776AB?logo=python&logoColor=white)
![Flask](https://img.shields.io/badge/Flask-3.x-000000?logo=flask&logoColor=white)
![Playwright](https://img.shields.io/badge/Tested_with-Playwright-2EAD33?logo=playwright&logoColor=white)

**English** · [Bahasa Indonesia](README.id.md)

Linkdrop is a responsive web application for inspecting and downloading public media from YouTube, Instagram, TikTok, X, Facebook, and other websites supported by [yt-dlp](https://github.com/yt-dlp/yt-dlp). It exposes the formats reported by the source, streams download progress to the browser, and produces device-friendly output with FFmpeg.

> Use Linkdrop only for media you own, media in the public domain, or media you have permission to download. Linkdrop does not bypass DRM, private access, CAPTCHA, or platform restrictions.

## Highlights

- Lists real source resolutions from lowest to highest, including 1440p and 2160p when available.
- Combines the selected video quality with the best available audio.
- Converts incompatible MP4 streams to H.264/AAC while preserving the selected resolution.
- Supports source audio and MP3 output at 128, 192, or 320 kbps.
- Preserves original photos and packages multi-image posts as ZIP files.
- Keeps single-link downloads as the default and offers an optional multi-link queue with per-item selection.
- Splits large selections into storage-aware ZIP packages and continues when individual items fail.
- Displays live download, processing, and transfer progress with cancellation support.
- Uses an explicit save action that works with desktop pickers, mobile share sheets, and browser downloads.
- Uses signed, expiring download tokens and blocks private-network URLs.
- Provides a responsive UI, PWA metadata, iOS safe areas, accessible focus states, and reduced-motion support.
- Runs without a database or persistent job state.

## How it works

```text
Browser
  ├── POST /api/info
  │     └── validate URL → inspect metadata → return signed choices
  ├── GET /api/download/<token>?progress=1
  │     └── download → merge/transcode → stream file → clean temporary data
  └── POST /api/batch/download?progress=1
        └── validate choices → process sequentially → package ZIP → clean temporary data
```

The download protocol carries progress events and file bytes in one HTTP response. Each short-lived worker remains bound to that response, without a persistent job registry or permanent output storage.

### Multiple links

`Single link` remains the default mode. Select `Multiple links` to paste one public URL per line, analyze at most two URLs concurrently, choose the output for each item, and download only the checked items as ZIP. More than 10 selections show a warning; packages contain at most 30 media and are split at 80% of the safe temporary-storage limit. Package downloads remain sequential so the next ZIP does not replace an unsaved file.

## Requirements

- Python 3.12 or newer
- FFmpeg
- Deno 2.3+ or Node.js 22+ for JavaScript challenges used by some YouTube formats
- Node.js 20+ only when running the Playwright test suite

## Quick start

```bash
git clone https://github.com/Zvareldric/Linkdrop.git
cd Linkdrop

python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env
export DOWNLOAD_TOKEN_SECRET="$(openssl rand -hex 32)"
python app.py
```

Open [http://127.0.0.1:5000](http://127.0.0.1:5000).

On macOS, install the native dependencies with:

```bash
brew install ffmpeg deno
```

## Private access from a phone

For personal use, the recommended setup is to keep Linkdrop on your computer and publish it only inside your private [Tailscale](https://tailscale.com/) network. This avoids a public deployment and works from mobile data or another Wi-Fi network.

Start Linkdrop on port `5050`:

```bash
source .venv/bin/activate
PORT=5050 gunicorn --bind 127.0.0.1:5050 --workers 1 --threads 4 --timeout 0 app:app
```

In another terminal, expose the local service to your tailnet:

```bash
tailscale serve --bg 5050
tailscale serve status
```

Install Tailscale on the phone, sign in to the same tailnet, and open the HTTPS address printed by `tailscale serve`. The computer must remain powered on, awake, connected to Tailscale, and running Linkdrop.

See the complete [Private Tailscale Access Guide](docs/TAILSCALE.md) for installation, security notes, shutdown commands, and troubleshooting.

## Configuration

| Variable | Default | Purpose |
|---|---:|---|
| `DOWNLOAD_TOKEN_SECRET` | Development fallback | Signing key; required in production |
| `DOWNLOAD_TOKEN_MAX_AGE` | `900` | Download token lifetime in seconds |
| `MAX_MEDIA_BYTES` | 2 GB locally; 220 MB on Vercel | Maximum working media size |
| `HTTP_TIMEOUT` | `30` | Source connection timeout in seconds |
| `DOWNLOAD_DIR` | `downloads/` or `/tmp/linkdrop` | Temporary working directory |
| `YTDLP_COOKIES_B64` | Empty | Base64-encoded Netscape cookie file for a server |
| `YTDLP_COOKIES_FILE` | Empty | Local Netscape cookie file path |
| `YTDLP_COOKIES_FROM_BROWSER` | Empty | Local browser cookie source |
| `FFMPEG_LOCATION` | System `PATH` | Custom FFmpeg path |
| `YTDLP_JS_RUNTIME` | Auto-detected | JavaScript runtime, such as `deno` |

Never commit `.env`, browser cookies, or exported account sessions. Do not use a primary account's cookies on a public instance.

## Tests

Run the Python suite:

```bash
source .venv/bin/activate
python -m unittest discover -s tests -v
```

Run the desktop and mobile browser suite:

```bash
npm ci
npx playwright install chromium webkit
npm run test:e2e
```

The browser matrix covers desktop Chrome, Pixel 7, and iPhone 13/WebKit.

## Container deployment

The root `Dockerfile` is suitable for Docker hosts, Dokku, Coolify, and CapRover:

```bash
docker build -t linkdrop .
docker run --rm \
  -p 5050:8080 \
  -e DOWNLOAD_TOKEN_SECRET="$(openssl rand -hex 32)" \
  linkdrop
```

For public deployment, a persistent VM or container host is more reliable than a short-lived serverless function because large media requires CPU time, temporary disk space, and sustained streaming. See [Deployment Guide](docs/DEPLOYMENT.md) for production settings and provider-specific trade-offs.

## Project structure

```text
Linkdrop/
├── .github/workflows/ci.yml   # Automated unit and browser tests
├── docs/                      # Architecture, deployment, PRD, and tooling notes
├── downloads/                 # Ignored local working directory
├── static/                    # PWA manifest and visual assets
├── templates/                 # Responsive web interface
├── tests/
│   └── e2e/                   # Playwright desktop and mobile tests
├── app.py                     # Flask application and media pipeline
├── Dockerfile                 # General-purpose production image
├── Dockerfile.vercel          # Vercel-oriented image
├── playwright.config.js       # Browser test matrix
└── requirements.txt           # Python runtime dependencies
```

## API

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/` | Web interface |
| `GET` | `/api/health` | Runtime and FFmpeg health check |
| `POST` | `/api/info` | Validate a public URL and return available formats |
| `GET` | `/api/download/<token>` | Prepare and stream the selected output |
| `POST` | `/api/batch/download` | Process signed selections sequentially and stream a ZIP package |

Add `?progress=1` to the download endpoint to use Linkdrop's framed progress stream.

## Known limitations

- Platform extractors can break when source websites change.
- Some sources require login, cookies, a PO token, or a residential IP address.
- Cloud data-center IP addresses may be throttled or blocked.
- MP3 transcoding does not restore detail missing from the source.
- A quality label is a maximum output resolution, not an upscale target.

## Documentation

- [Architecture](docs/ARCHITECTURE.md)
- [Deployment Guide](docs/DEPLOYMENT.md)
- [Private Tailscale Access](docs/TAILSCALE.md)
- [Product Requirements](docs/PRD.md)
- [Tooling Decisions](docs/TOOLING.md)

## Maintainer

Maintained by [Zvareldric](https://github.com/Zvareldric).
