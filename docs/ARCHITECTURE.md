# Architecture

This document describes Linkdrop's runtime boundaries and media-processing flow.

## System context

Linkdrop is a stateless Flask application. The browser first requests metadata, then starts a separate signed download request. yt-dlp resolves source formats, FFmpeg performs merge or transcoding when needed, and the server streams the result directly to the browser.

```text
Public media source
        │
        ▼
Flask API ─── yt-dlp ─── FFmpeg
    │                       │
    ├── signed choices      └── temporary output
    │                              │
    └──────────────────────────────┘
                   │
                   ▼
                Browser
```

## Request flow

### Metadata analysis

1. `POST /api/info` receives a public HTTP or HTTPS URL.
2. URL validation rejects credentials, unsupported ports, localhost, and private-network destinations.
3. yt-dlp reads the source metadata without downloading the full media.
4. Linkdrop groups available video, audio, and photo choices.
5. Every choice receives a short-lived signed token.

### Media download

1. `GET /api/download/<token>` validates the token and source URL again.
2. A unique temporary working directory is created.
3. yt-dlp downloads the selected source streams.
4. FFmpeg merges or transcodes only when the selected output requires it.
5. The result is streamed as an attachment.
6. Temporary files are deleted after completion, cancellation, or failure.

When `progress=1` is present, progress frames, metadata, data chunks, and completion status travel in the same response. This keeps the operation bound to one application instance.

## Design decisions

### Stateless requests

Linkdrop does not store jobs in process memory and does not depend on daemon threads. This makes restarts safer and avoids routing problems when multiple instances serve the application.

### Temporary storage

Media files exist only while a request is active. Storage must still be large enough to hold source video, source audio, and merged output at the same time. The required peak space can exceed twice the final file size.

### One application worker

The default container uses one Gunicorn worker with four threads. Multiple workers can multiply memory and temporary-disk pressure during concurrent downloads. Scale only after setting explicit request, CPU, memory, and storage limits.

### Source fidelity

Resolution options come from extractor metadata. Linkdrop does not upscale video or reconstruct detail lost by the source. Photo files are copied without recompression whenever the extractor exposes the original asset.

## Security boundaries

- Signed tokens expire and cannot be modified without invalidating the signature.
- URL validation reduces server-side request forgery risk.
- Secrets and cookies remain server-side.
- Response headers disable caching and MIME sniffing for generated downloads.
- DRM, private access, and anti-bot controls are outside the application's scope.

## Scaling path

The current architecture targets personal or low-volume deployments. A public, high-volume service should separate API and media workers, add a queue, store outputs in expiring object storage, enforce per-user quotas, and apply rate limiting before accepting untrusted traffic.
