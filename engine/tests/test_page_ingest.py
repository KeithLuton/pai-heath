"""Document ingest tests — synthetic PDF and image fixtures only."""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ingest import ingest, ingest_file, download  # noqa: E402
from ingest.pdf_pages import IngestError  # noqa: E402
from ingest.download import DownloadError  # noqa: E402

FX = Path(__file__).resolve().parent / "fixtures" / "ingest"
PDF = FX / "lab_report_sample.pdf"
PNG = FX / "lab_photo_sample.png"


class PdfRaster(unittest.TestCase):
    def test_lab_pdf_pages(self):
        with tempfile.TemporaryDirectory() as d:
            bundle = ingest_file(PDF, Path(d) / "pages", dpi=150)
            self.assertEqual(bundle.page_count, 2)
            self.assertEqual(len(bundle.pages), 2)
            self.assertTrue(all(Path(p.path).is_file() for p in bundle.pages))
            self.assertTrue(all(p.width_px > 0 and p.height_px > 0 for p in bundle.pages))
            self.assertEqual(bundle.pages[0].source, "pdf_raster")
            self.assertEqual(bundle.pages[0].dpi, 150)
            self.assertEqual(len(bundle.document_id), 64)
            self.assertGreater(bundle.embedded_text_chars, 0)
            self.assertTrue(any("text layer" in n.lower() for n in bundle.notes))

    def test_pipeline_layout(self):
        with tempfile.TemporaryDirectory() as d:
            bundle, report = ingest(str(PDF), d, dpi=120)
            self.assertEqual(report.page_count, 2)
            self.assertEqual(report.importer_status, "LOCAL_RASTER")
            self.assertEqual(report.original_sha256, bundle.document_id)
            pages_dir = Path(report.image_dir)
            self.assertTrue(pages_dir.is_dir())
            self.assertEqual(sorted(p.name for p in pages_dir.glob("page_*.png")),
                             ["page_0001.png", "page_0002.png"])
            report_path = Path(d) / bundle.document_id / "report.json"
            self.assertTrue(report_path.is_file())
            data = json.loads(report_path.read_text())
            self.assertEqual(data["page_count"], 2)
            # Shareable report must not contain document body text
            blob = json.dumps(data)
            self.assertNotIn("Glucose", blob)
            self.assertNotIn("Cholesterol", blob)

    def test_page_range(self):
        with tempfile.TemporaryDirectory() as d:
            bundle = ingest_file(PDF, Path(d) / "pages", dpi=100, first_page=2, last_page=2)
        self.assertEqual(bundle.page_count, 1)
        self.assertEqual(bundle.pages[0].page_index, 1)

    def test_not_a_pdf(self):
        with tempfile.TemporaryDirectory() as d:
            fake = Path(d) / "nope.pdf"
            fake.write_bytes(b"not a pdf at all")
            with self.assertRaises(IngestError):
                ingest_file(fake, Path(d) / "pages")


class ImagePassthrough(unittest.TestCase):
    def test_png_single_page(self):
        with tempfile.TemporaryDirectory() as d:
            bundle, report = ingest(str(PNG), d)
            self.assertEqual(bundle.page_count, 1)
            self.assertEqual(bundle.pages[0].source, "image_passthrough")
            self.assertEqual(bundle.embedded_text_chars, 0)
            self.assertTrue(Path(bundle.pages[0].path).is_file())
            self.assertEqual(report.importer_status, "LOCAL_RASTER")

    def test_unsupported_extension(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "notes.txt"
            p.write_text("hello")
            with self.assertRaises(IngestError):
                ingest_file(p, Path(d) / "pages")


class DownloadPolicy(unittest.TestCase):
    def test_http_refused(self):
        with self.assertRaises(DownloadError):
            download("http://example.com/a.pdf", tempfile.gettempdir())

    def test_url_requires_flag(self):
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(IngestError):
                ingest("https://example.com/report.pdf", d, download_allowed=False)

    def test_download_happy_path(self):
        pdf_bytes = PDF.read_bytes()

        class FakeResp:
            status_code = 200
            url = "https://portal.example/lab.pdf"
            headers = {"Content-Type": "application/pdf", "Content-Length": str(len(pdf_bytes))}

            def iter_content(self, chunk_size=0):
                yield pdf_bytes

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        class FakeSession:
            def get(self, *a, **k):
                return FakeResp()

        with tempfile.TemporaryDirectory() as d:
            with mock.patch("ingest.download.requests.Session", FakeSession):
                result = download("https://portal.example/lab.pdf", d)
            self.assertTrue(result.path.is_file())
            self.assertEqual(result.media_type, "application/pdf")
            self.assertEqual(result.bytes_written, len(pdf_bytes))

            with mock.patch("ingest.download.requests.Session", FakeSession):
                bundle, report = ingest(
                    "https://portal.example/lab.pdf",
                    Path(d) / "out",
                    download_allowed=True,
                    dpi=100,
                )
            self.assertEqual(report.importer_status, "DOWNLOADED_THEN_RASTER")
            self.assertEqual(bundle.page_count, 2)
            self.assertTrue(report.source.startswith("https://portal.example"))


if __name__ == "__main__":
    unittest.main()
