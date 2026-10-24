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

1. `POST /api/download/<token>` validates the token and source URL again and starts a background job. The response contains an unguessable job ID.
2. A unique temporary working directory is created for the job.
3. yt-dlp downloads the selected source streams.
4. FFmpeg merges the selected streams and validates MP4 codec compatibility.
5. VP9, AV1, unsupported pixel formats, and non-AAC audio are converted to H.264/AAC when required for broad device playback.
6. The browser polls `GET /api/jobs/<id>` for progress. When the job is ready, a save dialog starts with the server-provided filename, permits editing the name, and preserves the original extension. Saving starts a normal browser download of `GET /api/jobs/<id>/file?name=...`.
7. `DELETE /api/jobs/<id>` cancels a job. Failed jobs delete their files immediately; finished files are deleted `JOB_RESULT_TTL` seconds (default one hour) after completion, and leftover job folders are removed when the server starts.

The job does not depend on the browser connection, so it keeps running when a phone locks its screen, puts the tab in the background, or switches networks. Polling waits quietly while the network is unavailable and checks again as soon as the page is visible. The job ID is kept in `localStorage`, so a reloaded or reopened page picks up the same job. The file endpoint answers HTTP range requests, which lets the browser's download manager resume an interrupted transfer. At most `MAX_ACTIVE_JOBS` (default 4) jobs run at once.

On Vercel, where requests may reach different instances, jobs are disabled. `GET /api/download/<token>?progress=1` is used instead: progress frames, metadata, data chunks, and completion status travel in the same response, and the last progress frame is repeated every 15 seconds so that proxies do not close an idle stream. If the browser disconnects before the work finishes, the temporary directory is removed as soon as the worker stops.

### Multi-link package

1. The browser reuses `POST /api/info` with at most two concurrent analyses.
2. The user explicitly selects the items and output choices to include.
3. The browser groups signed choices at 80% of the safe package limit and sends at most 30 tokens to `POST /api/batch/download`.
4. The server validates every token before processing starts, then processes the selected links with the existing media pipeline, `BATCH_CONCURRENCY` (default 2) at a time.
5. Successful files are stored without recompression in a flat ZIP. Multi-item galleries are placed in a unique folder in the outer ZIP instead of becoming nested ZIP files; individual failures are written to `linkdrop-report.txt` without stopping other items.
6. Available disk space is divided by 2.5 and capped by `MAX_MEDIA_BYTES` to reserve room for source, conversion, and archive files.
7. Each ZIP is built by a background job (`POST /api/batch/download?job=1`) and saved like a single download. On Vercel it is streamed with the framed progress protocol instead.

## Design decisions

### In-memory jobs

Linkdrop keeps its job registry in memory and does not persist it. This keeps the deployment simple but requires a single application process (the default `gunicorn --workers 1`), and jobs that are running during a restart are lost; the browser then asks the user to download again.

### Temporary storage

Media files exist only while a request is active. Storage must still be large enough to hold source video, source audio, and merged output at the same time. The required peak space can exceed twice the final file size.

### One application worker

The default container uses one Gunicorn worker with four threads. Multiple workers can multiply memory and temporary-disk pressure during concurrent downloads. Scale only after setting explicit request, CPU, memory, and storage limits.

### Source fidelity

Resolution options come from extractor metadata. Linkdrop does not upscale video or reconstruct detail lost by the source. Photo files are copied without recompression whenever the extractor exposes the original asset.

### Original quality first

Every re-encode loses some quality, so Linkdrop avoids it wherever the source allows:

- **Video.** Every video is delivered as MP4. When the source offers H.264 at a resolution, that stream is downloaded and merged into MP4 without re-encoding. Compatible H.264/AAC streams in another container are moved into MP4 without re-encoding. Where only VP9 or AV1 exists (common above 1080p on YouTube), the video is converted to H.264, and the choice is labeled as converted.
- **Audio.** "Original audio" downloads the best M4A (AAC) stream untouched and is offered only when the source has one; WebM/Opus streams are never delivered. MP3 choices remain available and are labeled as converted.
- **Photos.** Photo files are always copied without recompression.

Re-encodes that still happen use x264 `-preset fast -crf 18`. Faster presets were measured to lower quality, so they are not used by default.

### Subtitles

When the source offers Indonesian or English subtitles, `/api/info` returns a signed subtitle choice for each language. Creator-provided tracks are preferred; automatic captions are offered only in the video's original spoken language, never YouTube's machine translations. The browser appends the selected subtitle token to the video download URL, and the server rejects it unless it was issued for the same video.

During the download, yt-dlp fetches the track (json3 on YouTube). Linkdrop converts json3 to SRT, ending each automatic-caption line when the next begins so rolling captions show one clean line instead of stacked duplicates. Two display modes follow:

- **Toggleable (default).** FFmpeg copies the audio and video streams unchanged and adds the subtitle as a separate track (`mov_text` in MP4) tagged with its language and marked as the default track. This takes seconds, and the video is bit-identical to the source. Most players show it as a CC option; some Android gallery apps ignore subtitle tracks.
- **Burned in.** FFmpeg renders the text into the picture with libass in a white-on-translucent-black style similar to YouTube's captions. This always re-encodes the video to H.264 MP4, so it takes longer, slightly lowers quality, and reports its progress as a percentage. Container images install `fonts-dejavu-core` because libass cannot draw text without a font. Multi-link packages do not offer subtitles.

### Playback compatibility

An `.mp4` extension does not guarantee that a device can decode the streams inside it. Linkdrop inspects completed MP4 files with ffprobe. Compatible H.264/AAC output is passed through; incompatible video is transcoded to H.264 with `yuv420p`, AAC audio, and fast-start metadata while retaining the selected resolution.

## Security boundaries

- Signed tokens expire and cannot be modified without invalidating the signature.
- URL validation rejects private destinations before work starts. While yt-dlp and the photo downloader run, every DNS lookup made by the request thread drops private and local addresses, so redirects, manifests, fragments, and DNS answers that change after validation cannot reach the internal network. A configured outbound proxy resolves hostnames itself and must enforce the same rule.
- An optional password (`ACCESS_PASSWORD`) and per-client rate limits protect public deployments.
- Secrets and cookies remain server-side.
- Response headers disable caching and MIME sniffing for generated downloads.
- DRM, private access, and anti-bot controls are outside the application's scope.

## Scaling path

The current architecture targets personal or low-volume deployments. A public, high-volume service should separate API and media workers, add a queue, store outputs in expiring object storage, enforce per-user quotas, and move rate limiting to shared storage before accepting untrusted traffic.
