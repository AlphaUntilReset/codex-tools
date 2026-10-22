"""Asset-free build tests. All input bytes are synthetic, local transport fixtures."""
import base64
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest


SOURCE = Path(__file__).resolve().parent


class PrepareImagesTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='wallpaper-build-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / 'source-only'
        self.source.mkdir()
        for name in ('prepare_images.py', 'theme.template.css', 'sidebar.template.css', 'sidebar.palette.svg'):
            shutil.copyfile(SOURCE / name, self.source / name)
        self.background = self.root / 'my background.png'
        self.repeat = self.root / 'repeat.jpg'
        self.ending = self.root / 'ending.jpg'
        self.background.write_bytes(b'\x89PNG\r\n\x1a\n' + b'local-test-background')
        self.repeat.write_bytes(b'\xff\xd8\xff' + b'local-test-repeat')
        self.ending.write_bytes(b'\xff\xd8\xff' + b'local-test-ending')
        self.output = self.root / 'local build'

    def run_build(self):
        return subprocess.run([sys.executable, str(self.source / 'prepare_images.py'),
                               '--background', str(self.background), '--repeat', str(self.repeat),
                               '--ending', str(self.ending), '--output', str(self.output)],
                              capture_output=True, text=True)

    def test_source_only_build_embeds_supplied_bytes_and_preserves_originals(self):
        before = [image.read_bytes() for image in (self.background, self.repeat, self.ending)]
        result = self.run_build()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual({file.name for file in self.output.iterdir()},
                         {'SunFaded.png', 'SunFaded.css', 'Sidebar.css', 'SidebarPalette.svg'})
        self.assertEqual((self.output / 'SunFaded.png').read_bytes(), before[0])
        self.assertIn(base64.b64encode(before[0]).decode(), (self.output / 'SunFaded.css').read_text())
        for data in before[1:]:
            self.assertIn(base64.b64encode(data).decode(), (self.output / 'Sidebar.css').read_text())
        self.assertEqual(before, [image.read_bytes() for image in (self.background, self.repeat, self.ending)])
        self.assertFalse((self.root / '预览').exists())
        self.assertFalse((self.root / '图片').exists())

    def test_missing_image_fails_before_creating_output(self):
        self.ending.unlink()
        result = self.run_build()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Supply an existing bottom sidebar JPEG', result.stderr)
        self.assertFalse(self.output.exists())

    def test_renamed_wrong_format_does_not_replace_previous_output(self):
        self.output.mkdir()
        (self.output / 'SunFaded.css').write_text('previous build')
        self.repeat.write_bytes(self.background.read_bytes())
        result = self.run_build()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('renaming an extension', result.stderr)
        self.assertEqual((self.output / 'SunFaded.css').read_text(), 'previous build')

    def test_output_cannot_overwrite_an_original(self):
        self.background = self.root / 'SunFaded.png'
        self.background.write_bytes(b'\x89PNG\r\n\x1a\n' + b'original')
        self.output = self.root
        result = self.run_build()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('different from the original input files', result.stderr)
        self.assertFalse((self.output / 'SunFaded.css').exists())

    def test_default_private_sidebar_builder_does_not_require_private_preview(self):
        shutil.copyfile(SOURCE / 'build_sidebar.py', self.source / 'build_sidebar.py')
        images = self.root / '图片'
        images.mkdir()
        shutil.copyfile(self.repeat, images / '现代舞_上下重复.jpg')
        shutil.copyfile(self.ending, images / '现代舞_底端结束.jpg')
        result = subprocess.run([sys.executable, str(self.source / 'build_sidebar.py')],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue((self.source / 'Sidebar.css').exists())
        self.assertFalse((self.root / '预览').exists())


if __name__ == '__main__':
    unittest.main()
