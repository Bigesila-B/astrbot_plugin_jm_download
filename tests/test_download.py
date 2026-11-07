"""Offline regressions for task-owned exports; no real comics or network calls."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
import importlib.util
import json
import logging
from pathlib import Path
import tempfile
import threading
import types
import unittest
from unittest.mock import patch


def load_plugin():
    modules = {name: types.ModuleType(name) for name in (
        "astrbot", "astrbot.api", "astrbot.api.event", "astrbot.api.star",
        "astrbot.api.message_components",
    )}
    api = modules["astrbot.api"]
    api.logger = logging.getLogger("jm-download-tests")
    api.AstrBotConfig = dict
    event = modules["astrbot.api.event"]
    event.AstrMessageEvent = object
    event.filter = types.SimpleNamespace(command=lambda *a: lambda fn: fn)
    star = modules["astrbot.api.star"]
    star.Context = object
    star.register = lambda *a: lambda cls: cls
    class Star:
        def __init__(self, context):
            pass
    star.Star = Star
    comp = modules["astrbot.api.message_components"]
    comp.Plain = lambda text: text
    comp.File = lambda **kwargs: kwargs
    api.message_components = comp
    spec = importlib.util.spec_from_file_location(
        "jm_download_under_test", Path(__file__).parents[1] / "main.py",
    )
    module = importlib.util.module_from_spec(spec)
    with patch.dict("sys.modules", modules):
        spec.loader.exec_module(module)
    return module


plugin_module = load_plugin()


class FakeEvent:
    message_str = "/jm 999"

    def __init__(self, fail_chain=False):
        self.fail_chain = fail_chain

    def plain_result(self, text):
        return text

    def chain_result(self, chain):
        if self.fail_chain:
            raise RuntimeError("simulated attachment failure")
        path = Path(chain[1]["file"])
        if not path.is_file():
            raise AssertionError("PDF removed before the platform could read it")
        return chain


class DownloadFixture:
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="jm-plugin-tests-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name) / "downloads # 中文"
        self.root.mkdir()
        self.plugin = plugin_module.JMDownloadPlugin(None, {
            "download_dir": str(self.root), "keep_files": False, "timeout": 2,
        })
        self.options = []
        self.fake = types.ModuleType("jmcomic")
        self.fake.create_option_by_file = self.create_option
        self.fake.Feature = types.SimpleNamespace(export_pdf=lambda **kwargs: kwargs)
        self.fake.download_album = self.export_pdf
        module_patch = patch.dict("sys.modules", {"jmcomic": self.fake})
        module_patch.start()
        self.addCleanup(module_patch.stop)

    def create_option(self, path):
        text = Path(path).read_text(encoding="utf-8")
        base_line = next(line for line in text.splitlines() if line.startswith("  base_dir: "))
        option = types.SimpleNamespace(
            base_dir=Path(json.loads(base_line.split(": ", 1)[1])), path=Path(path),
        )
        self.options.append(option)
        return option

    def export_pdf(self, comic_id, option, *, extra):
        directory = Path(extra["pdf_dir"])
        self.assertEqual(directory, option.base_dir / "pdf")
        self.assertTrue(extra["delete_original_file"])
        (directory / f"[JM{comic_id}] title.pdf").write_bytes(b"%PDF-offline-fixture")


class ExportTests(DownloadFixture, unittest.TestCase):
    def test_missing_export_does_not_select_unrelated_or_same_id_history(self):
        stale = self.root / "unrelated_111.pdf"
        same_id = self.root / "999_old.pdf"
        stale.write_bytes(b"unrelated")
        same_id.write_bytes(b"old request")
        self.fake.download_album = lambda *a, **kw: None
        with self.assertRaisesRegex(FileNotFoundError, "未生成 PDF"):
            self.plugin._download_comic("999")
        self.assertEqual(stale.read_bytes(), b"unrelated")
        self.assertEqual(same_id.read_bytes(), b"old request")
        self.assertEqual(set(self.root.iterdir()), {stale, same_id})

    def test_success_uses_only_its_export_and_cleans_option_file(self):
        stale = self.root / "999_newest.pdf"
        stale.write_bytes(b"history")
        path, title, task_dir = self.plugin._download_comic("999")
        self.assertEqual(Path(path).parent, Path(task_dir) / "pdf")
        self.assertEqual(title, "[JM999] title")
        self.assertEqual(Path(path).read_bytes(), b"%PDF-offline-fixture")
        self.assertFalse(self.options[0].path.exists())
        self.assertEqual(self.options[0].base_dir.parent, self.root)
        self.plugin._cleanup(task_dir)
        self.assertFalse(Path(task_dir).exists())
        self.assertEqual(stale.read_bytes(), b"history")

    def test_concurrent_requests_for_same_id_are_isolated(self):
        barrier = threading.Barrier(2)
        def export(comic_id, option, *, extra):
            barrier.wait(timeout=5)
            path = Path(extra["pdf_dir"]) / "same-title.pdf"
            path.write_text(str(option.base_dir), encoding="utf-8")
        self.fake.download_album = export
        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(self.plugin._download_comic, ["999", "999"]))
        first, second = results
        self.assertNotEqual(first[2], second[2])
        for path, _, task_dir in results:
            self.assertEqual(Path(path).read_text(encoding="utf-8"), task_dir)
        self.plugin._cleanup(first[2])
        self.assertTrue(Path(second[0]).is_file())
        self.plugin._cleanup(second[2])
        self.assertEqual(list(self.root.iterdir()), [])

    def test_failed_download_removes_only_its_partial_files(self):
        stale = self.root / "keep.pdf"
        stale.write_bytes(b"history")
        def fail(comic_id, option, *, extra):
            (option.base_dir / "partial.jpg").write_bytes(b"partial")
            raise RuntimeError("export failed")
        self.fake.download_album = fail
        with self.assertRaisesRegex(RuntimeError, "export failed"):
            self.plugin._download_comic("999")
        self.assertEqual(list(self.root.iterdir()), [stale])

    def test_ambiguous_exports_are_rejected_and_cleaned(self):
        def export(comic_id, option, *, extra):
            for name in ("first.pdf", "second.pdf"):
                (Path(extra["pdf_dir"]) / name).write_bytes(b"fixture")
        self.fake.download_album = export
        with self.assertRaisesRegex(RuntimeError, "多个 PDF"):
            self.plugin._download_comic("999")
        self.assertEqual(list(self.root.iterdir()), [])

    def test_cleanup_refuses_root_and_non_task_directory(self):
        unrelated = self.root / "user-files"
        unrelated.mkdir()
        with self.assertLogs("jm-download-tests", level="WARNING"):
            self.plugin._cleanup(self.root)
            self.plugin._cleanup(unrelated)
        self.assertTrue(self.root.is_dir())
        self.assertTrue(unrelated.is_dir())


class HandlerTests(DownloadFixture, unittest.IsolatedAsyncioTestCase):
    async def test_success_keeps_attachment_until_yield_finishes(self):
        handler = self.plugin.jm_download(FakeEvent())
        await anext(handler)  # starting
        await anext(handler)  # downloaded
        chain = await anext(handler)
        path = Path(chain[1]["file"])
        self.assertTrue(path.is_file())
        with self.assertRaises(StopAsyncIteration):
            await anext(handler)
        self.assertFalse(path.exists())
        self.assertEqual(list(self.root.iterdir()), [])

    async def test_keep_files_preserves_successful_task(self):
        self.plugin.config["keep_files"] = True
        results = [result async for result in self.plugin.jm_download(FakeEvent())]
        path = Path(results[-1][1]["file"])
        self.assertTrue(path.is_file())

    async def test_closed_handler_cleans_unsent_download(self):
        handler = self.plugin.jm_download(FakeEvent())
        await anext(handler)
        await anext(handler)
        await handler.aclose()
        self.assertEqual(list(self.root.iterdir()), [])

    async def test_attachment_failure_cleans_its_task(self):
        with self.assertLogs("jm-download-tests", level="ERROR"):
            results = [result async for result in self.plugin.jm_download(FakeEvent(fail_chain=True))]
        self.assertIn("simulated attachment failure", results[-1])
        self.assertEqual(list(self.root.iterdir()), [])

    async def run_abandoned_download(self, cancel):
        loop = asyncio.get_running_loop()
        started, cleaned = asyncio.Event(), asyncio.Event()
        release = threading.Event()
        def delayed_export(comic_id, option, *, extra):
            loop.call_soon_threadsafe(started.set)
            if not release.wait(timeout=5):
                raise RuntimeError("test worker was not released")
            self.export_pdf(comic_id, option, extra=extra)
        self.fake.download_album = delayed_export
        original_cleanup = self.plugin._cleanup
        def cleanup(task_dir):
            original_cleanup(task_dir)
            loop.call_soon_threadsafe(cleaned.set)
        self.plugin._cleanup = cleanup
        self.plugin.config["keep_files"] = True
        self.plugin.config["timeout"] = 2 if cancel else 0.05
        handler = self.plugin.jm_download(FakeEvent())
        await anext(handler)
        pending = asyncio.create_task(anext(handler))
        try:
            await asyncio.wait_for(started.wait(), timeout=2)
            if cancel:
                pending.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await pending
            else:
                self.assertIn("下载超时", await pending)
                with self.assertRaises(StopAsyncIteration):
                    await anext(handler)
            # The worker still owns its directory until it actually finishes.
            self.assertTrue(any(self.root.iterdir()))
        finally:
            release.set()
            await asyncio.wait_for(cleaned.wait(), timeout=2)
            await handler.aclose()
        self.assertEqual(list(self.root.iterdir()), [])

    async def test_timeout_discards_late_export_even_with_keep_files(self):
        await self.run_abandoned_download(cancel=False)

    async def test_cancelled_request_discards_late_export(self):
        await self.run_abandoned_download(cancel=True)


if __name__ == "__main__":
    unittest.main()
