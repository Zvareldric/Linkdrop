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
4. FFmpeg merges the selected streams and validates MP4 codec compatibility.
5. VP9, AV1, unsupported pixel formats, and non-AAC audio are converted to H.264/AAC when required for broad device playback.
6. The result is streamed to browser-managed temporary storage and exposed through a save dialog. The dialog starts with the server-provided filename, permits editing the name, and preserves the original extension before invoking the browser or operating system save flow.
7. Temporary files are deleted after completion, cancellation, or failure.

When `progress=1` is present, progress frames, metadata, data chunks, and completion status travel in the same response. This keeps the operation bound to one application instance.

### Multi-link package

1. The browser reuses `POST /api/info` with at most two concurrent analyses.
2. The user explicitly selects the items and output choices to include.
3. The browser groups signed choices at 80% of the safe package limit and sends at most 30 tokens to `POST /api/batch/download`.
4. The server validates every token before processing starts, then downloads each selected link sequentially with the existing media pipeline.
5. Successful files are stored without recompression in a flat ZIP. Multi-item galleries are placed in a unique folder in the outer ZIP instead of becoming nested ZIP files; individual failures are written to `linkdrop-report.txt` without stopping other items.
6. Available disk space is divided by 2.5 and capped by `MAX_MEDIA_BYTES` to reserve room for source, conversion, and archive files.
7. Each ZIP is streamed with the same framed progress protocol and all temporary data is removed afterward.

## Design decisions

### Stateless requests

Linkdrop does not keep a persistent job registry. A short-lived worker thread is scoped to each active progress response, so the browser connection remains the owner of the work and cancellation can clean its temporary directory.

### Temporary storage

Media files exist only while a request is active. Storage must still be large enough to hold source video, source audio, and merged output at the same time. The required peak space can exceed twice the final file size.

### One application worker

The default container uses one Gunicorn worker with four threads. Multiple workers can multiply memory and temporary-disk pressure during concurrent downloads. Scale only after setting explicit request, CPU, memory, and storage limits.

### Source fidelity

Resolution options come from extractor metadata. Linkdrop does not upscale video or reconstruct detail lost by the source. Photo files are copied without recompression whenever the extractor exposes the original asset.

### Playback compatibility

An `.mp4` extension does not guarantee that a device can decode the streams inside it. Linkdrop inspects completed MP4 files with ffprobe. Compatible H.264/AAC output is passed through; incompatible video is transcoded to H.264 with `yuv420p`, AAC audio, and fast-start metadata while retaining the selected resolution.

## Security boundaries

- Signed tokens expire and cannot be modified without invalidating the signature.
- URL validation reduces server-side request forgery risk.
- Secrets and cookies remain server-side.
- Response headers disable caching and MIME sniffing for generated downloads.
- DRM, private access, and anti-bot controls are outside the application's scope.

## Scaling path

The current architecture targets personal or low-volume deployments. A public, high-volume service should separate API and media workers, add a queue, store outputs in expiring object storage, enforce per-user quotas, and apply rate limiting before accepting untrusted traffic.
