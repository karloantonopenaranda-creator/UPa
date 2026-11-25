"""The submission builder (P), run on a small synthetic project."""
from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BUILDER = ROOT / "scripts" / "make_submission.py"
WORD = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"


def write_docx(path: Path, *paragraphs: str) -> None:
    body = "".join(f'<w:p><w:r><w:rPr><w:highlight w:val="yellow"/><w:highlightCs w:val="yellow"/></w:rPr><w:t>{p}</w:t></w:r></w:p>'
                   for p in paragraphs)
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("word/document.xml", f'<w:document xmlns:w="{WORD}"><w:body>{body}</w:body></w:document>')


class SubmissionBuilderTests(unittest.TestCase):
    def setUp(self) -> None:
        self.assertTrue(BUILDER.is_file(), "scripts/make_submission.py is missing")
        self._tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.root = root = Path(self._tmp.name)
        files = {
            "README.md": "| [TODO: member 1 name] | work |\n| [TODO: member 5 name] | work |\n",
            "requirements.txt": "# none\n",
            "pubsub/broker/broker.py": "", "pubsub/publisher/publisher.py": "", "pubsub/subscriber/subscriber.py": "",
            "pubsub/__pycache__/x.pyc": "", "tests/test_x.py": "",
            "docs/Presentation_and_QA_Guide.md": "team only", "docs/~$lock.docx": "",
            "data/broker.db": "", ".backup/old.txt": "", "team/notes.md": "private", "DormCare.zip": "",
            "PROJECT_AUDIT_REPORT.md": "internal",
        }
        for name, text in files.items():
            (root / name).parent.mkdir(parents=True, exist_ok=True)
            (root / name).write_text(text, encoding="utf-8", newline="\n")  # no Windows newline translation
        write_docx(root / "docs/System_Design_Document.docx", "[TODO: member 1 name]", "[TODO: member 5 name]",
                   "[TODO: page 6.1]")
        write_docx(root / "docs/Fault_Test_Report.docx", "[TODO: member 1 name]")
        (root / "scripts").mkdir()
        shutil.copy(BUILDER, root / "scripts")

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def build(self, details: str | None = None) -> subprocess.CompletedProcess:
        if details is not None:
            (self.root / "team/submission_details.ini").write_text("[details]\n" + details, encoding="utf-8")
        return subprocess.run([sys.executable, str(self.root / "scripts/make_submission.py")],
                              capture_output=True, text=True, timeout=60)

    def test_P1_incomplete_project_builds_a_clearly_marked_draft(self):
        result = self.build()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse((self.root / "DormCare_Submission.zip").exists())
        self.assertTrue((self.root / "DormCare_Submission_DRAFT.zip").exists())
        self.assertIn("page 6.1", result.stdout)

    def test_P2_archive_has_portable_names_and_only_submission_files(self):
        self.build()
        with zipfile.ZipFile(self.root / "DormCare_Submission_DRAFT.zip") as z:
            names = {info.orig_filename for info in z.infolist()}
        self.assertEqual(names, {f"DormCare_Submission/{n}" for n in (
            "README.md", "requirements.txt", "pubsub/broker/broker.py", "pubsub/publisher/publisher.py",
            "pubsub/subscriber/subscriber.py", "tests/test_x.py", "scripts/make_submission.py",
            "docs/System_Design_Document.docx", "docs/Fault_Test_Report.docx")})

    def test_P3_filled_details_produce_a_final_archive_without_touching_the_originals(self):
        result = self.build("member 1 name = Ana & Ben <A>\nmember 5 name = -\npage 6.1 = 123\n")
        self.assertEqual(result.returncode, 0, result.stderr)
        with zipfile.ZipFile(self.root / "DormCare_Submission.zip") as z:
            readme = z.read("DormCare_Submission/README.md").decode()
            with z.open("DormCare_Submission/docs/System_Design_Document.docx") as f, zipfile.ZipFile(f) as doc:
                xml = doc.read("word/document.xml").decode()
        self.assertEqual(readme, "| Ana & Ben <A> | work |\n")
        self.assertIn("Ana &amp; Ben", xml)
        self.assertIn(">123<", xml)
        self.assertNotIn("TODO", xml)
        self.assertNotIn("highlight", xml)
        self.assertEqual(xml.count("<w:p>"), 2, "the paragraph of the absent fifth member should be removed")
        self.assertIn("TODO", (self.root / "README.md").read_text(encoding="utf-8"))

    def test_P4_page_value_that_is_not_a_page_number_is_refused(self):
        result = self.build("page 6.1 = somewhere in chapter six\n")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("page 6.1", result.stdout + result.stderr)

    def test_P5_missing_required_file_is_refused(self):
        (self.root / "docs/Fault_Test_Report.docx").unlink()
        result = self.build()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Fault_Test_Report.docx", result.stdout + result.stderr)

    def test_P6_marketplace_runtime_and_assets_are_packaged(self):
        files = ("marketplace.js", "marketplace.css", "assets/marketplace/base.css",
                 "assets/marketplace/manrope.ttf", "assets/marketplace/living-room.jpg")
        for name in files:
            path = self.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"sample runtime asset")
        result = self.build()
        self.assertEqual(result.returncode, 0, result.stderr)
        with zipfile.ZipFile(self.root / "DormCare_Submission_DRAFT.zip") as archive:
            for name in files:
                self.assertIn(f"DormCare_Submission/{name}", archive.namelist())


if __name__ == "__main__":
    unittest.main()
