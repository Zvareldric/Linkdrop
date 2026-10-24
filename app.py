from __future__ import annotations

import base64
import functools
import hmac
import ipaddress
import json
import mimetypes
import os
import queue
import re
import secrets
import shutil
import socket
import struct
import subprocess
import tempfile
import threading
import time
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
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
PROGRESS_HEARTBEAT_SECONDS = 15
MAX_BATCH_ITEMS = 30
BATCH_WARNING_ITEMS = 10
BATCH_DISK_FACTOR = 2.5
MAX_GALLERY_ITEMS = 20
GALLERY_CONCURRENCY = max(1, int(os.environ.get("GALLERY_CONCURRENCY", 4)))
BATCH_CONCURRENCY = max(1, int(os.environ.get("BATCH_CONCURRENCY", 2)))
FFMPEG_TIMEOUT = int(os.environ.get("FFMPEG_TIMEOUT", 60 * 60))
SUBTITLE_LANGUAGES = ("id", "en")
SUBTITLE_SUFFIXES = {".json3", ".srt", ".vtt", ".ass", ".ssa", ".srv1", ".srv2", ".srv3", ".ttml"}
BURNABLE_SUBTITLE_SUFFIXES = {".srt", ".vtt", ".ass", ".ssa"}
SUBTITLE_STREAM_CODECS = {".mp4": "mov_text", ".m4v": "mov_text", ".mov": "mov_text", ".mkv": "srt", ".webm": "webvtt"}
SUBTITLE_LANGUAGE_TAGS = {"id": ("ind", "Indonesia"), "en": ("eng", "English")}
# White text on a translucent black box, close to YouTube's default captions.
SUBTITLE_STYLE = (
    "Fontname=DejaVu Sans,Fontsize=16,PrimaryColour=&H00FFFFFF,"
    "OutlineColour=&H80000000,BorderStyle=3,Outline=1,Shadow=0,MarginV=18"
)
ACCESS_USERNAME = os.environ.get("ACCESS_USERNAME", "linkdrop").strip() or "linkdrop"
ACCESS_PASSWORD = os.environ.get("ACCESS_PASSWORD", "").strip()
TRUSTED_PROXY_HEADER = os.environ.get("TRUSTED_PROXY_HEADER", "").strip()
FAILED_LOGIN_LIMIT = (10, 10 * 60)

WORK_DIR.mkdir(parents=True, exist_ok=True)

app = Flask(__name__)
app.config["JSON_SORT_KEYS"] = False

def _token_secret_for_runtime(configured_secret: str, is_vercel: bool) -> str:
    if is_vercel and not configured_secret:
        raise RuntimeError("DOWNLOAD_TOKEN_SECRET wajib diatur pada deployment Vercel.")
    return configured_secret or secrets.token_urlsafe(32)


_configured_token_secret = os.environ.get("DOWNLOAD_TOKEN_SECRET", "").strip()
_token_secret = _token_secret_for_runtime(_configured_token_secret, IS_VERCEL)
_signer = URLSafeTimedSerializer(_token_secret, salt="linkdrop-download-v1")


def _parse_rate_limit(value: str) -> tuple[int, int] | None:
    """Parse ``requests/seconds``; ``0`` or ``off`` disables the limit."""

    raw = value.strip().lower()
    if raw in {"", "0", "off"}:
        return None
    count, _, window = raw.partition("/")
    try:
        limit = (int(count), int(window or 60))
    except ValueError as exc:
        raise RuntimeError(f"Format rate limit tidak valid: {value!r}. Gunakan jumlah/detik, misalnya 30/60.") from exc
    if limit[0] < 1 or limit[1] < 1:
        raise RuntimeError(f"Format rate limit tidak valid: {value!r}. Gunakan jumlah/detik, misalnya 30/60.")
    return limit


RATE_LIMITS = {
    "info": _parse_rate_limit(os.environ.get("RATE_LIMIT_INFO", "30/60")),
    "download": _parse_rate_limit(os.environ.get("RATE_LIMIT_DOWNLOAD", "20/600")),
}
RATE_LIMITED_ENDPOINTS = {"media_info": "info", "download": "download", "batch_download": "download"}


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


class PrivateNetworkError(UserFacingError):
    """A media request tried to reach a private or local network address."""


class BlockedAddressError(socket.gaierror):
    """Resolver error used so HTTP clients treat a blocked host as unreachable."""


PRIVATE_NETWORK_MESSAGE = "Alamat jaringan privat atau lokal tidak diizinkan."
_egress_state = threading.local()
_system_getaddrinfo = socket.getaddrinfo


def _is_public_address(address: str) -> bool:
    try:
        return ipaddress.ip_address(address).is_global
    except ValueError:
        return False


def _proxy_hosts() -> set[str]:
    hosts = set()
    for name in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
        value = os.environ.get(name, "").strip()
        if value:
            host = urlparse(value if "://" in value else f"http://{value}").hostname
            if host:
                hosts.add(host.lower())
    return hosts


def _public_only_getaddrinfo(host, *args, **kwargs):
    """Resolve hosts, dropping private addresses while a media request runs.

    yt-dlp follows redirects and fetches manifests and fragments on its own,
    so validating only the submitted URL is not enough. Checking every lookup
    made by the request thread also covers DNS answers that change between
    validation and connection. A configured outbound proxy stays reachable.
    """

    results = _system_getaddrinfo(host, *args, **kwargs)
    if not getattr(_egress_state, "public_only", False):
        return results
    name = host.decode("ascii", "ignore") if isinstance(host, bytes) else str(host or "")
    if name.lower().rstrip(".") in _proxy_hosts():
        return results
    public = [item for item in results if _is_public_address(item[4][0])]
    if not public:
        raise BlockedAddressError(socket.EAI_NONAME, PRIVATE_NETWORK_MESSAGE)
    return public


socket.getaddrinfo = _public_only_getaddrinfo


@contextmanager
def _public_network_only():
    previous = getattr(_egress_state, "public_only", False)
    _egress_state.public_only = True
    try:
        yield
    finally:
        _egress_state.public_only = previous


def _json_body():
    return request.get_json(silent=True) or {}


def _friendly_error(exc: Exception) -> str:
    message = str(exc).replace("ERROR: ", "").strip()
    lowered = message.lower()

    if isinstance(exc, UserFacingError):
        return message
    if PRIVATE_NETWORK_MESSAGE.lower() in lowered:
        return PRIVATE_NETWORK_MESSAGE
    if "unable to download video subtitles" in lowered:
        return "Subtitle gagal diambil dari platform. Coba lagi nanti atau unduh tanpa subtitle."
    if "unsupported url" in lowered and "tiktok.com" in lowered and "/photo/" in lowered:
        return "Postingan foto TikTok belum didukung oleh extractor saat ini. Coba link TikTok video."
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
    except BlockedAddressError as exc:
        raise PrivateNetworkError(PRIVATE_NETWORK_MESSAGE) from exc
    except socket.gaierror as exc:
        raise UserFacingError("Domain pada link tidak dapat ditemukan.") from exc

    for address in addresses:
        ip = ipaddress.ip_address(address)
        if not ip.is_global:
            raise PrivateNetworkError(PRIVATE_NETWORK_MESSAGE)

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
        with _public_network_only(), yt_dlp.YoutubeDL(options) as ydl:
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


def _original_video_format_selector(height: int) -> str:
    """Pick the best source streams at a resolution without caring about codec."""

    return f"bv*[height={height}]+ba/b[height={height}]/bv*[height<={height}]+ba/b[height<={height}]"


def _is_h264(fmt: dict) -> bool:
    return str(fmt.get("vcodec") or "").lower().startswith(("h264", "avc"))


AUDIO_FORMAT_NAMES = {"m4a": "M4A (AAC)", "webm": "Opus (WebM)", "opus": "Opus", "mp3": "MP3", "ogg": "Ogg Vorbis"}


def _make_token(url: str, kind: str, **options) -> str:
    return _signer.dumps({"url": url, "kind": kind, **options})


def _choice(url: str, kind: str, label: str, detail: str, choice_id: str | None = None, **options) -> dict:
    token = _make_token(url, kind, **options)
    return {
        "id": choice_id or f"{kind}:{options.get('height') or options.get('codec') or options.get('bitrate') or 'original'}",
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
        size_text = f" · sekitar {size}" if size else " · ukuran dihitung saat mengunduh"
        has_h264 = any(_is_h264(fmt) and fmt.get("height") == height for fmt in formats)
        if has_h264:
            # The source already offers H.264 here, so the MP4 is the original stream.
            video_choices.append(_choice(
                url, "video", f"{height}p", "MP4 · kualitas asli" + size_text,
                choice_id=f"video:{height}", height=height, estimated_bytes=estimated_bytes,
            ))
            continue
        # Only VP9/AV1 exists at this resolution (common above 1080p). Keep the
        # source streams untouched, and offer an MP4 copy for older devices.
        video_choices.append(_choice(
            url, "video", f"{height}p", "Kualitas asli · WebM/MKV · tanpa konversi" + size_text,
            choice_id=f"video:{height}:original", height=height, container="original",
            estimated_bytes=estimated_bytes,
        ))
        video_choices.append(_choice(
            url, "video", f"{height}p MP4", "MP4 untuk iPhone/TV · dikonversi ulang · lebih lama",
            choice_id=f"video:{height}:mp4", height=height, estimated_bytes=estimated_bytes,
        ))

    duration = info.get("duration")
    audio_choices = []
    source_audio = _best_audio_format(formats)
    if source_audio:
        m4a_audio = max(
            (fmt for fmt in formats if fmt.get("vcodec") == "none" and fmt.get("ext") == "m4a"
             and fmt.get("acodec") not in (None, "none")),
            key=lambda fmt: fmt.get("abr") or fmt.get("tbr") or 0,
            default=None,
        )
        originals = [source_audio]
        if m4a_audio and m4a_audio.get("format_id") != source_audio.get("format_id"):
            originals.append(m4a_audio)
        for fmt in originals:
            extension = str(fmt.get("ext") or "").lower()
            name = AUDIO_FORMAT_NAMES.get(extension, extension.upper() or "Audio")
            bitrate = fmt.get("abr") or fmt.get("tbr")
            estimated_bytes = _format_size(fmt, duration)
            size = _pretty_bytes(estimated_bytes)
            detail = " · ".join(filter(None, [
                name,
                f"{round(bitrate)} kbps" if bitrate else None,
                "tanpa konversi",
                f"sekitar {size}" if size else None,
            ]))
            is_best = fmt is source_audio
            audio_choices.append(_choice(
                url, "audio", "Audio asli" if is_best else "Audio asli M4A", detail,
                choice_id="audio:source" if is_best else "audio:source-m4a",
                codec="source", format_id=str(fmt.get("format_id") or ""), estimated_bytes=estimated_bytes,
            ))

        for bitrate in (128, 192, 320):
            estimated_bytes = int(bitrate * 1000 * duration / 8) if duration else None
            size = _pretty_bytes(estimated_bytes)
            audio_choices.append(_choice(
                url, "audio", f"MP3 {bitrate} kbps",
                f"MP3 · {bitrate} kbps · dikonversi" + (f" · sekitar {size}" if size else ""),
                choice_id=f"audio:mp3:{bitrate}", codec="mp3", bitrate=bitrate, estimated_bytes=estimated_bytes,
            ))

    return {"video": video_choices, "audio": audio_choices, "photo": []}


def _subtitle_track_key(tracks: dict, language: str, automatic: bool) -> str | None:
    """Return the track key for a language, or None when it is unavailable.

    Automatic captions only count in the video's original spoken language
    (yt-dlp marks it ``<lang>-orig``); YouTube's machine translations into
    other languages are not offered.
    """

    if automatic:
        key = f"{language}-orig"
        return key if tracks.get(key) else None
    if tracks.get(language):
        return language
    for key in sorted(tracks):
        if key.split("-")[0].lower() == language and tracks[key]:
            return key
    return None


def _subtitle_choices(url: str, info: dict) -> list[dict]:
    manual = info.get("subtitles") or {}
    automatic = info.get("automatic_captions") or {}
    choices = []
    for language in SUBTITLE_LANGUAGES:
        is_automatic = False
        key = _subtitle_track_key(manual, language, automatic=False)
        if not key:
            key = _subtitle_track_key(automatic, language, automatic=True)
            is_automatic = bool(key)
        if key:
            choices.append({
                "id": f"subtitle:{language}",
                "language": language,
                "automatic": is_automatic,
                "token": _make_token(url, "subtitle", lang=key, automatic=is_automatic),
            })
    return choices


def _decode_subtitle_token(token: str, video_payload: dict) -> dict:
    subtitle = _signer.loads(token, max_age=TOKEN_MAX_AGE)
    if subtitle.get("kind") != "subtitle" or video_payload.get("kind") != "video":
        raise UserFacingError("Pilihan subtitle tidak valid.")
    if subtitle.get("url") != video_payload.get("url"):
        raise UserFacingError("Pilihan subtitle tidak cocok dengan video ini.")
    language = str(subtitle.get("lang") or "")
    if not re.fullmatch(r"[A-Za-z0-9-]{1,32}", language):
        raise UserFacingError("Pilihan subtitle tidak valid.")
    return {"lang": language, "automatic": bool(subtitle.get("automatic"))}


def _media_payload(url: str, info: dict) -> dict:
    entries = _entries(info)
    thumbnail = info.get("thumbnail")
    if not thumbnail and entries:
        thumbnail = entries[0].get("thumbnail")
    choices = _build_choices(url, info)

    return {
        "title": info.get("title") or info.get("description") or "Media tanpa judul",
        "uploader": info.get("uploader") or info.get("channel") or info.get("creator"),
        "duration": info.get("duration"),
        "thumbnail": thumbnail,
        "platform": info.get("extractor_key") or info.get("extractor") or "Unknown",
        "item_count": len(entries),
        "choices": choices,
        "subtitles": _subtitle_choices(url, info) if choices["video"] else [],
        "subtitle_burn": _subtitle_burn_supported(),
        "limits": {
            "max_file_mb": MAX_MEDIA_BYTES // (1024 * 1024),
            "token_minutes": TOKEN_MAX_AGE // 60,
        },
    }


def _find_downloaded_file(folder: Path) -> Path:
    ignored_suffixes = {".part", ".ytdl", ".temp", *SUBTITLE_SUFFIXES}
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


@functools.lru_cache(maxsize=4)
def _ffmpeg_filters(ffmpeg: str) -> frozenset[str]:
    result = subprocess.run([ffmpeg, "-hide_banner", "-filters"], capture_output=True, text=True, timeout=30)
    names = set()
    for line in result.stdout.splitlines():
        parts = line.split()
        if len(parts) >= 3 and "->" in parts[2]:
            names.add(parts[1])
    return frozenset(names)


def _subtitle_burn_supported() -> bool:
    """Whether FFmpeg can draw subtitles into video (needs libass).

    Some builds, such as Homebrew's default ffmpeg formula, leave libass out;
    on those only the selectable subtitle track is offered.
    """

    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        return False
    try:
        return "subtitles" in _ffmpeg_filters(ffmpeg)
    except (OSError, subprocess.SubprocessError):
        return False


SUBTITLE_BURN_UNAVAILABLE = (
    "Subtitle permanen tidak tersedia di server ini karena FFmpeg-nya dibuat tanpa libass. "
    "Pilih mode \u201cBisa dinyalakan\u201d."
)


def _ffmpeg_error_tail(exc: subprocess.CalledProcessError) -> str:
    stderr = exc.stderr or b""
    if isinstance(stderr, bytes):
        stderr = stderr.decode("utf-8", "replace")
    return " | ".join(line.strip() for line in stderr.strip().splitlines()[-5:])


def _read_ffmpeg_progress(stream, on_progress) -> None:
    for raw_line in stream:
        key, _, value = raw_line.decode("utf-8", "ignore").strip().partition("=")
        if key == "out_time_us" and value.isdigit():
            try:
                on_progress(int(value) / 1_000_000)
            except Exception:
                # Cancellation is enforced by the waiting loop, not this reader.
                pass


def _run_media_command(
    command: list[str],
    cancelled: threading.Event | None = None,
    cwd: Path | None = None,
    on_progress=None,
) -> None:
    """Run FFmpeg so that cancellation and FFMPEG_TIMEOUT stop it early.

    With ``on_progress``, the command must include ``-progress pipe:1``; the
    callback receives the number of output seconds written so far.
    """

    process = subprocess.Popen(
        command,
        cwd=cwd,
        stdout=subprocess.PIPE if on_progress else subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )
    stderr_chunks = []
    readers = [threading.Thread(target=lambda: stderr_chunks.append(process.stderr.read()), daemon=True)]
    if on_progress:
        readers.append(threading.Thread(target=_read_ffmpeg_progress, args=(process.stdout, on_progress), daemon=True))
    for reader in readers:
        reader.start()
    deadline = time.monotonic() + FFMPEG_TIMEOUT
    try:
        while True:
            try:
                process.wait(timeout=0.5)
                break
            except subprocess.TimeoutExpired:
                if cancelled and cancelled.is_set():
                    raise UserFacingError("Unduhan dibatalkan.")
                if time.monotonic() > deadline:
                    raise UserFacingError("Konversi video melebihi batas waktu server.")
    except BaseException:
        process.kill()
        process.wait()
        raise
    finally:
        for reader in readers:
            reader.join(timeout=5)
        for stream in (process.stdout, process.stderr):
            if stream:
                stream.close()
    if process.returncode:
        raise subprocess.CalledProcessError(process.returncode, command, stderr=b"".join(stderr_chunks))


def _json3_cues(data: dict, automatic: bool) -> list[tuple[int, int, str]]:
    """Read YouTube's json3 caption events as (start ms, end ms, text) cues."""

    cues = []
    for event in data.get("events") or []:
        segments = event.get("segs") if isinstance(event, dict) else None
        if not segments or "tStartMs" not in event:
            continue
        text = "".join(str(segment.get("utf8") or "") for segment in segments if isinstance(segment, dict))
        text = "\n".join(line.strip() for line in text.splitlines() if line.strip())
        if not text:
            continue
        start = int(event["tStartMs"])
        cues.append([start, start + int(event.get("dDurationMs") or 0), text])
    cues.sort(key=lambda cue: cue[0])
    if automatic:
        # Automatic captions roll: each line stays until well after the next
        # one appears. Ending each line when the next starts shows one clean
        # line at a time instead of stacked duplicates.
        for current, following in zip(cues, cues[1:]):
            current[1] = min(current[1], following[0])
    return [(start, end, text) for start, end, text in cues if end > start]


def _srt_timestamp(milliseconds: int) -> str:
    hours, remainder = divmod(milliseconds, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    seconds, millis = divmod(remainder, 1000)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d},{millis:03d}"


def _srt_from_cues(cues: list[tuple[int, int, str]]) -> str:
    blocks = [
        f"{index}\n{_srt_timestamp(start)} --> {_srt_timestamp(end)}\n{text}\n"
        for index, (start, end, text) in enumerate(cues, start=1)
    ]
    return "\n".join(blocks)


def _prepare_subtitle(folder: Path, subtitle: dict) -> Path:
    """Turn the downloaded subtitle into a file FFmpeg can burn into video."""

    candidates = sorted(
        path for path in folder.iterdir()
        if path.is_file() and path.suffix.lower() in SUBTITLE_SUFFIXES
    )
    if not candidates:
        raise UserFacingError("Subtitle yang dipilih tidak tersedia untuk video ini.")
    source = candidates[0]
    suffix = source.suffix.lower()

    if suffix == ".json3":
        try:
            data = json.loads(source.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise UserFacingError("File subtitle dari platform tidak dapat dibaca.") from exc
        cues = _json3_cues(data, subtitle.get("automatic", False))
        if not cues:
            raise UserFacingError("Subtitle yang dipilih kosong.")
        target = folder / "linkdrop-subtitle.srt"
        target.write_text(_srt_from_cues(cues), encoding="utf-8")
        source.unlink()
        return target
    if suffix in BURNABLE_SUBTITLE_SUFFIXES:
        target = folder / f"linkdrop-subtitle{suffix}"
        source.replace(target)
        return target
    raise UserFacingError("Format subtitle dari platform ini belum didukung.")


def _embed_subtitle_track(
    path: Path,
    subtitle: Path,
    language: str,
    progress=None,
    cancelled: threading.Event | None = None,
) -> Path:
    """Add the subtitle as a selectable track without re-encoding audio or video."""

    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise UserFacingError("FFmpeg tidak tersedia untuk menambahkan subtitle.")
    suffix = path.suffix.lower()
    if suffix not in SUBTITLE_STREAM_CODECS:
        suffix = ".mkv"
    codec = SUBTITLE_STREAM_CODECS[suffix]
    tag, title = SUBTITLE_LANGUAGE_TAGS.get(language.split("-")[0].lower(), ("und", "Subtitle"))
    if progress:
        progress({
            "percent": 92,
            "phase": "Menambahkan subtitle",
            "detail": "Menyisipkan track subtitle tanpa mengubah video…",
        })

    output = path.with_name(f"{path.stem}.linkdrop{suffix}")
    command = [
        ffmpeg, "-y",
        "-i", f"./{path.name}",
        "-i", f"./{subtitle.name}",
        "-map", "0:v", "-map", "0:a?", "-map", "1:0",
        "-c", "copy", "-c:s", codec,
        "-metadata:s:s:0", f"language={tag}",
        "-metadata:s:s:0", f"title={title}",
        "-disposition:s:0", "default",
    ]
    if codec == "mov_text":
        command += ["-movflags", "+faststart"]
    command.append(f"./{output.name}")
    try:
        _run_media_command(command, cancelled, cwd=path.parent)
    except subprocess.CalledProcessError as exc:
        output.unlink(missing_ok=True)
        app.logger.warning("FFmpeg failed to add the subtitle track: %s", _ffmpeg_error_tail(exc))
        raise UserFacingError("Subtitle gagal ditambahkan ke video.") from exc
    except BaseException:
        output.unlink(missing_ok=True)
        raise

    path.unlink()
    final = path.with_suffix(suffix)
    output.replace(final)
    return final


def _ensure_compatible_mp4(
    path: Path,
    progress=None,
    cancelled: threading.Event | None = None,
    subtitle: Path | None = None,
) -> Path:
    """Transcode incompatible MP4 codecs while retaining the selected resolution.

    With ``subtitle``, the captions are always burned into the picture, so the
    video is re-encoded even when its codecs are already compatible.
    """

    if path.suffix.lower() != ".mp4" and not subtitle:
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
                "-show_entries", "stream=codec_type,codec_name,pix_fmt:format=duration",
                "-of", "json",
                str(path),
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        probe_data = json.loads(probe.stdout)
        streams = probe_data.get("streams") or []
    except (subprocess.CalledProcessError, json.JSONDecodeError) as exc:
        raise UserFacingError("File video hasil unduhan tidak dapat divalidasi.") from exc

    if not subtitle and _mp4_streams_are_compatible(streams):
        return path

    if subtitle:
        phase = "Menempelkan subtitle ke video"
        detail = "Menggabungkan teks subtitle ke gambar video…"
    else:
        phase = "Menyesuaikan kompatibilitas video"
        detail = "Mengonversi video ke H.264 dan audio ke AAC…"
    if progress:
        progress({"percent": 92, "phase": phase, "detail": detail})

    try:
        duration = float((probe_data.get("format") or {}).get("duration") or 0)
    except (TypeError, ValueError):
        duration = 0
    last_reported = {"percent": -1}

    def report_conversion(seconds: float) -> None:
        if not progress or duration <= 0:
            return
        percent = min(int(seconds / duration * 100), 99)
        if percent > last_reported["percent"]:
            last_reported["percent"] = percent
            progress({"percent": 92, "phase": phase, "detail": f"{detail} {percent}%"})

    converted = path.with_name(f"{path.stem}.linkdrop.mp4")
    command = [ffmpeg, "-y", "-nostats", "-progress", "pipe:1", "-i", f"./{path.name}", "-map", "0:v:0", "-map", "0:a?"]
    if subtitle:
        command += ["-vf", f"subtitles={subtitle.name}:force_style='{SUBTITLE_STYLE}'"]
    command += [
        "-c:v", "libx264",
        "-preset", "fast",
        "-crf", "18",
        "-pix_fmt", "yuv420p",
        "-c:a", "aac",
        "-b:a", "192k",
        "-movflags", "+faststart",
        f"./{converted.name}",
    ]
    try:
        _run_media_command(command, cancelled, cwd=path.parent, on_progress=report_conversion)
    except subprocess.CalledProcessError as exc:
        converted.unlink(missing_ok=True)
        app.logger.warning("FFmpeg conversion failed: %s", _ffmpeg_error_tail(exc))
        if subtitle:
            if not _subtitle_burn_supported():
                raise UserFacingError(SUBTITLE_BURN_UNAVAILABLE) from exc
            raise UserFacingError("Subtitle gagal ditempelkan ke video.") from exc
        raise UserFacingError("Video gagal dikonversi ke format MP4 yang kompatibel.") from exc
    except BaseException:
        converted.unlink(missing_ok=True)
        raise

    path.unlink()
    final = path.with_suffix(".mp4")
    converted.replace(final)
    return final


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
            if Path(str(status.get("filename") or "")).suffix.lower() in SUBTITLE_SUFFIXES:
                return
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
        if payload.get("container") == "original":
            options.update({
                "format": _original_video_format_selector(height),
                "merge_output_format": "webm/mkv",
            })
        else:
            options.update({
                "format": _video_format_selector(height),
                "merge_output_format": "mp4",
                "postprocessor_args": {"Merger": ["-movflags", "+faststart"]},
            })
        if subtitle := payload.get("subtitle"):
            options.update({
                "writesubtitles": not subtitle["automatic"],
                "writeautomaticsub": subtitle["automatic"],
                "subtitleslangs": [re.escape(subtitle["lang"])],
                "subtitlesformat": "json3/srt/vtt/best",
            })
    elif kind == "audio":
        codec = payload.get("codec")
        options["format"] = "bestaudio/best"
        format_id = str(payload.get("format_id") or "")
        if codec == "source" and re.fullmatch(r"[A-Za-z0-9_.-]{1,64}", format_id):
            options["format"] = f"{format_id}/bestaudio/best"
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
        with _public_network_only(), yt_dlp.YoutubeDL({**options, **auth}) as ydl:
            ydl.download([url])

    result = _find_downloaded_file(folder)
    if kind == "video":
        subtitle = payload.get("subtitle")
        subtitle_file = _prepare_subtitle(folder, subtitle) if subtitle else None
        if subtitle_file and subtitle.get("burn"):
            result = _ensure_compatible_mp4(result, progress, cancelled, subtitle_file)
        else:
            if payload.get("container") != "original":
                result = _ensure_compatible_mp4(result, progress, cancelled)
            if subtitle_file:
                result = _embed_subtitle_track(result, subtitle_file, subtitle["lang"], progress, cancelled)
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


class _ByteBudget:
    """Byte allowance shared by downloads that run at the same time."""

    def __init__(self, limit: int):
        self.limit = limit
        self.used = 0
        self._lock = threading.Lock()

    def remaining(self) -> int:
        with self._lock:
            return self.limit - self.used

    def take(self, size: int) -> None:
        with self._lock:
            if self.used + size > self.limit:
                raise UserFacingError("Ukuran media melebihi batas server.")
            self.used += size


class _AnyEvent:
    """Read-only view that is set when any of the given events is set."""

    def __init__(self, *events):
        self._events = [event for event in events if event is not None]

    def is_set(self) -> bool:
        return any(event.is_set() for event in self._events)


def _download_remote(
    source: tuple[str, dict, str],
    destination: Path,
    budget: int | _ByteBudget,
    progress=None,
    cancelled: threading.Event | None = None,
) -> int:
    with _public_network_only():
        try:
            return _fetch_remote(source, destination, budget, progress, cancelled)
        except requests.RequestException as exc:
            if PRIVATE_NETWORK_MESSAGE in str(exc):
                raise PrivateNetworkError(PRIVATE_NETWORK_MESSAGE) from exc
            raise


def _fetch_remote(
    source: tuple[str, dict, str],
    destination: Path,
    budget: int | _ByteBudget,
    progress=None,
    cancelled: threading.Event | None = None,
) -> int:
    if not isinstance(budget, _ByteBudget):
        budget = _ByteBudget(budget)
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
        if content_length and content_length > budget.remaining():
            raise UserFacingError("Ukuran media melebihi batas server.")
        with destination.open("wb") as output:
            for chunk in response.iter_content(CHUNK_SIZE):
                if cancelled and cancelled.is_set():
                    raise UserFacingError("Unduhan dibatalkan.")
                if not chunk:
                    continue
                budget.take(len(chunk))
                downloaded += len(chunk)
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

    items = []
    for index, entry in enumerate(entries, start=1):
        source = _best_image_source(entry) if _is_image(entry) else _best_progressive_video_source(entry)
        if not source:
            raise UserFacingError(f"Format item ke-{index} tidak dapat diunduh.")
        extension = secure_filename(source[2].lower()) or ("jpg" if _is_image(entry) else "mp4")
        items.append((index, source, folder / f"{index:02d}.{extension}"))

    # Files are copied as-is, so the time goes into waiting on the network.
    # Several items are fetched at once; they share one byte budget.
    shared_budget = _ByteBudget(budget)
    stop = threading.Event()
    should_stop = _AnyEvent(cancelled, stop)
    fractions = {index: 0.0 for index, _, _ in items}
    state_lock = threading.Lock()
    finished = {"count": 0}

    def report(index: int, fraction: float) -> None:
        if not progress:
            return
        with state_lock:
            fractions[index] = fraction
            overall = sum(fractions.values()) / len(items)
            completed = finished["count"]
        progress({
            "percent": round(5 + overall * 82, 1),
            "phase": f"Mengunduh {len(items)} item",
            "detail": f"{completed} dari {len(items)} selesai · mengambil media asli…",
        })

    def fetch(index: int, source: tuple[str, dict, str], destination: Path) -> None:
        if should_stop.is_set():
            raise UserFacingError("Unduhan dibatalkan.")
        _download_remote(
            source,
            destination,
            shared_budget,
            progress=lambda fraction: report(index, fraction),
            cancelled=should_stop,
        )
        with state_lock:
            finished["count"] += 1
        report(index, 1.0)

    with ThreadPoolExecutor(max_workers=min(GALLERY_CONCURRENCY, len(items)), thread_name_prefix="linkdrop-gallery") as pool:
        futures = [pool.submit(fetch, *item) for item in items]
        try:
            for future in as_completed(futures):
                future.result()
        except BaseException:
            stop.set()
            for future in futures:
                future.cancel()
            raise

    return info.get("title") or info.get("id") or "media", [destination for _, _, destination in items]


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
    failures = {}
    state = {"completed": 0, "archived_bytes": 0}
    item_percents = {index: 0.0 for index in range(1, len(payloads) + 1)}
    archive_lock = threading.Lock()
    progress_lock = threading.Lock()
    stop = threading.Event()
    should_stop = _AnyEvent(cancelled, stop)

    def process(index: int, payload: dict, bundle: zipfile.ZipFile) -> None:
        if should_stop.is_set():
            raise UserFacingError("Unduhan dibatalkan.")
        item_folder = folder / f"item-{index:02d}"
        item_folder.mkdir()

        def item_progress(event):
            if not progress:
                return
            item_percent = max(0, min(float(event.get("percent") or 0), 100))
            with progress_lock:
                item_percents[index] = item_percent
                overall = sum(item_percents.values()) / 100 / len(payloads) * 88
            progress({
                "percent": round(overall, 1),
                "item_index": index,
                "item_count": len(payloads),
                "item_percent": round(item_percent, 1),
                "phase": f"{event.get('phase') or 'Memproses media'} · {index}/{len(payloads)}",
                "detail": event.get("detail") or "",
            })

        try:
            if payload["kind"] == "gallery":
                with archive_lock:
                    remaining = storage_limit - state["archived_bytes"]
                title, results = _download_gallery_items(payload, item_folder, remaining, item_progress, should_stop)
            else:
                results = [_download_with_retry(payload, item_folder, item_progress, should_stop)]
                title = None
            result_size = sum(result.stat().st_size for result in results)
            with archive_lock:
                if state["archived_bytes"] + result_size > storage_limit:
                    raise UserFacingError("Ukuran aktual media melewati sisa kapasitas paket.")
                if len(results) == 1:
                    bundle.write(results[0], arcname=_unique_archive_name(results[0].name, used_names))
                else:
                    archive_folder = _unique_archive_folder(title, used_names)
                    for result in results:
                        bundle.write(result, arcname=f"{archive_folder}/{result.name}")
                state["archived_bytes"] += result_size
                state["completed"] += 1
        except requests.RequestException as exc:
            failures[index] = f"Media {index}: {_friendly_error(exc)}"
        except OSError:
            raise
        except Exception as exc:
            if should_stop.is_set():
                raise
            failures[index] = f"Media {index}: {_friendly_error(exc)}"
        finally:
            shutil.rmtree(item_folder, ignore_errors=True)
            with progress_lock:
                item_percents[index] = 100.0

    # Items run a few at a time: downloads mostly wait on the network, while
    # the limit keeps CPU and temporary disk use bounded during conversions.
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_STORED, allowZip64=True) as bundle:
        with ThreadPoolExecutor(
            max_workers=min(BATCH_CONCURRENCY, len(payloads)),
            thread_name_prefix="linkdrop-batch",
        ) as pool:
            futures = [pool.submit(process, index, payload, bundle) for index, payload in enumerate(payloads, start=1)]
            try:
                for future in as_completed(futures):
                    future.result()
            except BaseException:
                stop.set()
                for future in futures:
                    future.cancel()
                raise

        if failures:
            bundle.writestr(
                "linkdrop-report.txt",
                "Beberapa media tidak dapat diproses:\n\n" + "\n".join(failures[index] for index in sorted(failures)),
            )

    completed = state["completed"]
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


def _remove_after_thread(thread: threading.Thread, folder: Path) -> None:
    thread.join()
    shutil.rmtree(folder, ignore_errors=True)


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
        last_progress = {"percent": 0, "phase": "Memulai unduhan", "detail": ""}
        try:
            while True:
                try:
                    event_type, value = events.get(timeout=PROGRESS_HEARTBEAT_SECONDS)
                except queue.Empty:
                    # Repeat the last status so proxies such as Cloudflare do not
                    # close a stream that is quiet during a long conversion.
                    yield _progress_frame(b"P", last_progress)
                    continue
                if event_type == "progress":
                    last_progress = value
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
            else:
                # The client left while the worker was still running. The worker
                # may still finish and hand over a file nobody will read, so
                # remove the folder once it stops instead of leaking it.
                threading.Thread(
                    target=_remove_after_thread,
                    args=(download_thread, cleanup_folder),
                    name="linkdrop-cleanup",
                    daemon=True,
                ).start()

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


class _RateLimiter:
    """In-memory sliding window, enough for the single-worker deployment."""

    def __init__(self):
        self._hits: dict[tuple, list[float]] = {}
        self._lock = threading.Lock()

    def _recent(self, key: tuple, window: int, now: float) -> list[float]:
        return [moment for moment in self._hits.get(key, ()) if now - moment < window]

    def retry_after(self, key: tuple, limit: int, window: int, record: bool = True) -> int:
        """Return seconds to wait when the limit is reached, otherwise 0."""

        now = time.monotonic()
        with self._lock:
            hits = self._recent(key, window, now)
            if len(hits) >= limit:
                self._hits[key] = hits
                return max(1, int(window - (now - hits[0])) + 1)
            if record:
                hits.append(now)
            self._hits[key] = hits
            if len(self._hits) > 10_000:
                self._hits = {
                    item: moments for item, moments in self._hits.items()
                    if moments and now - moments[-1] < 3600
                }
            return 0

    def reset(self) -> None:
        with self._lock:
            self._hits.clear()


_rate_limiter = _RateLimiter()


def _client_address() -> str:
    """Identify the client, trusting only the header named by the operator.

    Behind Cloudflare Tunnel every request arrives from cloudflared on
    localhost, so the real address comes from ``CF-Connecting-IP``. For a
    comma-separated header such as ``X-Forwarded-For`` the last entry is the
    one appended by the trusted proxy; earlier entries can be forged.
    """

    if TRUSTED_PROXY_HEADER:
        value = request.headers.get(TRUSTED_PROXY_HEADER, "")
        candidate = value.split(",")[-1].strip()
        if candidate:
            return candidate
    return request.remote_addr or "unknown"


def _error_response(message: str, status: int, retry_after: int | None = None):
    response = jsonify({"error": message})
    response.status_code = status
    if retry_after:
        response.headers["Retry-After"] = str(retry_after)
    return response


def _has_valid_credentials() -> bool:
    auth = request.authorization
    if not auth or auth.type != "basic":
        return False
    username_ok = hmac.compare_digest((auth.username or "").encode("utf-8"), ACCESS_USERNAME.encode("utf-8"))
    password_ok = hmac.compare_digest((auth.password or "").encode("utf-8"), ACCESS_PASSWORD.encode("utf-8"))
    return username_ok and password_ok


@app.before_request
def _access_control():
    if request.endpoint in {"health", "static"}:
        return None
    client = _client_address()

    if ACCESS_PASSWORD:
        login_key = ("failed-login", client)
        limit, window = FAILED_LOGIN_LIMIT
        retry_after = _rate_limiter.retry_after(login_key, limit, window, record=False)
        if retry_after:
            return _error_response("Terlalu banyak percobaan masuk. Coba lagi nanti.", 429, retry_after)
        if not _has_valid_credentials():
            if request.authorization:
                _rate_limiter.retry_after(login_key, limit, window)
            response = _error_response("Masukkan nama pengguna dan kata sandi Linkdrop.", 401)
            response.headers["WWW-Authenticate"] = 'Basic realm="Linkdrop", charset="UTF-8"'
            return response

    bucket = RATE_LIMITED_ENDPOINTS.get(request.endpoint)
    rate_limit = RATE_LIMITS.get(bucket) if bucket else None
    if rate_limit:
        retry_after = _rate_limiter.retry_after((bucket, client), *rate_limit)
        if retry_after:
            return _error_response(
                f"Terlalu banyak permintaan. Coba lagi dalam {retry_after} detik.",
                429,
                retry_after,
            )
    return None


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
        "version": os.environ.get("LINKDROP_VERSION", "dev"),
        "runtime": "vercel" if IS_VERCEL else "local",
        "ffmpeg": bool(shutil.which("ffmpeg")),
        "js_runtime": next(iter(js_runtimes), None),
        "subtitle_burn": _subtitle_burn_supported(),
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
        if subtitle_token := request.args.get("subtitle"):
            payload["subtitle"] = _decode_subtitle_token(subtitle_token, payload)
            payload["subtitle"]["burn"] = request.args.get("subtitle_mode") == "burn"
            # Fail before downloading anything rather than at the last step.
            if payload["subtitle"]["burn"] and not _subtitle_burn_supported():
                raise UserFacingError(SUBTITLE_BURN_UNAVAILABLE)
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
        try:
            package_number = int(body.get("package") or 1)
        except (TypeError, ValueError):
            raise UserFacingError("Nomor paket tidak valid.") from None
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
