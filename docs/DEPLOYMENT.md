# Deployment Guide

Linkdrop performs CPU-intensive media processing and can transfer large files. A persistent VM or container host is the recommended production target.

For a single user, a public deployment is usually unnecessary. Run Linkdrop on a trusted computer and use [Tailscale Serve](TAILSCALE.md) for private HTTPS access from your own devices.

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

Vercel is not the preferred target for large downloads or high-resolution transcoding. Tailscale is a better fit for personal use, while a persistent container or VM is a better fit for a public multi-user service.

## Cookies

Some sources require an authenticated session. For a private server, `YTDLP_COOKIES_B64` may contain a base64-encoded Netscape cookie file. Treat it as a production secret, rotate it regularly, and never use a primary personal account on a public instance.

## Production checklist

- Generate a unique `DOWNLOAD_TOKEN_SECRET`.
- Keep `.env` and cookie exports outside Git.
- Confirm `/api/health` reports FFmpeg as available.
- Enforce HTTPS.
- Set CPU, memory, disk, and request concurrency limits.
- Add rate limiting before exposing the service publicly.
- Monitor free disk space and failed extractor requests.
- Test at least one permitted URL from each required platform.
- Verify that temporary files are removed after success and failure.
- Review the source platform's terms and applicable copyright rules.
