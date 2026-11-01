# Deployment Guide

Linkdrop performs CPU-intensive media processing and can transfer large files. A persistent VM or container host is the recommended production target.

Without a VPS, you can still publish Linkdrop from your own computer through [Cloudflare Tunnel](CLOUDFLARE_TUNNEL.md). It needs no open router ports, and a home connection is blocked by media platforms less often than a data-center IP.

For personal use, a public deployment is usually unnecessary. Run Linkdrop on a trusted computer and use [Tailscale Serve](TAILSCALE.md) for private HTTPS access from your own devices; Tailscale's Personal plan can be used without a hosting bill for eligible non-commercial personal use. Use a VPS only when Linkdrop must remain online while the personal computer is off. A VPS adds provider cost and requires the production controls below.

## Recommended capacity

Start with:

- 2 vCPU
- 4 GB RAM
- 20–50 GB temporary disk
- A stable outbound connection
- Docker 24 or newer

Increase disk capacity for long or high-resolution media. During a merge, source video, source audio, and final output can coexist.

## Docker

Build and run the production image:

```bash
docker build -t linkdrop .
docker run --detach \
  --name linkdrop \
  --restart unless-stopped \
  --publish 127.0.0.1:5050:8080 \
  --env DOWNLOAD_TOKEN_SECRET="replace-with-a-long-random-value" \
  --env MAX_MEDIA_BYTES=2147483648 \
  linkdrop
```

Place Caddy, nginx, Traefik, or the proxy provided by Dokku/Coolify in front of the local port. Terminate HTTPS at the proxy and allow long-lived streaming responses.

To keep a Docker installation current without rebuilding by hand, use the published image and the [automatic update scripts](AUTO_UPDATE.md).

## Dokku

Dokku can deploy the root `Dockerfile` directly:

```bash
dokku apps:create linkdrop
dokku config:set --no-restart linkdrop DOWNLOAD_TOKEN_SECRET="replace-with-a-long-random-value"
dokku config:set linkdrop MAX_MEDIA_BYTES=2147483648
git remote add dokku dokku@example.com:linkdrop
git push dokku main
```

Configure the domain and TLS certificate through Dokku after the first successful deployment.

## Coolify and CapRover

Use the repository's root `Dockerfile`, expose container port `8080`, and configure at least `DOWNLOAD_TOKEN_SECRET`. Keep the working directory on local temporary storage unless downloads must survive container restarts; Linkdrop normally removes files at the end of each request.

## Vercel

`Dockerfile.vercel` keeps the smaller serverless defaults. Vercel remains suitable for demos and smaller media, but function duration, scratch-disk capacity, response streaming, and source-site IP filtering can prevent large or high-resolution downloads from completing.

Use these variables:

```text
DOWNLOAD_TOKEN_SECRET=<long-random-value>
DOWNLOAD_TOKEN_MAX_AGE=900
MAX_MEDIA_BYTES=230686720
HTTP_TIMEOUT=30
```

Do not raise `MAX_MEDIA_BYTES` beyond the runtime's available scratch space.

Vercel stops a function after 300 seconds on the Hobby plan, and one vCPU converts VP9 or AV1 to H.264 slowly: a 2560×1440 video at 25 fps converted at about 0.18× real time in a measured run. Linkdrop therefore hides choices that cannot finish on the server and explains why below the list:

- Choices whose estimated size is above `MAX_MEDIA_BYTES`. This applies on every server.
- Choices that need a conversion longer than `TRANSCODE_TIME_LIMIT` seconds (default `240` on Vercel, `0`, meaning no limit, elsewhere). The estimate is width × height × frame rate × duration divided by `TRANSCODE_PIXEL_RATE` (default `15000000` pixels per second).

On the Pro plan, where functions can run longer, raise `TRANSCODE_TIME_LIMIT` to match the configured maximum duration minus about a minute for the download and transfer.

Vercel is not the preferred target for large downloads or high-resolution transcoding. Tailscale is a better fit for personal use, while a persistent container or VM is a better fit for a public multi-user service.

## Public access controls

Set these variables before Linkdrop is reachable by people you do not fully trust:

| Variable | Default | Purpose |
|---|---|---|
| `ACCESS_PASSWORD` | empty (open) | Requires a browser sign-in for every page and API call except `/api/health` and static files. |
| `ACCESS_USERNAME` | `linkdrop` | Username for the sign-in prompt. |
| `RATE_LIMIT_INFO` | `30/60` | Link analyses per client address, as `count/seconds`, or `off`. |
| `RATE_LIMIT_DOWNLOAD` | `20/600` | Single and batch downloads per client address, as `count/seconds`, or `off`. |
| `TRUSTED_PROXY_HEADER` | empty | Header that carries the client address from a trusted proxy, such as `CF-Connecting-IP` behind Cloudflare. Set it only when the proxy is the sole way to reach Linkdrop. |
| `FFMPEG_TIMEOUT` | `3600` | Seconds before a compatibility conversion is stopped. |
| `TRANSCODE_TIME_LIMIT` | `240` on Vercel, otherwise `0` | Hides converted video choices whose estimated conversion takes longer than this many seconds. `0` offers every choice. |
| `TRANSCODE_PIXEL_RATE` | `15000000` | Pixels per second the server converts, used for that estimate. Raise it on faster CPUs. |
| `JOB_RESULT_TTL` | `3600` | Seconds a finished file stays on the server so the browser can save or resume it. |
| `MAX_ACTIVE_JOBS` | `4` | Downloads processed at the same time. Further requests are refused until one finishes. |
| `GALLERY_CONCURRENCY` | `4` | Carousel items downloaded at the same time. Items are copied as-is, so this mainly uses network bandwidth. |
| `BATCH_CONCURRENCY` | `2` | Links in a multi-link package processed at the same time. Raise it only with spare CPU and temporary disk, because each item may need a conversion. |

Ten wrong passwords from one address block that address for 10 minutes. Rate-limit counters live in memory, so they reset on restart and assume the default single Gunicorn worker. Serve Linkdrop over HTTPS whenever a password is set, because browser sign-in sends the password with every request.

## Cookies

Some sources require an authenticated session. For a private server, `YTDLP_COOKIES_B64` may contain a base64-encoded Netscape cookie file. Treat it as a production secret, rotate it regularly, and never use a primary personal account on a public instance.

## Production checklist

- Generate a unique `DOWNLOAD_TOKEN_SECRET`.
- Keep `.env` and cookie exports outside Git.
- Confirm `/api/health` reports FFmpeg as available.
- Enforce HTTPS.
- Set CPU, memory, disk, and request concurrency limits.
- Set `ACCESS_PASSWORD` and review the rate limits before exposing the service publicly.
- Monitor free disk space and failed extractor requests.
- Test at least one permitted URL from each required platform.
- Verify that temporary files are removed after success and failure.
- Review the source platform's terms and applicable copyright rules.
