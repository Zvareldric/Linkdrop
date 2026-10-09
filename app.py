from __future__ import annotations

import base64
import ipaddress
import json
import mimetypes
import os
import queue
import shutil
import socket
import struct
import subprocess
import tempfile
import threading
import time
import zipfile
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import parse_qsl, quote, urlencode, urljoin, urlparse, urlunparse

import requests
import yt_dlp
import yt_dlp.cookies as ytdlp_cookies
from yt_dlp.extractor.instagram import InstagramIE
from yt_dlp.extractor.twitter import TwitterIE
from yt_dlp.utils import ExtractorError
from flask import Flask, Response, jsonify, render_template, request, stream_with_context
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from werkzeug.utils import secure_filename


BASE_DIR = Path(__file__).resolve().parent
IS_VERCEL = bool(os.environ.get("VERCEL"))
WORK_DIR = Path(os.environ.get("DOWNLOAD_DIR", "/tmp/linkdrop" if IS_VERCEL else BASE_DIR / "downloads"))
DEFAULT_MAX_MEDIA_BYTES = (220 if IS_VERCEL else 2048) * 1024 * 1024
MAX_MEDIA_BYTES = int(os.environ.get("MAX_MEDIA_BYTES", DEFAULT_MAX_MEDIA_BYTES))
TOKEN_MAX_AGE = int(os.environ.get("DOWNLOAD_TOKEN_MAX_AGE", 15 * 60))
HTTP_TIMEOUT = int(os.environ.get("HTTP_TIMEOUT", 30))
CHUNK_SIZE = 1024 * 1024
PROGRESS_MIME = "application/vnd.linkdrop.progress"
MAX_BATCH_ITEMS = 30
BATCH_WARNING_ITEMS = 10
BATCH_DISK_FACTOR = 2.5
MAX_GALLERY_ITEMS = 20

WORK_DIR.mkdir(parents=True, exist_ok=True)

app = Flask(__name__)
app.config["JSON_SORT_KEYS"] = False

_configured_token_secret = os.environ.get("DOWNLOAD_TOKEN_SECRET", "").strip()
if IS_VERCEL and not _configured_token_secret:
    raise RuntimeError("DOWNLOAD_TOKEN_SECRET wajib diatur pada deployment Vercel.")
_token_secret = _configured_token_secret or "linkdrop-local-development-secret"
_signer = URLSafeTimedSerializer(_token_secret, salt="linkdrop-download-v1")


def _register_arc_cookie_support() -> None:
    """Teach yt-dlp's Chromium cookie loader where Arc stores its profile.

    Arc uses Chromium's cookie schema but is not listed by older yt-dlp
    releases. The adapter keeps all decryption inside yt-dlp and macOS
    Keychain; no cookie values are logged or copied by this application.
    """

    if "arc" in ytdlp_cookies.SUPPORTED_BROWSERS:
        return

    original_settings = ytdlp_cookies._get_chromium_based_browser_settings

    def settings(browser_name):
        if browser_name == "arc":
            if os.sys.platform != "darwin":
                raise ValueError("Arc cookie extraction is currently supported only on macOS")
            return {
                "browser_dir": os.path.expanduser(
                    "~/Library/Application Support/Arc/User Data"
                ),
                "keyring_name": "Arc",
                "supports_profiles": True,
            }
        return original_settings(browser_name)

    ytdlp_cookies.CHROMIUM_BASED_BROWSERS.add("arc")
    ytdlp_cookies.SUPPORTED_BROWSERS.add("arc")
    ytdlp_cookies._get_chromium_based_browser_settings = settings


_register_arc_cookie_support()


def _register_instagram_image_support() -> None:
    """Expose Instagram carousel photos as downloadable image formats.

    yt-dlp's Instagram extractor currently returns carousel photos without
    formats even though the API response includes their original image URLs.
    That makes mixed and image-only posts fail with "No video formats found".
    Keep yt-dlp's normal video handling and only fill the missing image case.
    """

    current_extractor = InstagramIE._extract_product_media
    if getattr(current_extractor, "_linkdrop_image_support", False):
        return

    def extract_product_media(extractor, product_media):
        result = current_extractor(extractor, product_media)
        # Instagram's video_versions are progressive MP4 files with H.264
        # video and AAC audio, but some yt-dlp releases leave both codec fields
        # empty. Without this metadata yt-dlp prefers a VP9 DASH stream instead.
        progressive_urls = {
            item.get("url")
            for item in product_media.get("video_versions") or []
            if item.get("url")
        }
        if result and progressive_urls:
            for media_format in result.get("formats") or []:
                if media_format.get("url") not in progressive_urls:
                    continue
                media_format["vcodec"] = media_format.get("vcodec") or "h264"
                media_format["acodec"] = media_format.get("acodec") or "aac"
                media_format["_linkdrop_progressive"] = True
            return result

        # Newer yt-dlp versions return thumbnail metadata for photos, but still
        # no downloadable formats. Continue into Linkdrop's image adapter.
        if result and result.get("formats"):
            return result

        candidates = (product_media.get("image_versions2") or {}).get("candidates") or []
        formats = []
        thumbnails = []
        for index, candidate in enumerate(candidates):
            image_url = candidate.get("url")
            if not image_url:
                continue
            extension = Path(urlparse(image_url).path).suffix.lstrip(".").lower()
            if extension not in {"jpg", "jpeg", "png", "webp", "avif"}:
                extension = "jpg"
            image = {
                "format_id": str(candidate.get("type") or index),
                "url": image_url,
                "ext": extension,
                "width": candidate.get("width"),
                "height": candidate.get("height"),
                # yt-dlp's default selector rejects formats with neither an
                # audio nor video codec. Mark this as a synthetic image codec;
                # Linkdrop still downloads the URL directly as an image.
                "vcodec": "image",
                "acodec": "none",
            }
            formats.append(image)
            thumbnails.append({
                "url": image_url,
                "width": candidate.get("width"),
                "height": candidate.get("height"),
            })

        if not formats:
            return result
        return {
            **(result or {}),
            "id": str(product_media.get("code") or product_media.get("pk") or "image"),
            "ext": formats[0]["ext"],
            "_linkdrop_media_type": "image",
            "formats": formats,
            "thumbnails": thumbnails,
        }

    extract_product_media._linkdrop_image_support = True
    InstagramIE._extract_product_media = extract_product_media


_register_instagram_image_support()


def _twitter_photo_result(extractor, status: dict | None, tweet_id: str) -> dict | None:
    """Build an image result for photo-only X/Twitter posts."""

    if not isinstance(status, dict):
        return None

    description = status.get("full_text") or status.get("text") or ""
    user = status.get("user") or {}
    uploader = user.get("name")
    username = user.get("screen_name")
    title = f"{uploader} - {description}" if uploader else description
    title = title.strip() or f"Post {tweet_id}"
    base_info = {
        "title": title[:180],
        "description": description,
        "uploader": uploader,
        "channel": username,
        "uploader_id": username,
        "http_headers": {"Referer": "https://x.com/"},
    }

    media_items = []
    for tweet in (status, status.get("quoted_status") or {}):
        media_items.extend(((tweet.get("extended_entities") or {}).get("media") or []))

    entries = []
    for index, media in enumerate(media_items, start=1):
        if media.get("type") != "photo":
            continue
        image_url = media.get("media_url_https") or media.get("media_url")
        if not image_url:
            continue

        parsed = urlparse(image_url)
        query = dict(parse_qsl(parsed.query, keep_blank_values=True))
        extension = Path(parsed.path).suffix.lstrip(".").lower() or query.get("format", "jpg").lower()
        if extension not in {"jpg", "jpeg", "png", "webp", "avif"}:
            extension = "jpg"
        query["name"] = "orig"
        if not Path(parsed.path).suffix:
            query["format"] = extension
        original_url = urlunparse(parsed._replace(query=urlencode(query)))

        dimensions = media.get("original_info") or (media.get("sizes") or {}).get("large") or {}
        image_format = {
            "format_id": "original",
            "url": original_url,
            "ext": extension,
            "width": dimensions.get("width") or dimensions.get("w"),
            "height": dimensions.get("height") or dimensions.get("h"),
            "vcodec": "image",
            "acodec": "none",
        }
        entries.append({
            **base_info,
            "id": str(media.get("id_str") or media.get("id") or f"{tweet_id}-{index}"),
            "title": f"{title[:160]} #{index}" if len(media_items) > 1 else title[:180],
            "ext": extension,
            "_linkdrop_media_type": "image",
            "formats": [image_format],
            "thumbnails": [{
                "url": original_url,
                "width": image_format["width"],
                "height": image_format["height"],
            }],
        })

    if not entries:
        return None
    if len(entries) == 1:
        return entries[0]
    return extractor.playlist_result(entries, tweet_id, title[:180], description)


def _register_twitter_image_support() -> None:
    """Fallback to original photos when yt-dlp finds no video in a tweet."""

    current_real_extract = TwitterIE._real_extract
    if getattr(current_real_extract, "_linkdrop_image_support", False):
        return
    current_extract_status = TwitterIE._extract_status

    def extract_status(extractor, tweet_id):
        status = current_extract_status(extractor, tweet_id)
        extractor._linkdrop_last_status = status
        return status

    def real_extract(extractor, url):
        try:
            return current_real_extract(extractor, url)
        except ExtractorError as exc:
            if "no video could be found in this tweet" not in str(exc).lower():
                raise
            tweet_id = extractor._match_valid_url(url).group("id")
            result = _twitter_photo_result(
                extractor,
                getattr(extractor, "_linkdrop_last_status", {}),
                tweet_id,
            )
            if result:
                return result
            raise

    real_extract._linkdrop_image_support = True
    TwitterIE._extract_status = extract_status
    TwitterIE._real_extract = real_extract


_register_twitter_image_support()


class UserFacingError(Exception):
    """An expected error that is safe to show to the user."""


def _json_body():
    return request.get_json(silent=True) or {}


def _friendly_error(exc: Exception) -> str:
    message = str(exc).replace("ERROR: ", "").strip()
    lowered = message.lower()

    if isinstance(exc, UserFacingError):
        return message
    if "unsupported url" in lowered:
        return "Link ini belum didukung. Pastikan link publik dan berasal dari platform yang didukung yt-dlp."
    if "rate-limit reached or login required" in lowered:
        return (
            "Instagram membatasi request server atau meminta sesi login. "
            "Coba lagi nanti, atau konfigurasi cookies Instagram pada server."
        )
    if "could not find" in lowered and "cookies database" in lowered:
        return (
            "Database cookies browser tidak ditemukan. "
            "Pastikan browser memiliki profil dan sudah login ke platform tersebut."
        )
    if "private video" in lowered or "login required" in lowered:
        return "Konten ini privat atau memerlukan login. Hanya konten publik yang dapat diproses."
    if "sign in to confirm" in lowered or "not a bot" in lowered:
        return "YouTube meminta verifikasi anti-bot. Coba lagi nanti atau konfigurasi cookie/PO Token pada server."
    if "page needs to be reloaded" in lowered:
        return "YouTube meminta sesi baru. Tunggu sebentar lalu analisis ulang link tersebut."
    if "403" in lowered or "po token" in lowered or "sabr" in lowered:
        return "Platform menolak akses media (403/anti-bot). Coba lagi, pilih kualitas lain, atau konfigurasi autentikasi server."
    if "ffmpeg" in lowered and ("not found" in lowered or "not installed" in lowered):
        return "FFmpeg tidak tersedia di server. Gunakan Dockerfile.vercel yang disertakan saat deployment."
    if "larger than max-filesize" in lowered or "max-filesize" in lowered:
        return f"Ukuran media melebihi batas server ({MAX_MEDIA_BYTES // (1024 * 1024)} MB)."
    if "timed out" in lowered or "timeout" in lowered:
        return "Platform sumber terlalu lama merespons. Silakan coba lagi."
    return message or "Terjadi kesalahan saat memproses media."


def _validate_public_url(raw_url: str) -> str:
    url = (raw_url or "").strip()
    if len(url) > 2048:
        raise UserFacingError("Link terlalu panjang.")

    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise UserFacingError("Masukkan link HTTP/HTTPS yang valid.")
    if parsed.username or parsed.password:
        raise UserFacingError("Link dengan username atau password tidak diizinkan.")
    try:
        port = parsed.port
    except ValueError as exc:
        raise UserFacingError("Port pada link tidak valid.") from exc
    if port and port not in {80, 443}:
        raise UserFacingError("Port pada link tidak diizinkan.")

    hostname = parsed.hostname.lower().rstrip(".")
    if hostname == "localhost" or hostname.endswith(".local"):
        raise UserFacingError("Alamat lokal tidak diizinkan.")

    try:
        addresses = {item[4][0] for item in socket.getaddrinfo(hostname, None)}
    except socket.gaierror as exc:
        raise UserFacingError("Domain pada link tidak dapat ditemukan.") from exc

    for address in addresses:
        ip = ipaddress.ip_address(address)
        if not ip.is_global:
            raise UserFacingError("Alamat jaringan privat atau lokal tidak diizinkan.")

    return url


@contextmanager
def _authentication_options():
    """Yield optional yt-dlp authentication settings.

    Browser-cookie extraction is useful locally. For a server deployment, a
    base64-encoded Netscape cookies.txt can be supplied through an environment
    variable and is materialized only for the duration of the request.
    """

    cookie_file = os.environ.get("YTDLP_COOKIES_FILE", "").strip()
    browser = os.environ.get("YTDLP_COOKIES_FROM_BROWSER", "").strip()
    encoded_cookies = os.environ.get("YTDLP_COOKIES_B64", "").strip()
    temporary_path = None

    try:
        if encoded_cookies:
            temporary = tempfile.NamedTemporaryFile(mode="wb", suffix=".txt", dir=WORK_DIR, delete=False)
            temporary.write(base64.b64decode(encoded_cookies, validate=True))
            temporary.close()
            temporary_path = temporary.name
            yield {"cookiefile": temporary_path}
        elif cookie_file:
            yield {"cookiefile": cookie_file}
        elif browser and not IS_VERCEL:
            browser_name, _, profile = browser.partition(":")
            yield {"cookiesfrombrowser": (browser_name, profile or None, None, None)}
        else:
            yield {}
    finally:
        if temporary_path:
            try:
                os.unlink(temporary_path)
            except OSError:
                pass


def _base_ydl_options() -> dict:
    options = {
        "quiet": True,
        "noprogress": True,
        "no_warnings": True,
        "socket_timeout": HTTP_TIMEOUT,
        "retries": 3,
        "fragment_retries": 3,
        "extractor_retries": 2,
        "playlistend": 20,
        "noplaylist": True,
    }
    configured_runtime = os.environ.get("YTDLP_JS_RUNTIME", "").strip()
    if configured_runtime:
        runtime_name, _, configured_path = configured_runtime.partition(":")
        runtime_path = configured_path or shutil.which(runtime_name)
        if runtime_name and runtime_path:
            options["js_runtimes"] = {runtime_name: {"path": runtime_path}}
    else:
        for runtime_name, executable in (("deno", "deno"), ("node", "node"), ("quickjs", "qjs")):
            if runtime_path := shutil.which(executable):
                options["js_runtimes"] = {runtime_name: {"path": runtime_path}}
                break
    ffmpeg_location = os.environ.get("FFMPEG_LOCATION", "").strip()
    if ffmpeg_location:
        options["ffmpeg_location"] = ffmpeg_location
    return options


def _extract_info(url: str) -> dict:
    with _authentication_options() as auth:
        options = {**_base_ydl_options(), **auth, "skip_download": True}
        with yt_dlp.YoutubeDL(options) as ydl:
            info = ydl.extract_info(url, download=False)
    if not isinstance(info, dict):
        raise UserFacingError("Media publik pada link ini tidak dapat dibaca oleh platform sumber.")
    return info


def _entries(info: dict) -> list[dict]:
    entries = [item for item in (info.get("entries") or []) if item]
    return entries or [info]


def _is_image(entry: dict) -> bool:
    image_extensions = {"jpg", "jpeg", "png", "webp", "avif"}
    if entry.get("_linkdrop_media_type") == "image":
        return True
    if str(entry.get("ext", "")).lower() in image_extensions:
        return True

    formats = [fmt for fmt in entry.get("formats") or [] if isinstance(fmt, dict)]
    has_video = any(fmt.get("vcodec") not in (None, "none") for fmt in formats)
    has_image = any(str(fmt.get("ext", "")).lower() in image_extensions for fmt in formats)
    return has_image and not has_video


def _format_size(fmt: dict | None, duration: float | None = None) -> int | None:
    if not isinstance(fmt, dict):
        return None
    value = fmt.get("filesize") or fmt.get("filesize_approx")
    if value:
        return int(value)
    bitrate = fmt.get("tbr") or fmt.get("abr")
    return int(float(bitrate) * 1000 * duration / 8) if bitrate and duration else None


def _pretty_bytes(value: int | None) -> str | None:
    if not value:
        return None
    units = ["B", "KB", "MB", "GB"]
    size = float(value)
    for unit in units:
        if size < 1024 or unit == units[-1]:
            return f"{size:.1f} {unit}" if unit != "B" else f"{int(size)} B"
        size /= 1024
    return None


def _estimated_video_size(formats: list[dict], height: int, duration: float | None = None) -> int | None:
    formats = [fmt for fmt in formats if isinstance(fmt, dict)]
    video = [
        fmt for fmt in formats
        if fmt.get("vcodec") not in (None, "none") and (fmt.get("height") or 0) <= height
    ]
    audio = [fmt for fmt in formats if fmt.get("acodec") not in (None, "none")]
    exact_video = [fmt for fmt in video if fmt.get("height") == height]
    best_video = max(
        exact_video or video,
        key=lambda fmt: (
            str(fmt.get("vcodec") or "").lower().startswith(("h264", "avc")),
            fmt.get("ext") == "mp4",
            fmt.get("height") or 0,
            fmt.get("tbr") or 0,
        ),
        default=None,
    )
    best_audio = max(
        audio,
        key=lambda fmt: (fmt.get("ext") == "m4a", fmt.get("abr") or fmt.get("tbr") or 0),
        default=None,
    )
    video_size = _format_size(best_video, duration) if best_video else None
    if not video_size:
        return None
    if best_video.get("acodec") not in (None, "none"):
        return video_size
    return video_size + (_format_size(best_audio, duration) or 0) if best_audio else video_size


def _best_audio_format(formats: list[dict]) -> dict | None:
    return max(
        (
            fmt for fmt in formats
            if isinstance(fmt, dict) and fmt.get("vcodec") == "none" and fmt.get("acodec") not in (None, "none")
        ),
        key=lambda fmt: fmt.get("abr") or fmt.get("tbr") or 0,
        default=None,
    )


def _video_format_selector(height: int) -> str:
    """Prefer the requested resolution, then compatibility within that tier."""

    return (
        f"b[height={height}][ext=mp4][vcodec^=h264]/"
        f"b[height={height}][ext=mp4][vcodec^=avc]/"
        f"bv*[height={height}][ext=mp4][vcodec^=h264]+ba[ext=m4a]/"
        f"bv*[height={height}][ext=mp4][vcodec^=avc]+ba[ext=m4a]/"
        f"bv*[height={height}][ext=mp4]+ba[ext=m4a]/"
        f"bv*[height={height}]+ba/"
        f"b[height<={height}][ext=mp4][vcodec^=h264]/"
        f"b[height<={height}][ext=mp4][vcodec^=avc]/"
        f"bv*[height<={height}][ext=mp4][vcodec^=h264]+ba[ext=m4a]/"
        f"bv*[height<={height}][ext=mp4][vcodec^=avc]+ba[ext=m4a]/"
        f"bv*[height<={height}][ext=mp4]+ba[ext=m4a]/"
        f"b[height<={height}][ext=mp4]/"
        f"bv*[height<={height}]+ba/b[height<={height}]"
    )


def _make_token(url: str, kind: str, **options) -> str:
    return _signer.dumps({"url": url, "kind": kind, **options})


def _choice(url: str, kind: str, label: str, detail: str, **options) -> dict:
    token = _make_token(url, kind, **options)
    return {
        "id": f"{kind}:{options.get('height') or options.get('codec') or options.get('bitrate') or 'original'}",
        "label": label,
        "detail": detail,
        "estimated_bytes": options.get("estimated_bytes"),
        "token": token,
        "download_url": f"/api/download/{token}",
    }


def _build_choices(url: str, info: dict) -> dict:
    entries = _entries(info)
    image_count = sum(1 for entry in entries if _is_image(entry))
    is_gallery = len(entries) > 1 or image_count > 0

    if is_gallery:
        count = len(entries)
        detail = "File asli" if count == 1 else f"{count} item · ZIP"
        return {
            "video": [],
            "audio": [],
            "photo": [_choice(url, "gallery", "Unduh media asli", detail)],
        }

    formats = [fmt for fmt in info.get("formats") or [] if isinstance(fmt, dict)]
    heights = sorted({
        int(fmt["height"])
        for fmt in formats
        if fmt.get("vcodec") not in (None, "none") and fmt.get("height")
    })

    video_choices = []
    for height in heights:
        estimated_bytes = _estimated_video_size(formats, height, info.get("duration"))
        size = _pretty_bytes(estimated_bytes)
        detail = "MP4" + (f" · sekitar {size}" if size else " · ukuran dihitung saat mengunduh")
        video_choices.append(_choice(
            url,
            "video",
            f"{height}p",
            detail,
            height=height,
            estimated_bytes=estimated_bytes,
        ))

    source_audio = _best_audio_format(formats)
    source_bitrate = round(source_audio.get("abr") or source_audio.get("tbr") or 0) if source_audio else 0
    source_size_bytes = _format_size(source_audio, info.get("duration")) if source_audio else None
    source_size = _pretty_bytes(source_size_bytes)
    source_codec = str(source_audio.get("acodec") or "").lower() if source_audio else ""
    source_format = "AAC / M4A" if source_codec.startswith(("aac", "mp4a")) else "Opus / WebM"
    source_label = "Audio sumber" + (f" · {source_bitrate} kbps" if source_bitrate else "")
    source_detail = f"{source_format} · tanpa konversi"
    if source_size:
        source_detail += f" · sekitar {source_size}"

    def mp3_detail(bitrate: int) -> str:
        size = _pretty_bytes(int(bitrate * 1000 * info["duration"] / 8)) if info.get("duration") else None
        return f"MP3 · {bitrate} kbps" + (f" · sekitar {size}" if size else "")

    audio_choices = [
        _choice(
            url,
            "audio",
            source_label,
            source_detail,
            codec="source",
            estimated_bytes=source_size_bytes,
        ),
        _choice(
            url,
            "audio",
            "MP3 128 kbps",
            mp3_detail(128),
            codec="mp3",
            bitrate=128,
            estimated_bytes=int(128 * 1000 * info["duration"] / 8) if info.get("duration") else None,
        ),
        _choice(
            url,
            "audio",
            "MP3 192 kbps",
            mp3_detail(192),
            codec="mp3",
            bitrate=192,
            estimated_bytes=int(192 * 1000 * info["duration"] / 8) if info.get("duration") else None,
        ),
        _choice(
            url,
            "audio",
            "MP3 320 kbps",
            mp3_detail(320),
            codec="mp3",
            bitrate=320,
            estimated_bytes=int(320 * 1000 * info["duration"] / 8) if info.get("duration") else None,
        ),
    ] if source_audio else []

    return {"video": video_choices, "audio": audio_choices, "photo": []}


def _media_payload(url: str, info: dict) -> dict:
    entries = _entries(info)
    thumbnail = info.get("thumbnail")
    if not thumbnail and entries:
        thumbnail = entries[0].get("thumbnail")

    return {
        "title": info.get("title") or info.get("description") or "Media tanpa judul",
        "uploader": info.get("uploader") or info.get("channel") or info.get("creator"),
        "duration": info.get("duration"),
        "thumbnail": thumbnail,
        "platform": info.get("extractor_key") or info.get("extractor") or "Unknown",
        "item_count": len(entries),
        "choices": _build_choices(url, info),
        "limits": {
            "max_file_mb": MAX_MEDIA_BYTES // (1024 * 1024),
            "token_minutes": TOKEN_MAX_AGE // 60,
        },
    }


def _find_downloaded_file(folder: Path) -> Path:
    ignored_suffixes = {".part", ".ytdl", ".temp"}
    candidates = [path for path in folder.iterdir() if path.is_file() and path.suffix.lower() not in ignored_suffixes]
    if not candidates:
        raise UserFacingError("File hasil unduhan tidak ditemukan.")
    return max(candidates, key=lambda path: path.stat().st_mtime)


def _mp4_streams_are_compatible(streams: list[dict]) -> bool:
    """Return whether MP4 streams play in common browsers and phone players."""

    video_streams = [stream for stream in streams if stream.get("codec_type") == "video"]
    audio_streams = [stream for stream in streams if stream.get("codec_type") == "audio"]
    if not video_streams:
        return False

    video = video_streams[0]
    video_compatible = (
        video.get("codec_name") == "h264"
        and video.get("pix_fmt") in {None, "yuv420p", "yuvj420p"}
    )
    audio_compatible = not audio_streams or audio_streams[0].get("codec_name") == "aac"
    return video_compatible and audio_compatible


def _ensure_compatible_mp4(path: Path, progress=None) -> Path:
    """Transcode incompatible MP4 codecs while retaining the selected resolution."""

    if path.suffix.lower() != ".mp4":
        return path

    ffprobe = shutil.which("ffprobe")
    ffmpeg = shutil.which("ffmpeg")
    if not ffprobe or not ffmpeg:
        raise UserFacingError("FFmpeg/ffprobe tidak tersedia untuk membuat MP4 yang kompatibel.")

    try:
        probe = subprocess.run(
            [
                ffprobe,
                "-v", "error",
                "-show_entries", "stream=codec_type,codec_name,pix_fmt",
                "-of", "json",
                str(path),
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        streams = json.loads(probe.stdout).get("streams") or []
    except (subprocess.CalledProcessError, json.JSONDecodeError) as exc:
        raise UserFacingError("File video hasil unduhan tidak dapat divalidasi.") from exc

    if _mp4_streams_are_compatible(streams):
        return path

    if progress:
        progress({
            "percent": 92,
            "phase": "Menyesuaikan kompatibilitas video",
            "detail": "Mengonversi video ke H.264 dan audio ke AAC…",
        })

    converted = path.with_name(f"{path.stem}.compatible.mp4")
    try:
        subprocess.run(
            [
                ffmpeg,
                "-y",
                "-i", str(path),
                "-map", "0:v:0",
                "-map", "0:a?",
                "-c:v", "libx264",
                "-preset", "fast",
                "-crf", "18",
                "-pix_fmt", "yuv420p",
                "-c:a", "aac",
                "-b:a", "192k",
                "-movflags", "+faststart",
                str(converted),
            ],
            check=True,
            capture_output=True,
        )
    except subprocess.CalledProcessError as exc:
        converted.unlink(missing_ok=True)
        raise UserFacingError("Video gagal dikonversi ke format MP4 yang kompatibel.") from exc

    path.unlink()
    converted.replace(path)
    return path


def _download_video_or_audio(
    payload: dict,
    folder: Path,
    progress=None,
    cancelled: threading.Event | None = None,
) -> Path:
    kind = payload["kind"]
    url = payload["url"]
    output_template = str(folder / "%(title).120s [%(id)s].%(ext)s")

    options = {
        **_base_ydl_options(),
        "outtmpl": output_template,
        "max_filesize": MAX_MEDIA_BYTES,
        "overwrites": True,
        "continuedl": True,
        "noplaylist": True,
    }

    if progress:
        stream_slots = 2 if kind == "video" else 1
        stream_indexes = {}
        last_update = {"percent": -1.0, "time": 0.0}

        def emit(percent, phase, detail="", force=False):
            if cancelled and cancelled.is_set():
                raise UserFacingError("Unduhan dibatalkan.")
            now = time.monotonic()
            percent = max(last_update["percent"], min(float(percent), 92.0))
            if not force and percent - last_update["percent"] < 0.4 and now - last_update["time"] < 1:
                return
            last_update.update(percent=percent, time=now)
            progress({
                "percent": round(percent, 1),
                "phase": phase,
                "detail": detail,
            })

        def download_hook(status):
            state = status.get("status")
            if state == "downloading":
                stream_key = status.get("filename") or str(
                    (status.get("info_dict") or {}).get("format_id") or "media"
                )
                if stream_key not in stream_indexes:
                    stream_indexes[stream_key] = len(stream_indexes)
                stream_index = min(stream_indexes[stream_key], stream_slots - 1)
                downloaded = status.get("downloaded_bytes") or 0
                total = status.get("total_bytes") or status.get("total_bytes_estimate") or 0
                fraction = min(downloaded / total, 1) if total else 0
                percent = ((stream_index + fraction) / stream_slots) * 84
                details = []
                if speed := status.get("speed"):
                    details.append(f"{speed / (1024 * 1024):.1f} MB/s")
                if status.get("eta") is not None:
                    details.append(f"sisa {int(status['eta'])} dtk")
                emit(percent, "Mengunduh media", " · ".join(details))
            elif state == "finished":
                stream_key = status.get("filename") or str(
                    (status.get("info_dict") or {}).get("format_id") or "media"
                )
                stream_index = min(stream_indexes.get(stream_key, 0), stream_slots - 1)
                percent = ((stream_index + 1) / stream_slots) * 84
                emit(percent, "Bagian media selesai", "Menyiapkan bagian berikutnya…", force=True)

        def postprocessor_hook(status):
            state = status.get("status")
            if state == "started":
                emit(87, "Memproses media", "Menggabungkan video dan audio…", force=True)
            elif state == "processing":
                emit(90, "Memproses media", "FFmpeg sedang bekerja…")
            elif state == "finished":
                emit(92, "Pemrosesan selesai", "Menyiapkan file untuk browser…", force=True)

        options["progress_hooks"] = [download_hook]
        options["postprocessor_hooks"] = [postprocessor_hook]

    if kind == "video":
        height = int(payload.get("height", 0))
        if height < 1 or height > 4320:
            raise UserFacingError("Pilihan kualitas video tidak valid.")
        options.update({
            "format": _video_format_selector(height),
            "merge_output_format": "mp4",
            "postprocessor_args": {"Merger": ["-movflags", "+faststart"]},
        })
    elif kind == "audio":
        codec = payload.get("codec")
        options["format"] = "bestaudio/best"
        if codec == "mp3":
            bitrate = int(payload.get("bitrate", 0))
            if bitrate not in {128, 192, 320}:
                raise UserFacingError("Pilihan bitrate audio tidak valid.")
            options["postprocessors"] = [{
                "key": "FFmpegExtractAudio",
                "preferredcodec": "mp3",
                "preferredquality": str(bitrate),
            }]
        elif codec != "source":
            raise UserFacingError("Pilihan format audio tidak valid.")
    else:
        raise UserFacingError("Jenis unduhan tidak valid.")

    with _authentication_options() as auth:
        with yt_dlp.YoutubeDL({**options, **auth}) as ydl:
            ydl.download([url])

    result = _find_downloaded_file(folder)
    if kind == "video":
        result = _ensure_compatible_mp4(result, progress)
    if result.stat().st_size > MAX_MEDIA_BYTES:
        raise UserFacingError(f"Ukuran hasil melebihi batas {MAX_MEDIA_BYTES // (1024 * 1024)} MB.")
    if progress:
        progress({
            "percent": 92,
            "phase": "File siap",
            "detail": "Mengirim file ke browser…",
        })
    return result


def _best_image_source(entry: dict) -> tuple[str, dict, str] | None:
    image_extensions = {"jpg", "jpeg", "png", "webp", "avif"}
    candidates = []
    for fmt in entry.get("formats") or []:
        if not isinstance(fmt, dict):
            continue
        if str(fmt.get("ext", "")).lower() in image_extensions and fmt.get("url"):
            candidates.append(fmt)

    if candidates:
        selected = max(candidates, key=lambda fmt: (fmt.get("width") or 0) * (fmt.get("height") or 0))
        return selected["url"], selected.get("http_headers") or {}, selected.get("ext") or "jpg"

    if str(entry.get("ext", "")).lower() in image_extensions and entry.get("url"):
        return entry["url"], entry.get("http_headers") or {}, entry.get("ext") or "jpg"

    thumbnails = [item for item in entry.get("thumbnails") or [] if isinstance(item, dict) and item.get("url")]
    if thumbnails:
        selected = max(thumbnails, key=lambda item: (item.get("width") or 0) * (item.get("height") or 0))
        extension = Path(urlparse(selected["url"]).path).suffix.lstrip(".").lower()
        return selected["url"], entry.get("http_headers") or {}, extension if extension in image_extensions else "jpg"
    return None


def _best_progressive_video_source(entry: dict) -> tuple[str, dict, str] | None:
    formats = [
        fmt for fmt in entry.get("formats") or []
        if isinstance(fmt, dict) and fmt.get("url") and fmt.get("vcodec") not in (None, "none") and fmt.get("acodec") not in (None, "none")
    ]
    if not formats:
        # Instagram's progressive video_versions are playable MP4 files, but
        # older extractor metadata may omit acodec entirely.
        formats = [
            fmt for fmt in entry.get("formats") or []
            if isinstance(fmt, dict) and fmt.get("url")
            and fmt.get("vcodec") not in (None, "none")
            and fmt.get("protocol") not in {"m3u8", "m3u8_native", "http_dash_segments"}
        ]
    if not formats:
        return None
    selected = max(formats, key=lambda fmt: ((fmt.get("height") or 0), (fmt.get("tbr") or 0)))
    return selected["url"], selected.get("http_headers") or {}, selected.get("ext") or "mp4"


def _download_remote(
    source: tuple[str, dict, str],
    destination: Path,
    budget: int,
    progress=None,
    cancelled: threading.Event | None = None,
) -> int:
    source_url, headers, _ = source
    _validate_public_url(source_url)
    downloaded = 0
    current_url = source_url
    response = None
    for _ in range(6):
        _validate_public_url(current_url)
        response = requests.get(
            current_url,
            headers=headers,
            stream=True,
            timeout=HTTP_TIMEOUT,
            allow_redirects=False,
        )
        if response.is_redirect or response.is_permanent_redirect:
            location = response.headers.get("location")
            response.close()
            if not location:
                raise UserFacingError("Redirect sumber media tidak valid.")
            current_url = urljoin(current_url, location)
            continue
        break
    else:
        raise UserFacingError("Terlalu banyak redirect dari sumber media.")

    with response:
        response.raise_for_status()
        content_length = int(response.headers.get("content-length") or 0)
        if content_length and content_length > budget:
            raise UserFacingError("Ukuran media melebihi batas server.")
        with destination.open("wb") as output:
            for chunk in response.iter_content(CHUNK_SIZE):
                if cancelled and cancelled.is_set():
                    raise UserFacingError("Unduhan dibatalkan.")
                if not chunk:
                    continue
                downloaded += len(chunk)
                if downloaded > budget:
                    raise UserFacingError("Ukuran media melebihi batas server.")
                output.write(chunk)
                if progress:
                    progress(min(downloaded / content_length, 1) if content_length else 0)
    return downloaded


def _download_gallery_items(
    payload: dict,
    folder: Path,
    budget: int = MAX_MEDIA_BYTES,
    progress=None,
    cancelled: threading.Event | None = None,
) -> tuple[str, list[Path]]:
    if progress:
        progress({"percent": 2, "phase": "Membaca media", "detail": "Memuat daftar foto dan video…"})
    info = _extract_info(payload["url"])
    entries = _entries(info)[:MAX_GALLERY_ITEMS]
    if not entries:
        raise UserFacingError("Tidak ada media yang dapat diunduh.")
    item_paths = []
    used_bytes = 0

    for index, entry in enumerate(entries, start=1):
        if cancelled and cancelled.is_set():
            raise UserFacingError("Unduhan dibatalkan.")
        source = _best_image_source(entry) if _is_image(entry) else _best_progressive_video_source(entry)
        if not source:
            raise UserFacingError(f"Format item ke-{index} tidak dapat diunduh.")
        extension = secure_filename(source[2].lower()) or ("jpg" if _is_image(entry) else "mp4")
        destination = folder / f"{index:02d}.{extension}"
        start_percent = 5 + ((index - 1) / len(entries)) * 82
        end_percent = 5 + (index / len(entries)) * 82

        def item_progress(fraction, item=index):
            if progress:
                progress({
                    "percent": round(start_percent + (end_percent - start_percent) * fraction, 1),
                    "phase": f"Mengunduh item {item} dari {len(entries)}",
                    "detail": "Mengambil media asli…",
                })

        used_bytes += _download_remote(
            source,
            destination,
            budget - used_bytes,
            progress=item_progress,
            cancelled=cancelled,
        )
        item_paths.append(destination)

    return info.get("title") or info.get("id") or "media", item_paths


def _download_gallery(
    payload: dict,
    folder: Path,
    progress=None,
    cancelled: threading.Event | None = None,
) -> Path:
    _, item_paths = _download_gallery_items(payload, folder, MAX_MEDIA_BYTES, progress, cancelled)

    if len(item_paths) == 1:
        return item_paths[0]

    archive = folder / "media-asli.zip"
    if progress:
        progress({"percent": 90, "phase": "Membuat arsip", "detail": "Mengemas seluruh media ke ZIP…"})
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_STORED) as bundle:
        for path in item_paths:
            bundle.write(path, arcname=path.name)
    if archive.stat().st_size > MAX_MEDIA_BYTES:
        raise UserFacingError("Ukuran arsip melebihi batas server.")
    return archive


def _download_payload(
    payload: dict,
    folder: Path,
    progress=None,
    cancelled: threading.Event | None = None,
) -> Path:
    if payload["kind"] == "gallery":
        return _download_gallery(payload, folder, progress, cancelled)
    return _download_video_or_audio(payload, folder, progress, cancelled)


def _should_retry_media_error(payload: dict, exc: Exception) -> bool:
    hostname = (urlparse(payload.get("url", "")).hostname or "").lower()
    message = str(exc).lower()
    is_youtube = hostname == "youtu.be" or hostname == "youtube.com" or hostname.endswith(".youtube.com")
    return is_youtube and (
        "403" in message or "forbidden" in message
    )


def _download_with_retry(
    payload: dict,
    folder: Path,
    progress=None,
    cancelled: threading.Event | None = None,
) -> Path:
    for attempt in range(2):
        if cancelled and cancelled.is_set():
            raise UserFacingError("Unduhan dibatalkan.")
        attempt_folder = folder / f"attempt-{attempt + 1}"
        attempt_folder.mkdir(exist_ok=True)
        try:
            return _download_payload(payload, attempt_folder, progress, cancelled)
        except Exception as exc:
            if attempt or not _should_retry_media_error(payload, exc):
                raise
            if progress:
                progress({
                    "percent": 1,
                    "phase": "Mencoba ulang unduhan",
                    "detail": "YouTube menolak URL media sementara. Menyiapkan sesi baru…",
                })

    raise UserFacingError("Unduhan tidak dapat diproses.")


def _batch_storage_limit() -> int:
    free_bytes = shutil.disk_usage(WORK_DIR).free
    return min(MAX_MEDIA_BYTES, int(free_bytes / BATCH_DISK_FACTOR))


def _unique_archive_name(filename: str, used_names: set[str]) -> str:
    safe_name = secure_filename(filename) or "media"
    candidate = safe_name
    stem = Path(safe_name).stem
    suffix = Path(safe_name).suffix
    counter = 2
    while candidate.lower() in used_names:
        candidate = f"{stem}-{counter}{suffix}"
        counter += 1
    used_names.add(candidate.lower())
    return candidate


def _unique_archive_folder(name: str, used_names: set[str]) -> str:
    safe_name = secure_filename(name) or "media"
    candidate = safe_name
    counter = 2
    while f"{candidate.lower()}/" in used_names:
        candidate = f"{safe_name}-{counter}"
        counter += 1
    used_names.add(f"{candidate.lower()}/")
    return candidate


def _download_batch(
    payloads: list[dict],
    folder: Path,
    package_number: int,
    progress=None,
    cancelled: threading.Event | None = None,
) -> Path:
    if not payloads or len(payloads) > MAX_BATCH_ITEMS:
        raise UserFacingError(f"Satu paket harus berisi 1–{MAX_BATCH_ITEMS} media.")

    storage_limit = _batch_storage_limit()
    if storage_limit < 32 * 1024 * 1024:
        raise UserFacingError("Ruang penyimpanan sementara tidak mencukupi untuk membuat paket.")

    estimated_total = sum(int(item.get("estimated_bytes") or 0) for item in payloads)
    if estimated_total > storage_limit:
        raise UserFacingError("Perkiraan ukuran paket melewati batas penyimpanan. Bagi pilihan menjadi paket yang lebih kecil.")

    archive = folder / f"linkdrop-{time.strftime('%Y-%m-%d')}-part-{package_number}.zip"
    used_names = set()
    failures = []
    completed = 0
    archived_bytes = 0

    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_STORED, allowZip64=True) as bundle:
        for index, payload in enumerate(payloads, start=1):
            if cancelled and cancelled.is_set():
                raise UserFacingError("Unduhan dibatalkan.")
            item_folder = folder / f"item-{index:02d}"
            item_folder.mkdir()

            def item_progress(event, item=index):
                if not progress:
                    return
                item_percent = max(0, min(float(event.get("percent") or 0), 100))
                overall = ((item - 1) + item_percent / 100) / len(payloads) * 88
                progress({
                    "percent": round(overall, 1),
                    "item_index": item,
                    "item_count": len(payloads),
                    "item_percent": round(item_percent, 1),
                    "phase": f"{event.get('phase') or 'Memproses media'} · {item}/{len(payloads)}",
                    "detail": event.get("detail") or "",
                })

            try:
                if payload["kind"] == "gallery":
                    title, results = _download_gallery_items(
                        payload,
                        item_folder,
                        storage_limit - archived_bytes,
                        item_progress,
                        cancelled,
                    )
                    result_size = sum(result.stat().st_size for result in results)
                    if archived_bytes + result_size > storage_limit:
                        raise UserFacingError("Ukuran aktual media melewati sisa kapasitas paket.")
                    if len(results) == 1:
                        result = results[0]
                        bundle.write(result, arcname=_unique_archive_name(result.name, used_names))
                    else:
                        archive_folder = _unique_archive_folder(title, used_names)
                        for result in results:
                            bundle.write(result, arcname=f"{archive_folder}/{result.name}")
                    archived_bytes += result_size
                else:
                    result = _download_with_retry(payload, item_folder, item_progress, cancelled)
                    result_size = result.stat().st_size
                    if archived_bytes + result_size > storage_limit:
                        raise UserFacingError("Ukuran aktual media melewati sisa kapasitas paket.")
                    bundle.write(result, arcname=_unique_archive_name(result.name, used_names))
                    archived_bytes += result_size
                completed += 1
            except OSError:
                raise
            except Exception as exc:
                if cancelled and cancelled.is_set():
                    raise
                failures.append(f"Media {index}: {_friendly_error(exc)}")
            finally:
                shutil.rmtree(item_folder, ignore_errors=True)

        if failures:
            bundle.writestr(
                "linkdrop-report.txt",
                "Beberapa media tidak dapat diproses:\n\n" + "\n".join(failures),
            )

    if not completed:
        archive.unlink(missing_ok=True)
        raise UserFacingError("Tidak ada media yang berhasil diproses dalam paket ini.")
    if archive.stat().st_size > storage_limit:
        archive.unlink(missing_ok=True)
        raise UserFacingError("Ukuran aktual paket melewati batas penyimpanan.")
    if progress:
        progress({
            "percent": 92,
            "phase": "Paket ZIP siap",
            "detail": f"{completed} media berhasil" + (f" · {len(failures)} gagal" if failures else ""),
        })
    return archive


def _progress_frame(kind: bytes, payload=b"") -> bytes:
    if isinstance(payload, dict):
        payload = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    elif isinstance(payload, str):
        payload = payload.encode("utf-8")
    return struct.pack(">cI", kind, len(payload)) + payload


def _stream_progress_download(
    payload: dict | None = None,
    batch_payloads: list[dict] | None = None,
    package_number: int = 1,
) -> Response:
    """Keep one request open while streaming progress events and the final file."""

    cleanup_folder = Path(tempfile.mkdtemp(prefix="linkdrop-", dir=WORK_DIR))
    events = queue.Queue()
    cancelled = threading.Event()

    def report(event):
        if cancelled.is_set():
            raise UserFacingError("Unduhan dibatalkan.")
        events.put(("progress", event))

    def worker():
        try:
            report({"percent": 0, "phase": "Memulai unduhan", "detail": "Menghubungi platform sumber…"})
            if batch_payloads is not None:
                result = _download_batch(batch_payloads, cleanup_folder, package_number, report, cancelled)
            else:
                result = _download_with_retry(payload, cleanup_folder, report, cancelled)
            events.put(("file", result))
        except Exception as exc:
            app.logger.warning("Media progress download failed: %s", exc)
            events.put(("error", _friendly_error(exc)))
            shutil.rmtree(cleanup_folder, ignore_errors=True)

    download_thread = threading.Thread(target=worker, name="linkdrop-download")
    download_thread.start()

    @stream_with_context
    def generate():
        completed = False
        try:
            while True:
                event_type, value = events.get()
                if event_type == "progress":
                    yield _progress_frame(b"P", value)
                    continue
                if event_type == "error":
                    yield _progress_frame(b"E", {"error": value})
                    break

                path = value
                filename = secure_filename(path.name) or "download"
                file_size = path.stat().st_size
                content_type = mimetypes.guess_type(filename)[0] or "application/octet-stream"
                yield _progress_frame(b"M", {
                    "filename": filename,
                    "content_type": content_type,
                    "size": file_size,
                })
                with path.open("rb") as media:
                    while chunk := media.read(CHUNK_SIZE):
                        if cancelled.is_set():
                            return
                        yield _progress_frame(b"D", chunk)
                yield _progress_frame(b"C", {"ok": True})
                completed = True
                break
        finally:
            cancelled.set()
            if completed or not download_thread.is_alive():
                shutil.rmtree(cleanup_folder, ignore_errors=True)

    response = Response(generate(), mimetype=PROGRESS_MIME)
    response.headers["Cache-Control"] = "private, no-store, max-age=0"
    response.headers["X-Accel-Buffering"] = "no"
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response


def _stream_file(path: Path, cleanup_folder: Path) -> Response:
    filename = secure_filename(path.name) or "download"
    content_type = mimetypes.guess_type(filename)[0] or "application/octet-stream"
    file_size = path.stat().st_size

    @stream_with_context
    def generate():
        try:
            with path.open("rb") as media:
                while chunk := media.read(CHUNK_SIZE):
                    yield chunk
        finally:
            shutil.rmtree(cleanup_folder, ignore_errors=True)

    response = Response(generate(), mimetype=content_type, direct_passthrough=True)
    response.headers["Content-Disposition"] = f"attachment; filename*=UTF-8''{quote(filename)}"
    response.headers["Content-Length"] = str(file_size)
    response.headers["Cache-Control"] = "private, no-store, max-age=0"
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response


def _decode_download_token(token: str) -> dict:
    payload = _signer.loads(token, max_age=TOKEN_MAX_AGE)
    payload["url"] = _validate_public_url(payload.get("url", ""))
    if payload.get("kind") not in {"video", "audio", "gallery"}:
        raise UserFacingError("Jenis unduhan tidak valid.")
    return payload


@app.after_request
def _security_headers(response):
    response.headers.setdefault("Referrer-Policy", "no-referrer")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
    response.headers.setdefault(
        "Content-Security-Policy",
        "default-src 'self'; img-src 'self' https: data:; "
        "style-src 'self' 'unsafe-inline'; script-src 'self' 'unsafe-inline'; "
        "connect-src 'self'; object-src 'none'; base-uri 'none'; form-action 'self'",
    )
    return response


@app.get("/")
def index():
    return render_template(
        "index.html",
        max_file_mb=MAX_MEDIA_BYTES // (1024 * 1024),
        batch_limit_bytes=_batch_storage_limit(),
        batch_warning_items=BATCH_WARNING_ITEMS,
        max_batch_items=MAX_BATCH_ITEMS,
    )


@app.get("/api/health")
def health():
    js_runtimes = _base_ydl_options().get("js_runtimes") or {}
    return jsonify({
        "ok": True,
        "runtime": "vercel" if IS_VERCEL else "local",
        "ffmpeg": bool(shutil.which("ffmpeg")),
        "js_runtime": next(iter(js_runtimes), None),
        "max_file_mb": MAX_MEDIA_BYTES // (1024 * 1024),
        "batch_limit_mb": _batch_storage_limit() // (1024 * 1024),
        "max_batch_items": MAX_BATCH_ITEMS,
    })


@app.post("/api/info")
def media_info():
    try:
        url = _validate_public_url(_json_body().get("url", ""))
        info = _extract_info(url)
        return jsonify(_media_payload(url, info))
    except Exception as exc:
        app.logger.warning("Media extraction failed: %s", exc)
        return jsonify({"error": _friendly_error(exc)}), 422


@app.get("/api/download/<token>")
def download(token: str):
    try:
        payload = _decode_download_token(token)
    except SignatureExpired:
        return jsonify({"error": "Link unduhan kedaluwarsa. Analisis link kembali."}), 410
    except BadSignature:
        return jsonify({"error": "Link unduhan tidak valid."}), 400
    except Exception as exc:
        return jsonify({"error": _friendly_error(exc)}), 422

    if request.args.get("progress") == "1":
        return _stream_progress_download(payload)

    cleanup_folder = Path(tempfile.mkdtemp(prefix="linkdrop-", dir=WORK_DIR))
    try:
        if payload["kind"] == "gallery":
            result = _download_gallery(payload, cleanup_folder)
        else:
            result = _download_with_retry(payload, cleanup_folder)
        return _stream_file(result, cleanup_folder)
    except Exception as exc:
        shutil.rmtree(cleanup_folder, ignore_errors=True)
        app.logger.warning("Media download failed: %s", exc)
        return jsonify({"error": _friendly_error(exc)}), 422


@app.post("/api/batch/download")
def batch_download():
    try:
        body = _json_body()
        tokens = body.get("tokens")
        if not isinstance(tokens, list) or not tokens:
            raise UserFacingError("Pilih setidaknya satu media untuk diunduh.")
        if len(tokens) > MAX_BATCH_ITEMS:
            raise UserFacingError(f"Satu paket maksimal berisi {MAX_BATCH_ITEMS} media.")
        if any(not isinstance(token, str) or len(token) > 8192 for token in tokens):
            raise UserFacingError("Pilihan media tidak valid.")
        package_number = int(body.get("package") or 1)
        if package_number < 1 or package_number > 999:
            raise UserFacingError("Nomor paket tidak valid.")
        payloads = [_decode_download_token(token) for token in tokens]
    except SignatureExpired:
        return jsonify({"error": "Pilihan unduhan kedaluwarsa. Analisis link kembali."}), 410
    except BadSignature:
        return jsonify({"error": "Pilihan unduhan tidak valid."}), 400
    except Exception as exc:
        return jsonify({"error": _friendly_error(exc)}), 422

    return _stream_progress_download(batch_payloads=payloads, package_number=package_number)


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "5000"))
    app.run(host="0.0.0.0", port=port, debug=os.environ.get("FLASK_DEBUG") == "1")
