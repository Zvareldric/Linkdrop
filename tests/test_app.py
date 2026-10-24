import base64
import http.server
import io
import json
import os
import shutil
import socket
import subprocess
import struct
import sys
import tempfile
import threading
import time
import unittest
import zipfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import app as media_app


SAMPLE_INFO = {
    "title": "Contoh Video",
    "uploader": "Creator",
    "duration": 125,
    "thumbnail": "https://cdn.example.com/thumb.jpg",
    "extractor_key": "YouTube",
    "formats": [
        {
            "format_id": "18",
            "height": 360,
            "vcodec": "h264",
            "acodec": "aac",
            "filesize": 4_000_000,
            "url": "https://cdn.example.com/360.mp4",
        },
        {
            "format_id": "137",
            "height": 1080,
            "vcodec": "h264",
            "acodec": "none",
            "filesize": 20_000_000,
            "url": "https://cdn.example.com/1080.mp4",
        },
        {
            "format_id": "140",
            "ext": "m4a",
            "vcodec": "none",
            "acodec": "aac",
            "filesize": 2_000_000,
            "abr": 128,
            "url": "https://cdn.example.com/audio.m4a",
        },
    ],
}


def parse_progress_frames(body):
    frames = []
    offset = 0
    while offset < len(body):
        frame_type, frame_length = struct.unpack(">cI", body[offset:offset + 5])
        offset += 5
        payload = body[offset:offset + frame_length]
        offset += frame_length
        frames.append((frame_type, payload))
    return frames


class AppTests(unittest.TestCase):
    def setUp(self):
        media_app.app.config.update(TESTING=True)
        media_app._rate_limiter.reset()
        self.client = media_app.app.test_client()

    def test_health_endpoint(self):
        response = self.client.get("/api/health")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.get_json()["ok"])

    def test_health_reports_running_version(self):
        self.assertEqual(self.client.get("/api/health").get_json()["version"], "dev")
        with patch.dict(media_app.os.environ, {"LINKDROP_VERSION": "a71a35f"}):
            self.assertEqual(self.client.get("/api/health").get_json()["version"], "a71a35f")

    @patch("app.secrets.token_urlsafe", return_value="generated-local-secret")
    def test_token_secret_is_random_when_not_configured(self, token_urlsafe_mock):
        secret = media_app._token_secret_for_runtime("", False)

        self.assertEqual(secret, "generated-local-secret")
        token_urlsafe_mock.assert_called_once_with(32)

    def test_token_secret_is_required_on_vercel(self):
        with self.assertRaisesRegex(RuntimeError, "DOWNLOAD_TOKEN_SECRET"):
            media_app._token_secret_for_runtime("", True)

    @patch("app.shutil.which")
    def test_node_runtime_is_enabled_for_youtube_challenges(self, which_mock):
        which_mock.side_effect = lambda executable: (
            "/usr/local/bin/node" if executable == "node" else None
        )

        options = media_app._base_ydl_options()

        self.assertEqual(
            options["js_runtimes"],
            {"node": {"path": "/usr/local/bin/node"}},
        )

    def test_arc_cookie_adapter_is_registered(self):
        self.assertIn("arc", media_app.ytdlp_cookies.SUPPORTED_BROWSERS)

    def test_instagram_photo_adapter_exposes_image_formats(self):
        result = media_app.InstagramIE._extract_product_media(None, {
            "pk": "123456789",
            "image_versions2": {
                "candidates": [{
                    "url": "https://cdn.example.com/original.jpg?signature=example",
                    "width": 1440,
                    "height": 1080,
                }],
            },
        })

        self.assertEqual(result["ext"], "jpg")
        self.assertEqual(result["_linkdrop_media_type"], "image")
        self.assertEqual(result["formats"][0]["vcodec"], "image")
        self.assertEqual(result["formats"][0]["width"], 1440)

    def test_instagram_progressive_video_without_acodec_is_downloadable(self):
        source = media_app._best_progressive_video_source({
            "formats": [{
                "url": "https://cdn.example.com/video.mp4",
                "ext": "mp4",
                "vcodec": "h264",
                "height": 1080,
            }],
        })

        self.assertIsNotNone(source)
        self.assertEqual(source[0], "https://cdn.example.com/video.mp4")

    def test_instagram_progressive_video_gets_compatible_codec_metadata(self):
        result = media_app.InstagramIE._extract_product_media(None, {
            "pk": "123456789",
            "video_versions": [{
                "id": "progressive",
                "url": "https://cdn.example.com/video.mp4",
                "width": 1080,
                "height": 720,
            }],
        })

        progressive = result["formats"][0]
        self.assertEqual(progressive["vcodec"], "h264")
        self.assertEqual(progressive["acodec"], "aac")
        self.assertTrue(progressive["_linkdrop_progressive"])

    def test_twitter_photo_post_uses_original_images(self):
        class ExtractorStub:
            @staticmethod
            def playlist_result(entries, playlist_id, title, description):
                return {
                    "_type": "playlist",
                    "entries": entries,
                    "id": playlist_id,
                    "title": title,
                    "description": description,
                }

        result = media_app._twitter_photo_result(ExtractorStub(), {
            "full_text": "Contoh post",
            "user": {"name": "Creator", "screen_name": "creator"},
            "extended_entities": {
                "media": [
                    {
                        "id_str": "photo-1",
                        "type": "photo",
                        "media_url_https": "https://pbs.twimg.com/media/example.jpg",
                        "original_info": {"width": 1024, "height": 1942},
                    },
                    {
                        "id_str": "photo-2",
                        "type": "photo",
                        "media_url_https": "https://pbs.twimg.com/media/example2.jpg",
                        "original_info": {"width": 960, "height": 1916},
                    },
                ],
            },
        }, "tweet-1")

        self.assertEqual(result["_type"], "playlist")
        self.assertEqual(len(result["entries"]), 2)
        self.assertEqual(result["entries"][0]["_linkdrop_media_type"], "image")
        self.assertIn("name=orig", result["entries"][0]["formats"][0]["url"])

    def test_twitter_photo_fallback_ignores_missing_status(self):
        self.assertIsNone(media_app._twitter_photo_result(None, None, "tweet-1"))

    @patch("app.yt_dlp.YoutubeDL")
    def test_extract_info_rejects_empty_extractor_result(self, youtube_dl):
        youtube_dl.return_value.__enter__.return_value.extract_info.return_value = None

        with self.assertRaisesRegex(media_app.UserFacingError, "tidak dapat dibaca"):
            media_app._extract_info("https://x.com/example/status/1")

    def test_homepage_renders(self):
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Linkdrop", response.data)

    def test_private_network_url_is_rejected(self):
        response = self.client.post("/api/info", json={"url": "http://127.0.0.1/private"})
        self.assertEqual(response.status_code, 422)
        self.assertIn("lokal", response.get_json()["error"].lower())

    @patch("app._validate_public_url", return_value="https://youtube.com/watch?v=example")
    @patch("app._extract_info", return_value=SAMPLE_INFO)
    def test_info_lists_quality_from_low_to_high(self, _extract, _validate):
        response = self.client.post("/api/info", json={"url": "https://youtube.com/watch?v=example"})
        payload = response.get_json()
        labels = [choice["label"] for choice in payload["choices"]["video"]]

        self.assertEqual(response.status_code, 200)
        self.assertEqual(labels, ["360p", "1080p"])
        self.assertEqual(
            [choice["label"] for choice in payload["choices"]["audio"]],
            ["Audio asli", "MP3 128 kbps", "MP3 192 kbps", "MP3 320 kbps"],
        )
        self.assertEqual(payload["choices"]["audio"][0]["detail"], "M4A (AAC) · 128 kbps · tanpa konversi · sekitar 1.9 MB")
        self.assertEqual(payload["choices"]["audio"][1]["detail"], "MP3 · 128 kbps · dikonversi · sekitar 1.9 MB")
        self.assertEqual(len({choice["id"] for choice in payload["choices"]["audio"]}), 4)
        self.assertTrue(payload["choices"]["video"][0]["download_url"].startswith("/api/download/"))
        self.assertTrue(payload["choices"]["video"][0]["token"])
        self.assertEqual(payload["choices"]["video"][0]["estimated_bytes"], 4_000_000)

    def test_video_size_uses_bitrate_when_filesize_is_missing(self):
        formats = [
            {
                "height": 720,
                "ext": "mp4",
                "vcodec": "avc1.4D401F",
                "acodec": "none",
                "tbr": 1_000,
            },
            {
                "ext": "m4a",
                "vcodec": "none",
                "acodec": "mp4a.40.2",
                "abr": 128,
            },
        ]

        self.assertEqual(media_app._estimated_video_size(formats, 720, 100), 14_100_000)

    def test_video_size_is_unknown_instead_of_showing_audio_only(self):
        formats = [
            {"height": 720, "ext": "mp4", "vcodec": "avc1", "acodec": "none"},
            {"ext": "m4a", "vcodec": "none", "acodec": "aac", "filesize": 2_000_000},
        ]

        self.assertIsNone(media_app._estimated_video_size(formats, 720))

    def test_video_size_uses_video_only_when_source_has_no_audio_stream(self):
        formats = [{
            "height": 720,
            "ext": "mp4",
            "vcodec": "avc1",
            "acodec": "none",
            "filesize": 4_000_000,
        }]

        self.assertEqual(media_app._estimated_video_size(formats, 720), 4_000_000)

    def test_video_choices_ignore_missing_audio_and_empty_format_metadata(self):
        choices = media_app._build_choices("https://example.com/media", {
            "formats": [
                None,
                {
                    "height": 720,
                    "ext": "mp4",
                    "vcodec": "h264",
                    "acodec": "none",
                    "filesize": 4_000_000,
                },
            ],
        })

        self.assertEqual([choice["label"] for choice in choices["video"]], ["720p"])
        self.assertEqual(choices["audio"], [])

    def test_best_audio_prefers_highest_source_bitrate(self):
        selected = media_app._best_audio_format([
            {"format_id": "m4a", "ext": "m4a", "vcodec": "none", "acodec": "aac", "abr": 128},
            {"format_id": "opus", "ext": "webm", "vcodec": "none", "acodec": "opus", "abr": 160},
        ])

        self.assertEqual(selected["format_id"], "opus")

    def test_video_selector_prioritizes_requested_resolution(self):
        selector = media_app._video_format_selector(2160)

        self.assertTrue(selector.startswith("b[height=2160]"))
        self.assertLess(selector.index("height=2160"), selector.index("height<=2160"))

    def test_mp4_compatibility_accepts_h264_aac(self):
        streams = [
            {"codec_type": "video", "codec_name": "h264", "pix_fmt": "yuv420p"},
            {"codec_type": "audio", "codec_name": "aac"},
        ]

        self.assertTrue(media_app._mp4_streams_are_compatible(streams))

    def test_mp4_compatibility_rejects_vp9_video(self):
        streams = [
            {"codec_type": "video", "codec_name": "vp9", "pix_fmt": "yuv420p"},
            {"codec_type": "audio", "codec_name": "aac"},
        ]

        self.assertFalse(media_app._mp4_streams_are_compatible(streams))

    def test_mp4_compatibility_rejects_unsupported_pixel_format(self):
        streams = [
            {"codec_type": "video", "codec_name": "h264", "pix_fmt": "yuv444p"},
            {"codec_type": "audio", "codec_name": "aac"},
        ]

        self.assertFalse(media_app._mp4_streams_are_compatible(streams))

    def test_tampered_download_token_is_rejected(self):
        response = self.client.get("/api/download/not-a-valid-token")
        self.assertEqual(response.status_code, 400)

    @patch("app._validate_public_url", return_value="https://example.com/video")
    @patch("app._download_video_or_audio")
    def test_download_endpoint_streams_attachment(self, download_mock, _validate):
        def make_result(_payload, folder, *_args):
            result = folder / "sample.mp4"
            result.write_bytes(b"media-bytes")
            return result

        download_mock.side_effect = make_result
        token = media_app._make_token(
            "https://example.com/video",
            "video",
            height=720,
        )
        response = self.client.get(f"/api/download/{token}")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data, b"media-bytes")
        self.assertIn("attachment", response.headers["Content-Disposition"])

    @patch("app._validate_public_url", return_value="https://example.com/video")
    @patch("app._download_video_or_audio")
    def test_progress_download_streams_status_and_complete_file(self, download_mock, _validate):
        def make_result(_payload, folder, progress, _cancelled):
            progress({"percent": 42, "phase": "Mengunduh media", "detail": "1 MB/s"})
            result = folder / "sample.mp4"
            result.write_bytes(b"media-bytes")
            return result

        download_mock.side_effect = make_result
        token = media_app._make_token(
            "https://example.com/video",
            "video",
            height=720,
        )
        response = self.client.get(f"/api/download/{token}?progress=1")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.mimetype, media_app.PROGRESS_MIME)

        frames = parse_progress_frames(response.data)

        self.assertEqual([frame[0] for frame in frames], [b"P", b"P", b"M", b"D", b"C"])
        metadata = json.loads(frames[2][1])
        self.assertEqual(metadata["filename"], "sample.mp4")
        self.assertEqual(metadata["size"], len(b"media-bytes"))
        self.assertEqual(frames[3][1], b"media-bytes")

    @patch("app.shutil.disk_usage", return_value=SimpleNamespace(free=1_000_000_000))
    def test_batch_storage_limit_reserves_temporary_space(self, _disk_usage):
        self.assertEqual(media_app._batch_storage_limit(), 400_000_000)

    @patch("app._download_payload")
    def test_youtube_403_retries_with_a_new_working_folder(self, download_mock):
        calls = []

        def download(payload, folder, progress, cancelled):
            calls.append(folder)
            if len(calls) == 1:
                raise media_app.UserFacingError("HTTP Error 403: Forbidden")
            result = folder / "retry.mp4"
            result.write_bytes(b"media-bytes")
            return result

        download_mock.side_effect = download
        events = []
        with tempfile.TemporaryDirectory() as parent:
            result = media_app._download_with_retry(
                {"url": "https://youtu.be/example", "kind": "video"},
                Path(parent),
                events.append,
            )

            self.assertEqual(result.name, "retry.mp4")
            self.assertEqual([folder.name for folder in calls], ["attempt-1", "attempt-2"])
        self.assertEqual(events[0]["phase"], "Mencoba ulang unduhan")

    def test_batch_endpoint_rejects_more_than_thirty_items(self):
        response = self.client.post("/api/batch/download", json={
            "tokens": ["token"] * (media_app.MAX_BATCH_ITEMS + 1),
        })

        self.assertEqual(response.status_code, 422)
        self.assertIn("maksimal", response.get_json()["error"].lower())

    def test_batch_endpoint_rejects_tampered_token(self):
        response = self.client.post("/api/batch/download", json={"tokens": ["invalid-token"]})

        self.assertEqual(response.status_code, 400)

    @patch("app._download_gallery_items")
    def test_single_gallery_keeps_its_zip_output(self, gallery_mock):
        def make_gallery(_payload, folder, _budget, _progress, _cancelled):
            first = folder / "01.jpg"
            second = folder / "02.jpg"
            first.write_bytes(b"one")
            second.write_bytes(b"two")
            return "Galeri Foto", [first, second]

        gallery_mock.side_effect = make_gallery
        with tempfile.TemporaryDirectory() as parent:
            result = media_app._download_gallery(
                {"url": "https://example.com/gallery", "kind": "gallery"}, Path(parent)
            )
            with zipfile.ZipFile(result) as bundle:
                self.assertEqual(set(bundle.namelist()), {"01.jpg", "02.jpg"})

    @patch("app._download_gallery_items")
    def test_batch_flattens_gallery_items_into_outer_zip(self, gallery_mock):
        def make_gallery(_payload, folder, _budget, _progress, _cancelled):
            first = folder / "01.jpg"
            second = folder / "02.jpg"
            first.write_bytes(b"one")
            second.write_bytes(b"two")
            return "Galeri Foto", [first, second]

        gallery_mock.side_effect = make_gallery
        with tempfile.TemporaryDirectory() as parent:
            result = media_app._download_batch(
                [
                    {"url": "https://example.com/gallery-one", "kind": "gallery"},
                    {"url": "https://example.com/gallery-two", "kind": "gallery"},
                ],
                Path(parent),
                1,
            )
            with zipfile.ZipFile(result) as bundle:
                self.assertEqual(set(bundle.namelist()), {
                    "Galeri_Foto/01.jpg",
                    "Galeri_Foto/02.jpg",
                    "Galeri_Foto-2/01.jpg",
                    "Galeri_Foto-2/02.jpg",
                })
                self.assertFalse(any(path.endswith(".zip") for path in bundle.namelist()))

    @patch("app._validate_public_url", side_effect=lambda url: url)
    @patch("app._download_video_or_audio")
    def test_batch_download_keeps_successful_items_when_one_fails(self, download_mock, _validate):
        def make_result(payload, folder, progress, _cancelled):
            if payload["url"].endswith("failed"):
                raise media_app.UserFacingError("Sumber menolak akses.")
            progress({"percent": 50, "phase": "Mengunduh media", "detail": "1 MB/s"})
            result = folder / "sample.mp4"
            result.write_bytes(b"media-bytes")
            return result

        download_mock.side_effect = make_result
        tokens = [
            media_app._make_token(
                "https://example.com/success",
                "video",
                height=720,
                estimated_bytes=100,
            ),
            media_app._make_token(
                "https://example.com/failed",
                "video",
                height=720,
                estimated_bytes=100,
            ),
        ]
        response = self.client.post("/api/batch/download", json={"tokens": tokens, "package": 2})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.mimetype, media_app.PROGRESS_MIME)
        frames = parse_progress_frames(response.data)
        self.assertEqual(download_mock.call_args_list[0].args[0]["height"], 720)
        metadata = json.loads(next(payload for kind, payload in frames if kind == b"M"))
        archive_bytes = b"".join(payload for kind, payload in frames if kind == b"D")
        self.assertEqual(metadata["filename"], "linkdrop-" + media_app.time.strftime("%Y-%m-%d") + "-part-2.zip")
        with zipfile.ZipFile(io.BytesIO(archive_bytes)) as bundle:
            self.assertEqual(set(bundle.namelist()), {"sample.mp4", "linkdrop-report.txt"})
            self.assertIn("Media 2", bundle.read("linkdrop-report.txt").decode("utf-8"))

    def test_youtube_reload_error_is_actionable(self):
        message = media_app._friendly_error(Exception("The page needs to be reloaded."))
        self.assertIn("analisis ulang", message.lower())

    def test_tiktok_photo_error_is_actionable(self):
        message = media_app._friendly_error(Exception(
            "ERROR: Unsupported URL: https://www.tiktok.com/@creator/photo/123456789"
        ))
        self.assertEqual(
            message,
            "Postingan foto TikTok belum didukung oleh extractor saat ini. Coba link TikTok video.",
        )

    def test_instagram_rate_limit_error_is_actionable(self):
        message = media_app._friendly_error(
            Exception("Requested content is not available, rate-limit reached or login required.")
        )
        self.assertIn("membatasi request", message.lower())

    def test_missing_browser_cookie_database_is_actionable(self):
        message = media_app._friendly_error(
            Exception('could not find chrome cookies database in "/tmp/chrome"')
        )
        self.assertIn("database cookies browser", message.lower())

    def test_stream_file_cleans_temporary_directory(self):
        with tempfile.TemporaryDirectory() as parent:
            folder = Path(parent) / "job"
            folder.mkdir()
            result = folder / "sample.txt"
            result.write_text("hello", encoding="utf-8")

            with media_app.app.test_request_context("/"):
                response = media_app._stream_file(result, folder)
                self.assertEqual(b"".join(response.response), b"hello")
            self.assertFalse(folder.exists())


def fake_resolver(mapping):
    def resolve(host, *args, **kwargs):
        address = mapping[host]
        family = socket.AF_INET6 if ":" in address else socket.AF_INET
        return [(family, socket.SOCK_STREAM, 6, "", (address, args[0] if args else 0))]
    return resolve


def basic_auth(username, password):
    token = base64.b64encode(f"{username}:{password}".encode()).decode()
    return {"Authorization": f"Basic {token}"}


class NetworkGuardTests(unittest.TestCase):
    def test_guard_blocks_private_answers_only_inside_media_requests(self):
        resolver = fake_resolver({"media.example.com": "10.0.0.5"})
        with patch.object(media_app, "_system_getaddrinfo", resolver):
            self.assertEqual(socket.getaddrinfo("media.example.com", 443)[0][4][0], "10.0.0.5")
            with media_app._public_network_only():
                with self.assertRaises(media_app.BlockedAddressError):
                    socket.getaddrinfo("media.example.com", 443)

    def test_guard_keeps_public_answers_and_drops_private_ones(self):
        def resolver(host, *args, **kwargs):
            return [
                (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("192.168.1.10", 443)),
                (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443)),
            ]

        with patch.object(media_app, "_system_getaddrinfo", resolver), media_app._public_network_only():
            answers = socket.getaddrinfo("cdn.example.com", 443)
        self.assertEqual([item[4][0] for item in answers], ["93.184.216.34"])

    def test_guard_allows_the_configured_outbound_proxy(self):
        resolver = fake_resolver({"proxy.internal": "10.0.0.2"})
        with patch.object(media_app, "_system_getaddrinfo", resolver), \
                patch.dict(os.environ, {"HTTPS_PROXY": "http://proxy.internal:3128"}), \
                media_app._public_network_only():
            self.assertEqual(socket.getaddrinfo("proxy.internal", 3128)[0][4][0], "10.0.0.2")

    def test_validation_reports_blocked_host_as_private(self):
        resolver = fake_resolver({"internal.example.com": "127.0.0.1"})
        with patch.object(media_app, "_system_getaddrinfo", resolver):
            with self.assertRaisesRegex(media_app.PrivateNetworkError, "privat"):
                media_app._validate_public_url("https://internal.example.com/video")

    def test_remote_download_does_not_follow_redirect_into_private_network(self):
        requested_paths = []

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                requested_paths.append(self.path)
                if self.path == "/start":
                    self.send_response(302)
                    self.send_header("Location", f"http://internal.test:{self.server.server_port}/secret")
                    self.end_headers()
                else:
                    self.send_response(200)
                    self.end_headers()
                    self.wfile.write(b"secret")

            def log_message(self, *args):
                pass

        server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        environment = {key: value for key, value in os.environ.items() if not key.lower().endswith("_proxy")}
        resolver = fake_resolver({"public.test": "127.0.0.1", "internal.test": "127.0.0.2"})

        with tempfile.TemporaryDirectory() as folder, \
                patch.dict(os.environ, environment, clear=True), \
                patch.object(media_app, "_system_getaddrinfo", resolver), \
                patch.object(media_app, "_is_public_address", lambda address: address == "127.0.0.1"), \
                patch.object(media_app, "_validate_public_url", lambda url: url):
            with self.assertRaises(media_app.PrivateNetworkError):
                media_app._download_remote(
                    (f"http://public.test:{server.server_port}/start", {}, "jpg"),
                    Path(folder) / "photo.jpg",
                    1024,
                )

        self.assertEqual(requested_paths, ["/start"])

    def test_wrapped_private_network_error_keeps_clear_message(self):
        message = media_app._friendly_error(Exception(
            "ERROR: [generic] Unable to download webpage: Failed to resolve "
            "([Errno -2] Alamat jaringan privat atau lokal tidak diizinkan.)"
        ))
        self.assertEqual(message, media_app.PRIVATE_NETWORK_MESSAGE)


class CleanupTests(unittest.TestCase):
    def test_progress_download_removes_files_when_client_leaves_early(self):
        created = {}

        def slow_download(payload, folder, report=None, cancelled=None):
            report({"percent": 92, "phase": "File siap"})
            time.sleep(0.3)
            result = folder / "video.mp4"
            result.write_bytes(b"0" * 1000)
            created["folder"] = folder
            return result

        with tempfile.TemporaryDirectory() as work_dir, \
                patch.object(media_app, "WORK_DIR", Path(work_dir)), \
                patch.object(media_app, "_download_with_retry", slow_download):
            with media_app.app.test_request_context("/"):
                response = media_app._stream_progress_download({"url": "https://example.com", "kind": "video"})
                frames = iter(response.response)
                next(frames)
                next(frames)
                response.response.close()

            deadline = time.monotonic() + 5
            while any(Path(work_dir).iterdir()) and time.monotonic() < deadline:
                time.sleep(0.05)
            self.assertIn("folder", created)
            self.assertEqual(list(Path(work_dir).iterdir()), [])

    def test_quiet_progress_stream_repeats_last_status(self):
        def slow_download(payload, folder, report=None, cancelled=None):
            report({"percent": 92, "phase": "Menyesuaikan kompatibilitas video"})
            time.sleep(0.25)
            result = folder / "video.mp4"
            result.write_bytes(b"data")
            return result

        with tempfile.TemporaryDirectory() as work_dir, \
                patch.object(media_app, "WORK_DIR", Path(work_dir)), \
                patch.object(media_app, "PROGRESS_HEARTBEAT_SECONDS", 0.05), \
                patch.object(media_app, "_download_with_retry", slow_download):
            with media_app.app.test_request_context("/"):
                response = media_app._stream_progress_download({"url": "https://example.com", "kind": "video"})
                frames = parse_progress_frames(b"".join(response.response))

        repeated = [json.loads(payload) for kind, payload in frames if kind == b"P"]
        self.assertGreater(sum(1 for event in repeated if event["percent"] == 92), 1)
        self.assertEqual(frames[-1][0], b"C")

    def test_ffmpeg_command_stops_when_download_is_cancelled(self):
        cancelled = threading.Event()
        threading.Timer(0.2, cancelled.set).start()
        started = time.monotonic()
        with self.assertRaisesRegex(media_app.UserFacingError, "dibatalkan"):
            media_app._run_media_command([sys.executable, "-c", "import time; time.sleep(30)"], cancelled)
        self.assertLess(time.monotonic() - started, 5)

    def test_ffmpeg_command_stops_after_timeout(self):
        with patch.object(media_app, "FFMPEG_TIMEOUT", 0):
            with self.assertRaisesRegex(media_app.UserFacingError, "batas waktu"):
                media_app._run_media_command([sys.executable, "-c", "import time; time.sleep(30)"])

    def test_ffmpeg_command_failure_is_reported(self):
        with self.assertRaises(media_app.subprocess.CalledProcessError):
            media_app._run_media_command([sys.executable, "-c", "raise SystemExit(3)"])


class AccessControlTests(unittest.TestCase):
    def setUp(self):
        media_app.app.config.update(TESTING=True)
        media_app._rate_limiter.reset()
        self.addCleanup(media_app._rate_limiter.reset)
        self.client = media_app.app.test_client()

    def test_batch_rejects_non_numeric_package_number(self):
        with patch.object(media_app, "_decode_download_token", return_value={"url": "https://example.com", "kind": "video"}):
            response = self.client.post("/api/batch/download", json={"tokens": ["token"], "package": "abc"})
        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.get_json()["error"], "Nomor paket tidak valid.")

    def test_rate_limit_parser(self):
        self.assertEqual(media_app._parse_rate_limit("30/60"), (30, 60))
        self.assertEqual(media_app._parse_rate_limit("5"), (5, 60))
        self.assertIsNone(media_app._parse_rate_limit("off"))
        self.assertIsNone(media_app._parse_rate_limit("0"))
        with self.assertRaises(RuntimeError):
            media_app._parse_rate_limit("banyak")

    @patch("app._validate_public_url", side_effect=media_app.UserFacingError("Link tidak valid."))
    def test_info_requests_are_rate_limited_per_client(self, _validate):
        with patch.dict(media_app.RATE_LIMITS, {"info": (2, 60)}):
            statuses = [self.client.post("/api/info", json={"url": "x"}).status_code for _ in range(3)]
            limited = self.client.post("/api/info", json={"url": "x"})
        self.assertEqual(statuses, [422, 422, 429])
        self.assertIn("Retry-After", limited.headers)
        self.assertIn("Terlalu banyak permintaan", limited.get_json()["error"])

    @patch("app._validate_public_url", side_effect=media_app.UserFacingError("Link tidak valid."))
    def test_trusted_proxy_header_separates_clients(self, _validate):
        with patch.dict(media_app.RATE_LIMITS, {"info": (1, 60)}), \
                patch.object(media_app, "TRUSTED_PROXY_HEADER", "CF-Connecting-IP"):
            first = self.client.post("/api/info", json={"url": "x"}, headers={"CF-Connecting-IP": "203.0.113.1"})
            second = self.client.post("/api/info", json={"url": "x"}, headers={"CF-Connecting-IP": "203.0.113.2"})
            repeated = self.client.post("/api/info", json={"url": "x"}, headers={"CF-Connecting-IP": "203.0.113.1"})
        self.assertEqual([first.status_code, second.status_code, repeated.status_code], [422, 422, 429])

    def test_forwarded_header_uses_entry_added_by_trusted_proxy(self):
        with patch.object(media_app, "TRUSTED_PROXY_HEADER", "X-Forwarded-For"), \
                media_app.app.test_request_context("/", headers={"X-Forwarded-For": "1.1.1.1, 203.0.113.9"}):
            self.assertEqual(media_app._client_address(), "203.0.113.9")

    def test_password_protects_pages_but_not_health_check(self):
        with patch.object(media_app, "ACCESS_PASSWORD", "rahasia"):
            anonymous = self.client.get("/")
            health = self.client.get("/api/health")
            signed_in = self.client.get("/", headers=basic_auth("linkdrop", "rahasia"))
            wrong_user = self.client.get("/", headers=basic_auth("admin", "rahasia"))
        self.assertEqual(anonymous.status_code, 401)
        self.assertIn("Basic", anonymous.headers["WWW-Authenticate"])
        self.assertEqual(health.status_code, 200)
        self.assertEqual(signed_in.status_code, 200)
        self.assertEqual(wrong_user.status_code, 401)

    def test_repeated_wrong_passwords_lock_out_even_the_right_one(self):
        with patch.object(media_app, "ACCESS_PASSWORD", "rahasia"):
            for _ in range(media_app.FAILED_LOGIN_LIMIT[0]):
                self.assertEqual(self.client.get("/", headers=basic_auth("linkdrop", "salah")).status_code, 401)
            locked = self.client.get("/", headers=basic_auth("linkdrop", "rahasia"))
        self.assertEqual(locked.status_code, 429)
        self.assertIn("Retry-After", locked.headers)

    def test_no_password_keeps_app_open(self):
        self.assertEqual(self.client.get("/").status_code, 200)


AUTO_JSON3 = {
    "wireMagic": "pb3",
    "events": [
        {"tStartMs": 0, "dDurationMs": 6000, "id": 1, "wpWinPosId": 1, "wsWinStyleId": 1},
        {"tStartMs": 200, "dDurationMs": 3800, "wWinId": 1, "segs": [{"utf8": "halo"}, {"utf8": " semuanya", "tOffsetMs": 300}]},
        {"tStartMs": 2000, "dDurationMs": 2000, "wWinId": 1, "aAppend": 1, "segs": [{"utf8": "\n"}]},
        {"tStartMs": 2000, "dDurationMs": 4000, "wWinId": 1, "segs": [{"utf8": "baris kedua"}]},
    ],
}

SUBTITLE_INFO = {
    **SAMPLE_INFO,
    "subtitles": {
        "en-US": [{"ext": "json3", "url": "https://www.youtube.com/api/timedtext?lang=en"}],
        "live_chat": [{"ext": "json", "url": "https://www.youtube.com/live_chat"}],
    },
    "automatic_captions": {
        "id-orig": [{"ext": "json3", "url": "https://www.youtube.com/api/timedtext?lang=id"}],
        "id": [{"ext": "json3", "url": "https://www.youtube.com/api/timedtext?lang=id"}],
        "en": [{"ext": "json3", "url": "https://www.youtube.com/api/timedtext?tlang=en"}],
        "fr": [{"ext": "json3", "url": "https://www.youtube.com/api/timedtext?tlang=fr"}],
    },
}


class SubtitleTests(unittest.TestCase):
    def setUp(self):
        media_app.app.config.update(TESTING=True)
        media_app._rate_limiter.reset()
        self.client = media_app.app.test_client()
        self.url = "https://www.youtube.com/watch?v=abc123"

    def test_offers_only_indonesian_and_english_tracks_that_exist(self):
        subtitles = media_app._subtitle_choices(self.url, SUBTITLE_INFO)
        self.assertEqual(
            [(item["language"], item["automatic"]) for item in subtitles],
            [("id", True), ("en", False)],
        )
        english = media_app._signer.loads(subtitles[1]["token"])
        self.assertEqual((english["kind"], english["lang"], english["automatic"]), ("subtitle", "en-US", False))
        indonesian = media_app._signer.loads(subtitles[0]["token"])
        self.assertEqual(indonesian["lang"], "id-orig")

    def test_machine_translated_captions_are_not_offered(self):
        info = {**SAMPLE_INFO, "automatic_captions": {"id": [{"ext": "json3"}], "en": [{"ext": "json3"}]}}
        self.assertEqual(media_app._subtitle_choices(self.url, info), [])

    def test_video_without_subtitles_has_no_subtitle_choices(self):
        payload = media_app._media_payload(self.url, SAMPLE_INFO)
        self.assertEqual(payload["subtitles"], [])

    def test_gallery_has_no_subtitle_choices(self):
        info = {**SUBTITLE_INFO, "entries": [{"ext": "jpg", "url": "https://cdn.example.com/1.jpg"}, {"ext": "jpg"}]}
        self.assertEqual(media_app._media_payload(self.url, info)["subtitles"], [])

    @patch("app._validate_public_url", side_effect=lambda value: value)
    @patch("app._download_with_retry")
    def test_download_endpoint_attaches_selected_subtitle(self, download_mock, _validate):
        captured = {}

        def fake_download(payload, folder, progress=None, cancelled=None):
            captured.update(payload)
            result = folder / "video.mp4"
            result.write_bytes(b"video")
            return result

        download_mock.side_effect = fake_download
        video_token = media_app._make_token(self.url, "video", height=720)
        subtitle_token = media_app._make_token(self.url, "subtitle", lang="id-orig", automatic=True)
        response = self.client.get(f"/api/download/{video_token}?subtitle={subtitle_token}")
        self.assertEqual(response.status_code, 200)
        b"".join(response.response)
        self.assertEqual(captured["subtitle"], {"lang": "id-orig", "automatic": True, "burn": False})

    @patch("app._validate_public_url", side_effect=lambda value: value)
    @patch("app._download_with_retry")
    def test_download_endpoint_accepts_burned_subtitle_mode(self, download_mock, _validate):
        captured = {}

        def fake_download(payload, folder, progress=None, cancelled=None):
            captured.update(payload)
            result = folder / "video.mp4"
            result.write_bytes(b"video")
            return result

        download_mock.side_effect = fake_download
        video_token = media_app._make_token(self.url, "video", height=720)
        subtitle_token = media_app._make_token(self.url, "subtitle", lang="en", automatic=False)
        response = self.client.get(f"/api/download/{video_token}?subtitle={subtitle_token}&subtitle_mode=burn")
        b"".join(response.response)
        self.assertTrue(captured["subtitle"]["burn"])

    @patch("app._validate_public_url", side_effect=lambda value: value)
    def test_subtitle_token_must_match_the_video(self, _validate):
        video_token = media_app._make_token(self.url, "video", height=720)
        other = media_app._make_token("https://www.youtube.com/watch?v=other", "subtitle", lang="en", automatic=False)
        response = self.client.get(f"/api/download/{video_token}?subtitle={other}")
        self.assertEqual(response.status_code, 422)
        self.assertIn("tidak cocok", response.get_json()["error"])

    @patch("app._validate_public_url", side_effect=lambda value: value)
    def test_subtitle_is_rejected_for_audio_and_tampering(self, _validate):
        audio_token = media_app._make_token(self.url, "audio", codec="mp3", bitrate=128)
        subtitle_token = media_app._make_token(self.url, "subtitle", lang="en", automatic=False)
        audio = self.client.get(f"/api/download/{audio_token}?subtitle={subtitle_token}")
        self.assertEqual(audio.status_code, 422)
        video_token = media_app._make_token(self.url, "video", height=720)
        tampered = self.client.get(f"/api/download/{video_token}?subtitle={subtitle_token}x")
        self.assertEqual(tampered.status_code, 400)

    def test_automatic_json3_becomes_clean_single_line_srt(self):
        cues = media_app._json3_cues(AUTO_JSON3, automatic=True)
        self.assertEqual(cues, [(200, 2000, "halo semuanya"), (2000, 6000, "baris kedua")])
        self.assertEqual(
            media_app._srt_from_cues(cues),
            "1\n00:00:00,200 --> 00:00:02,000\nhalo semuanya\n\n"
            "2\n00:00:02,000 --> 00:00:06,000\nbaris kedua\n",
        )

    def test_manual_json3_keeps_original_timing(self):
        cues = media_app._json3_cues(AUTO_JSON3, automatic=False)
        self.assertEqual(cues[0], (200, 4000, "halo semuanya"))

    def test_prepare_subtitle_converts_json3_and_ignores_video(self):
        with tempfile.TemporaryDirectory() as folder:
            folder = Path(folder)
            (folder / "Video [abc].mp4").write_bytes(b"video")
            (folder / "Video [abc].id-orig.json3").write_text(json.dumps(AUTO_JSON3), encoding="utf-8")
            self.assertEqual(media_app._find_downloaded_file(folder).name, "Video [abc].mp4")
            subtitle = media_app._prepare_subtitle(folder, {"lang": "id-orig", "automatic": True})
            self.assertEqual(subtitle.name, "linkdrop-subtitle.srt")
            self.assertIn("halo semuanya", subtitle.read_text(encoding="utf-8"))

    def test_prepare_subtitle_reports_missing_track(self):
        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaisesRegex(media_app.UserFacingError, "tidak tersedia"):
                media_app._prepare_subtitle(Path(folder), {"lang": "en", "automatic": False})

    def test_failed_subtitle_download_is_actionable(self):
        message = media_app._friendly_error(Exception(
            "ERROR: Unable to download video subtitles for 'en': HTTP Error 429: Too Many Requests"
        ))
        self.assertIn("Subtitle gagal diambil", message)

    def test_video_download_requests_and_burns_selected_subtitle(self):
        captured = {}

        class FakeYoutubeDL:
            def __init__(self, options):
                captured["options"] = options

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def download(self, urls):
                folder = Path(captured["options"]["outtmpl"]).parent
                (folder / "Video [abc].mp4").write_bytes(b"video")
                (folder / "Video [abc].en-US.json3").write_text(json.dumps(AUTO_JSON3), encoding="utf-8")

        def fake_burn(path, progress=None, cancelled=None, subtitle=None):
            captured["subtitle"] = subtitle.read_text(encoding="utf-8")
            return path

        payload = {"url": self.url, "kind": "video", "height": 720, "subtitle": {"lang": "en-US", "automatic": False, "burn": True}}
        with tempfile.TemporaryDirectory() as folder, \
                patch.object(media_app.yt_dlp, "YoutubeDL", FakeYoutubeDL), \
                patch.object(media_app, "_ensure_compatible_mp4", fake_burn):
            result = media_app._download_video_or_audio(payload, Path(folder))

        options = captured["options"]
        self.assertTrue(options["writesubtitles"])
        self.assertFalse(options["writeautomaticsub"])
        self.assertEqual(options["subtitleslangs"], ["en\\-US"])
        self.assertEqual(result.name, "Video [abc].mp4")
        self.assertIn("halo semuanya", captured["subtitle"])

    @unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "FFmpeg is required")
    def test_subtitle_is_burned_into_the_picture(self):
        def bottom_brightness(video: Path) -> int:
            frame = subprocess.run(
                ["ffmpeg", "-v", "error", "-ss", "1", "-i", str(video), "-frames:v", "1",
                 "-vf", "crop=320:60:0:180", "-f", "rawvideo", "-pix_fmt", "gray", "-"],
                check=True, capture_output=True,
            ).stdout
            return max(frame)

        with tempfile.TemporaryDirectory() as folder:
            folder = Path(folder)
            video = folder / "Video [abc].mp4"
            subprocess.run(
                ["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "color=black:size=320x240:rate=10:duration=3",
                 "-c:v", "libx264", "-pix_fmt", "yuv420p", str(video)],
                check=True,
            )
            self.assertLess(bottom_brightness(video), 40)
            (folder / "linkdrop-subtitle.srt").write_text(
                "1\n00:00:00,000 --> 00:00:03,000\nHALO SEMUANYA\n", encoding="utf-8"
            )
            events = []
            result = media_app._ensure_compatible_mp4(video, events.append, None, folder / "linkdrop-subtitle.srt")

            self.assertEqual(result, video)
            self.assertGreater(bottom_brightness(result), 200)
            self.assertFalse((folder / "Video [abc].linkdrop.mp4").exists())
            self.assertEqual(events[0]["phase"], "Menempelkan subtitle ke video")


class FakeYoutubeDL:
    """Records yt-dlp options and writes the files a download would create."""

    created_files = ()
    last_options = None

    def __init__(self, options):
        FakeYoutubeDL.last_options = options

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def download(self, urls):
        folder = Path(FakeYoutubeDL.last_options["outtmpl"]).parent
        for name, content in FakeYoutubeDL.created_files:
            (folder / name).write_bytes(content)


def video_packets_md5(path):
    return subprocess.run(
        ["ffmpeg", "-v", "error", "-i", str(path), "-map", "0:v", "-c", "copy", "-f", "md5", "-"],
        check=True, capture_output=True, text=True,
    ).stdout.strip()


class OriginalQualityTests(unittest.TestCase):
    url = "https://www.youtube.com/watch?v=abc123"

    def test_resolutions_without_h264_offer_original_and_mp4_copies(self):
        info = {
            **SAMPLE_INFO,
            "formats": SAMPLE_INFO["formats"] + [
                {"format_id": "313", "height": 2160, "vcodec": "vp9", "acodec": "none", "filesize": 90_000_000, "ext": "webm"},
            ],
        }
        video = media_app._build_choices(self.url, info)["video"]
        self.assertEqual([choice["label"] for choice in video], ["360p", "1080p", "2160p", "2160p MP4"])
        self.assertIn("MP4 · kualitas asli", video[1]["detail"])
        self.assertIn("tanpa konversi", video[2]["detail"])
        self.assertIn("dikonversi ulang", video[3]["detail"])
        self.assertEqual(media_app._signer.loads(video[2]["token"])["container"], "original")
        self.assertNotIn("container", media_app._signer.loads(video[3]["token"]))
        self.assertEqual(len({choice["id"] for choice in video}), 4)

    def test_best_original_audio_and_m4a_are_offered_without_conversion(self):
        info = {
            **SAMPLE_INFO,
            "formats": SAMPLE_INFO["formats"] + [
                {"format_id": "251", "vcodec": "none", "acodec": "opus", "abr": 160, "ext": "webm", "filesize": 2_500_000},
            ],
        }
        audio = media_app._build_choices(self.url, info)["audio"]
        self.assertEqual([choice["label"] for choice in audio][:2], ["Audio asli", "Audio asli M4A"])
        self.assertTrue(audio[0]["detail"].startswith("Opus (WebM) · 160 kbps · tanpa konversi"))
        self.assertEqual(media_app._signer.loads(audio[0]["token"])["format_id"], "251")
        self.assertEqual(media_app._signer.loads(audio[1]["token"])["format_id"], "140")

    def test_original_video_download_skips_conversion(self):
        FakeYoutubeDL.created_files = [("Video [abc].webm", b"webm")]
        payload = {"url": self.url, "kind": "video", "height": 2160, "container": "original"}
        with tempfile.TemporaryDirectory() as folder, \
                patch.object(media_app.yt_dlp, "YoutubeDL", FakeYoutubeDL), \
                patch.object(media_app, "_ensure_compatible_mp4") as convert:
            result = media_app._download_video_or_audio(payload, Path(folder))
        convert.assert_not_called()
        self.assertEqual(result.name, "Video [abc].webm")
        self.assertEqual(FakeYoutubeDL.last_options["merge_output_format"], "webm/mkv")
        self.assertTrue(FakeYoutubeDL.last_options["format"].startswith("bv*[height=2160]+ba"))

    def test_original_audio_download_uses_signed_format_without_postprocessing(self):
        FakeYoutubeDL.created_files = [("Audio [abc].webm", b"opus")]
        payload = {"url": self.url, "kind": "audio", "codec": "source", "format_id": "251"}
        with tempfile.TemporaryDirectory() as folder, patch.object(media_app.yt_dlp, "YoutubeDL", FakeYoutubeDL):
            media_app._download_video_or_audio(payload, Path(folder))
        self.assertEqual(FakeYoutubeDL.last_options["format"], "251/bestaudio/best")
        self.assertNotIn("postprocessors", FakeYoutubeDL.last_options)

    def test_unsafe_format_id_falls_back_to_best_audio(self):
        FakeYoutubeDL.created_files = [("Audio [abc].m4a", b"aac")]
        payload = {"url": self.url, "kind": "audio", "codec": "source", "format_id": "251+bv*"}
        with tempfile.TemporaryDirectory() as folder, patch.object(media_app.yt_dlp, "YoutubeDL", FakeYoutubeDL):
            media_app._download_video_or_audio(payload, Path(folder))
        self.assertEqual(FakeYoutubeDL.last_options["format"], "bestaudio/best")

    def test_selectable_subtitle_is_added_without_burning(self):
        FakeYoutubeDL.created_files = [
            ("Video [abc].mp4", b"video"),
            ("Video [abc].id-orig.json3", json.dumps(AUTO_JSON3).encode()),
        ]
        calls = {}

        def fake_compatible(path, progress=None, cancelled=None, subtitle=None):
            calls["compatible_subtitle"] = subtitle
            return path

        def fake_embed(path, subtitle, language, progress=None, cancelled=None):
            calls["embed"] = (subtitle.name, language)
            return path

        payload = {"url": self.url, "kind": "video", "height": 720,
                   "subtitle": {"lang": "id-orig", "automatic": True, "burn": False}}
        with tempfile.TemporaryDirectory() as folder, \
                patch.object(media_app.yt_dlp, "YoutubeDL", FakeYoutubeDL), \
                patch.object(media_app, "_ensure_compatible_mp4", fake_compatible), \
                patch.object(media_app, "_embed_subtitle_track", fake_embed):
            media_app._download_video_or_audio(payload, Path(folder))
        self.assertIsNone(calls["compatible_subtitle"])
        self.assertEqual(calls["embed"], ("linkdrop-subtitle.srt", "id-orig"))

    @unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "FFmpeg is required")
    def test_embedded_subtitle_keeps_video_bit_identical(self):
        with tempfile.TemporaryDirectory() as folder:
            folder = Path(folder)
            video = folder / "-Judul: Video [abc].mp4"
            subprocess.run(
                ["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "testsrc2=size=320x240:rate=10:duration=2",
                 "-f", "lavfi", "-i", "sine=duration=2", "-c:v", "libx264", "-pix_fmt", "yuv420p",
                 "-c:a", "aac", "-shortest", f"./{video.name}"],
                check=True, cwd=folder,
            )
            before = video_packets_md5(video)
            subtitle = folder / "linkdrop-subtitle.srt"
            subtitle.write_text("1\n00:00:00,000 --> 00:00:02,000\nHalo\n", encoding="utf-8")

            result = media_app._embed_subtitle_track(video, subtitle, "id-orig")

            self.assertEqual(result, video)
            self.assertEqual(video_packets_md5(result), before)
            probe = subprocess.run(
                ["ffprobe", "-v", "error", "-select_streams", "s",
                 "-show_entries", "stream=codec_name:stream_tags=language:stream_disposition=default",
                 "-of", "json", str(result)],
                check=True, capture_output=True, text=True,
            )
            stream = json.loads(probe.stdout)["streams"][0]
            self.assertEqual(stream["codec_name"], "mov_text")
            self.assertEqual(stream["tags"]["language"], "ind")
            self.assertEqual(stream["disposition"]["default"], 1)


class ParallelDownloadTests(unittest.TestCase):
    def test_byte_budget_is_shared(self):
        budget = media_app._ByteBudget(10)
        budget.take(6)
        self.assertEqual(budget.remaining(), 4)
        with self.assertRaisesRegex(media_app.UserFacingError, "batas"):
            budget.take(5)

    def test_gallery_items_download_in_parallel_and_keep_order(self):
        info = {"title": "Carousel", "entries": [
            {"ext": "jpg", "url": f"https://cdn.example.com/{index}.jpg"} for index in range(1, 7)
        ]}
        active = {"now": 0, "peak": 0}
        lock = threading.Lock()

        def fake_remote(source, destination, budget, progress=None, cancelled=None):
            with lock:
                active["now"] += 1
                active["peak"] = max(active["peak"], active["now"])
            time.sleep(0.2)
            destination.write_bytes(source[0].encode())
            budget.take(10)
            with lock:
                active["now"] -= 1
            return 10

        events = []
        started = time.monotonic()
        with tempfile.TemporaryDirectory() as folder, \
                patch.object(media_app, "_extract_info", return_value=info), \
                patch.object(media_app, "_download_remote", fake_remote):
            title, paths = media_app._download_gallery_items(
                {"url": "https://example.com/post"}, Path(folder), 1000, events.append
            )
            contents = [path.read_text() for path in paths]
        elapsed = time.monotonic() - started

        self.assertEqual(title, "Carousel")
        self.assertEqual([path.name for path in paths], [f"{index:02d}.jpg" for index in range(1, 7)])
        self.assertEqual(contents, [f"https://cdn.example.com/{index}.jpg" for index in range(1, 7)])
        self.assertEqual(active["peak"], media_app.GALLERY_CONCURRENCY)
        self.assertLess(elapsed, 0.2 * 6 * 0.75)
        self.assertEqual(events[-1]["detail"].split(" ")[0], "6")

    def test_gallery_failure_stops_remaining_items(self):
        info = {"entries": [{"ext": "jpg", "url": f"https://cdn.example.com/{index}.jpg"} for index in range(1, 9)]}
        started = []

        def fake_remote(source, destination, budget, progress=None, cancelled=None):
            started.append(source[0])
            if source[0].endswith("/1.jpg"):
                raise media_app.UserFacingError("Sumber menolak akses.")
            for _ in range(20):
                if cancelled.is_set():
                    raise media_app.UserFacingError("Unduhan dibatalkan.")
                time.sleep(0.01)
            destination.write_bytes(b"x")
            return 1

        with tempfile.TemporaryDirectory() as folder, \
                patch.object(media_app, "_extract_info", return_value=info), \
                patch.object(media_app, "_download_remote", fake_remote):
            with self.assertRaisesRegex(media_app.UserFacingError, "menolak"):
                media_app._download_gallery_items({"url": "https://example.com/post"}, Path(folder), 1000)
        self.assertLess(len(started), 8)

    @patch("app._validate_public_url", side_effect=lambda value: value)
    @patch("app._download_with_retry")
    def test_batch_items_run_in_parallel(self, download_mock, _validate):
        def slow_download(payload, folder, progress, cancelled):
            time.sleep(0.3)
            result = folder / f"{payload['url'].rsplit('/', 1)[-1]}.mp4"
            result.write_bytes(b"media")
            return result

        download_mock.side_effect = slow_download
        tokens = [
            media_app._make_token(f"https://example.com/video{index}", "video", height=720, estimated_bytes=10)
            for index in range(1, 5)
        ]
        client = media_app.app.test_client()
        media_app._rate_limiter.reset()
        started = time.monotonic()
        response = client.post("/api/batch/download", json={"tokens": tokens})
        frames = parse_progress_frames(response.data)
        elapsed = time.monotonic() - started

        archive_bytes = b"".join(payload for kind, payload in frames if kind == b"D")
        with zipfile.ZipFile(io.BytesIO(archive_bytes)) as bundle:
            self.assertEqual(sorted(bundle.namelist()), [f"video{index}.mp4" for index in range(1, 5)])
        self.assertLess(elapsed, 0.3 * 4 * 0.8)


if __name__ == "__main__":
    unittest.main()
