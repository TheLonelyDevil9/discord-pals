import base64
import types
import unittest
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, patch

import module_stubs  # noqa: F401
import image_attachments
import runtime_config

from test_support import MemorySandboxMixin

MB = 1024 * 1024


class FakeStream:
    def __init__(self, chunks):
        self.chunks = list(chunks)
        self.bytes_read = 0

    async def iter_chunked(self, size):
        for chunk in self.chunks:
            self.bytes_read += len(chunk)
            yield chunk


class FakeResponse:
    def __init__(self, body=b"", *, chunks=None, status=200, content_length=None):
        self.status = status
        self.content_length = content_length
        self.content = FakeStream(chunks if chunks is not None else [body])


class FakeSession:
    def __init__(self, responses):
        self.responses = responses
        self.requested = []

    def get(self, url):
        self.requested.append(url)
        response = self.responses[url]

        @asynccontextmanager
        async def request():
            yield response

        return request()


def image(name, *, size=1000):
    return types.SimpleNamespace(filename=name, size=size, url=f"https://cdn.test/{name}")


class ImageAttachmentLimitTests(unittest.IsolatedAsyncioTestCase):
    async def _process(self, attachments, responses, *, max_mb=8, max_count=4, content="look at this"):
        session = FakeSession(responses)
        settings = {"image_attachment_max_mb": max_mb, "image_attachment_max_count": max_count}
        message = types.SimpleNamespace(content=content, attachments=attachments)
        with patch.object(image_attachments, "get_http_session", AsyncMock(return_value=session)), \
                patch.object(image_attachments.runtime_config, "get",
                             side_effect=lambda key, default=None: settings.get(key, default)), \
                patch.object(image_attachments.log, "warn") as warn:
            parts = await image_attachments.process_attachments(message, bot_name="Nahida")
        return parts, session, warn

    @staticmethod
    def _image_urls(parts):
        return [part["image_url"]["url"] for part in parts or [] if part.get("type") == "image_url"]

    async def test_a_message_with_a_few_ordinary_images_is_sent_as_before(self):
        first, second = b"first image bytes", b"second image bytes"
        parts, session, warn = await self._process(
            [image("cat.png"), image("dog.JPG"), types.SimpleNamespace(filename="notes.txt", size=10, url="https://cdn.test/notes.txt")],
            {"https://cdn.test/cat.png": FakeResponse(first), "https://cdn.test/dog.JPG": FakeResponse(second)},
        )

        self.assertEqual(parts, [
            {"type": "text", "text": "look at this"},
            {"type": "image_url", "image_url": {"url": "data:image/png;base64," + base64.b64encode(first).decode()}},
            {"type": "image_url", "image_url": {"url": "data:image/JPG;base64," + base64.b64encode(second).decode()}},
        ])
        self.assertEqual(session.requested, ["https://cdn.test/cat.png", "https://cdn.test/dog.JPG"])
        warn.assert_not_called()

    async def test_an_image_declared_over_the_size_limit_is_skipped_without_a_download(self):
        parts, session, warn = await self._process(
            [image("huge.png", size=2 * MB), image("small.png")],
            {"https://cdn.test/small.png": FakeResponse(b"small")},
            max_mb=1,
        )

        self.assertEqual(session.requested, ["https://cdn.test/small.png"])
        self.assertEqual(len(self._image_urls(parts)), 1)
        self.assertEqual(parts[0], {"type": "text", "text": "look at this"})
        warn.assert_called_once()
        self.assertIn("huge.png", warn.call_args.args[0])
        self.assertIn("1 MB", warn.call_args.args[0])
        self.assertEqual(warn.call_args.kwargs["reason"], "size_limit")

    async def test_a_download_stops_once_it_passes_the_size_limit(self):
        stream_chunks = [b"x" * (256 * 1024)] * 40
        oversized = FakeResponse(chunks=stream_chunks)
        parts, session, warn = await self._process(
            [image("sticker.webp", size=0), image("ok.gif")],
            {"https://cdn.test/sticker.webp": oversized, "https://cdn.test/ok.gif": FakeResponse(b"gif")},
            max_mb=1,
        )

        self.assertLessEqual(oversized.content.bytes_read, MB + 256 * 1024)
        self.assertEqual(self._image_urls(parts), ["data:image/gif;base64," + base64.b64encode(b"gif").decode()])
        warn.assert_called_once()
        self.assertIn("sticker.webp", warn.call_args.args[0])
        self.assertEqual(warn.call_args.kwargs["reason"], "size_limit")

    async def test_a_response_that_declares_a_length_over_the_limit_is_not_read(self):
        declared_large = FakeResponse(b"x" * 10, content_length=3 * MB)
        parts, _session, warn = await self._process(
            [image("lying.png", size=100)],
            {"https://cdn.test/lying.png": declared_large},
            max_mb=1,
            content="",
        )

        self.assertEqual(declared_large.content.bytes_read, 0)
        self.assertIsNone(parts)
        warn.assert_called_once()
        self.assertEqual(warn.call_args.kwargs["reason"], "size_limit")

    async def test_images_past_the_count_limit_are_skipped_with_one_line_each(self):
        names = [f"img{index}.png" for index in range(10)]
        parts, session, warn = await self._process(
            [image(name) for name in names],
            {f"https://cdn.test/{name}": FakeResponse(name.encode()) for name in names},
        )

        self.assertEqual(session.requested, [f"https://cdn.test/{name}" for name in names[:4]])
        self.assertEqual(
            self._image_urls(parts),
            ["data:image/png;base64," + base64.b64encode(name.encode()).decode() for name in names[:4]],
        )
        self.assertEqual(warn.call_count, 6)
        for call, name in zip(warn.call_args_list, names[4:]):
            self.assertIn(name, call.args[0])
            self.assertIn("4 images", call.args[0])
            self.assertEqual(call.kwargs["reason"], "count_limit")

    async def test_a_skipped_oversized_image_does_not_take_a_place_in_the_count(self):
        names = ["big.png", "a.png", "b.png", "c.png", "d.png"]
        attachments = [image("big.png", size=9 * MB)] + [image(name) for name in names[1:]]
        parts, _session, warn = await self._process(
            attachments,
            {f"https://cdn.test/{name}": FakeResponse(name.encode()) for name in names[1:]},
        )

        self.assertEqual(len(self._image_urls(parts)), 4)
        warn.assert_called_once()
        self.assertEqual(warn.call_args.kwargs["reason"], "size_limit")

    async def test_a_count_limit_of_zero_sends_no_images(self):
        parts, session, warn = await self._process(
            [image("a.png"), image("b.png")],
            {},
            max_count=0,
        )

        self.assertIsNone(parts)
        self.assertEqual(session.requested, [])
        self.assertEqual(warn.call_count, 2)

    async def test_a_failed_download_is_skipped_and_the_other_images_stay(self):
        parts, _session, warn = await self._process(
            [image("gone.png"), image("here.png")],
            {"https://cdn.test/gone.png": FakeResponse(status=404), "https://cdn.test/here.png": FakeResponse(b"here")},
        )

        self.assertEqual(self._image_urls(parts), ["data:image/png;base64," + base64.b64encode(b"here").decode()])
        warn.assert_called_once()
        self.assertIn("gone.png", warn.call_args.args[0])
        self.assertEqual(warn.call_args.kwargs["reason"], "download_failed")

    async def test_an_image_without_text_still_gets_the_text_part_providers_need(self):
        parts, _session, _warn = await self._process(
            [image("only.png")],
            {"https://cdn.test/only.png": FakeResponse(b"only")},
            content="",
        )

        self.assertEqual(parts[0], {"type": "text", "text": "(user sent an image)"})
        self.assertEqual(len(self._image_urls(parts)), 1)


class ImageLimitSettingTests(MemorySandboxMixin, unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.setUpMemorySandbox()
        self.client = self.make_client()
        self._runtime_originals = (runtime_config.DATA_DIR, runtime_config.RUNTIME_CONFIG_FILE)
        runtime_config.DATA_DIR = str(self.data_dir)
        runtime_config.RUNTIME_CONFIG_FILE = str(self.data_dir / "runtime_config.json")
        runtime_config.invalidate_cache()

    def tearDown(self):
        runtime_config.DATA_DIR, runtime_config.RUNTIME_CONFIG_FILE = self._runtime_originals
        runtime_config.invalidate_cache()
        self.tearDownMemorySandbox()

    def _saved_value(self, key, raw):
        response = self.client.post("/api/config", json={key: raw}, headers=self.csrf_headers())
        self.assertEqual(response.status_code, 200)
        return self.client.get("/api/config").get_json()[key]

    async def test_defaults_keep_ordinary_messages_as_they_were(self):
        config = self.client.get("/api/config").get_json()

        self.assertEqual(config["image_attachment_max_mb"], 8)
        self.assertEqual(config["image_attachment_max_count"], 4)

    async def test_the_limits_are_edited_on_the_config_page_within_bounds(self):
        page = self.client.get("/config").get_data(as_text=True)

        self.assertIn('id="image_attachment_max_mb"', page)
        self.assertIn('id="image_attachment_max_count"', page)
        self.assertIn("image_attachment_max_mb: parseInt(document.getElementById('image_attachment_max_mb').value)", page)
        self.assertIn("image_attachment_max_count: parseInt(document.getElementById('image_attachment_max_count').value)", page)
        for raw, expected in ((0, 1), (-5, 1), (999, 20), ("not a number", 8), ("3", 3)):
            self.assertEqual(self._saved_value("image_attachment_max_mb", raw), expected)
        for raw, expected in ((-1, 0), (99, 10), ("not a number", 4), (0, 0), (2, 2)):
            self.assertEqual(self._saved_value("image_attachment_max_count", raw), expected)

    async def test_saved_limits_apply_to_the_next_message(self):
        self._saved_value("image_attachment_max_mb", 2)
        self._saved_value("image_attachment_max_count", 1)
        session = FakeSession({
            "https://cdn.test/first.png": FakeResponse(b"first"),
            "https://cdn.test/second.png": FakeResponse(b"second"),
        })
        message = types.SimpleNamespace(
            content="three pictures",
            attachments=[image("large.png", size=3 * MB), image("first.png"), image("second.png")],
        )

        with patch.object(image_attachments, "get_http_session", AsyncMock(return_value=session)), \
                patch.object(image_attachments.log, "warn") as warn:
            parts = await image_attachments.process_attachments(message)

        self.assertEqual(session.requested, ["https://cdn.test/first.png"])
        self.assertEqual(len([part for part in parts if part["type"] == "image_url"]), 1)
        self.assertEqual([call.kwargs["reason"] for call in warn.call_args_list], ["size_limit", "count_limit"])


if __name__ == "__main__":
    unittest.main()
