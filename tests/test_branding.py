import unittest
from pathlib import Path
try:
    from PIL import Image
except ImportError:
    Image = None


@unittest.skipUnless(Image is not None, "Pillow is required")
class BrandingTests(unittest.TestCase):
    def test_all_six_covers_render(self):
        from fa_base.branding import banner_path
        for name in ("welcome", "check", "garants", "info", "report", "deal"):
            with self.subTest(name=name):
                path = Path(banner_path(name))
                self.assertTrue(path.is_file())
                with Image.open(path) as im:
                    self.assertEqual(im.format, "PNG")
                    self.assertEqual(im.size, (1200, 590))


if __name__ == '__main__':
    unittest.main()
