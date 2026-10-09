import json
import struct
import tempfile
import unittest
from pathlib import Path
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
            "vcodec": "none",
            "acodec": "aac",
            "filesize": 2_000_000,
            "abr": 128,
            "url": "https://cdn.example.com/audio.m4a",
        },
    ],
}


class AppTests(unittest.TestCase):
    def setUp(self):
        media_app.app.config.update(TESTING=True)
        self.client = media_app.app.test_client()

    def test_health_endpoint(self):
        response = self.client.get("/api/health")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.get_json()["ok"])

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
        self.assertEqual(len(payload["choices"]["audio"]), 4)
        self.assertTrue(payload["choices"]["video"][0]["download_url"].startswith("/api/download/"))

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
        def make_result(_payload, folder):
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

        frames = []
        body = response.data
        offset = 0
        while offset < len(body):
            frame_type, frame_length = struct.unpack(">cI", body[offset:offset + 5])
            offset += 5
            payload = body[offset:offset + frame_length]
            offset += frame_length
            frames.append((frame_type, payload))

        self.assertEqual([frame[0] for frame in frames], [b"P", b"P", b"M", b"D", b"C"])
        metadata = json.loads(frames[2][1])
        self.assertEqual(metadata["filename"], "sample.mp4")
        self.assertEqual(metadata["size"], len(b"media-bytes"))
        self.assertEqual(frames[3][1], b"media-bytes")

    def test_youtube_reload_error_is_actionable(self):
        message = media_app._friendly_error(Exception("The page needs to be reloaded."))
        self.assertIn("analisis ulang", message.lower())

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


if __name__ == "__main__":
    unittest.main()
