"""Static contract checks for the medical-library onboarding UI."""
import pathlib
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[2]


class MedicalLibrarySetupTests(unittest.TestCase):
    def test_publisher_links_are_https(self):
        source = (ROOT / 'src/setup/MedicalLibraries.tsx').read_text(encoding='utf-8')
        self.assertEqual(source.count("url: 'https://"), 8)
        self.assertNotIn("url: 'http://", source)

    def test_user_controls_acceptance(self):
        source = (ROOT / 'src/setup/MedicalLibraries.tsx').read_text(encoding='utf-8')
        self.assertIn('This is not acceptance of the publisher', source)
        self.assertIn('disabled={!understood}', source)
        self.assertIn('Skip for now', source)

    def test_no_embedded_frame(self):
        # health_server CSP is default-src 'self' (LOCKED): any iframe to a publisher is blocked.
        source = (ROOT / 'src/setup/MedicalLibraries.tsx').read_text(encoding='utf-8')
        self.assertNotIn('<iframe', source)

    def test_restore_kept(self):
        source = (ROOT / 'src/setup/Setup.tsx').read_text(encoding='utf-8')
        self.assertIn('Restore from a backup', source)

    def test_wired_to_first_run(self):
        source = (ROOT / 'src/setup/Setup.tsx').read_text(encoding='utf-8')
        self.assertIn('step === 3 && <MedicalLibraries onDone={() => setStep(4)}', source)
        self.assertIn('step === 4 && (', source)


if __name__ == '__main__':
    unittest.main()
