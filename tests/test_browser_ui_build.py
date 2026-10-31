"""Builder-only regressions; no game assets, downloads, or cloud requests."""
import importlib.util
import re
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("builder", ROOT / "build_girllife_html.py")
builder = importlib.util.module_from_spec(spec)
spec.loader.exec_module(builder)


class BrowserUIBuildTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.output = Path(self.temp.name)
        (self.output / "assets").mkdir()
        (self.output / "index.html").write_text(
            '<html><head><script type="module" crossorigin src="./assets/index-test.js?v=old"></script>'
            '</head><body></body></html>', encoding="utf-8")
        (self.output / "assets/index-test.js").write_text(
            'let instance$1=null;function getStorage(){};'
            'function command(_e){isPauseScreenVisible$.value||openPauseScreen(),pauseScreenCurrentPanel$.set(_e)};'
            'function quit(x){switch(x){case"quit":stopCurrentGame();break;}}', encoding="utf-8")

    def snapshot(self):
        return {str(p.relative_to(self.output)): p.read_bytes() for p in self.output.rglob("*") if p.is_file()}

    def test_fresh_install_and_repeat_are_identical(self):
        builder.apply_cloud_saves(self.output)
        first = self.snapshot()
        builder.apply_cloud_saves(self.output)
        self.assertEqual(first, self.snapshot())
        index = (self.output / "index.html").read_text()
        for name in ("browser-ui.js", "browser-ui.css", "cloud-saves-ui.js"):
            self.assertEqual(index.count(name), 1)
            self.assertIn(f'{name}?v={builder.CLOUD_SAVE_CACHE_VERSION}', index)
        self.assertIn(f'index-test.js?v={builder.CLOUD_SAVE_CACHE_VERSION}', index)

    @unittest.skipUnless(shutil.which("node"), "Node required for JS syntax checks")
    def test_generated_javascript_parses(self):
        builder.apply_cloud_saves(self.output)
        for path in (self.output / "browser-ui.js", self.output / "cloud-saves-ui.js", self.output / "assets/index-test.js"):
            result = subprocess.run(["node", "--check", str(path)], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)

    def test_cache_refresh_is_not_skipped_by_unrelated_version_text(self):
        index = self.output / "index.html"
        index.write_text(index.read_text().replace('</head>', f'<!-- {builder.CLOUD_SAVE_CACHE_VERSION} --></head>'))
        builder.apply_cloud_saves(self.output)
        self.assertNotIn('index-test.js?v=old', index.read_text())

    def test_unknown_player_hook_is_rejected(self):
        (self.output / "assets/index-test.js").write_text("console.log('changed upstream player')")
        with self.assertRaisesRegex(RuntimeError, "storage hook changed"):
            builder.apply_cloud_saves(self.output)

    def test_shell_does_not_shadow_engine_palette(self):
        css = re.sub(r"/\*.*?\*/", "", builder.BROWSER_UI_CSS, flags=re.S)
        self.assertNotRegex(css,
                            r"--(?:color|background-color|link-color|code-color|code-background-color)\s*:")
        self.assertIn('function syncPalette()', builder.BROWSER_UI_JS)
        self.assertIn('characterData:true', builder.BROWSER_UI_JS)


if __name__ == "__main__":
    unittest.main()
