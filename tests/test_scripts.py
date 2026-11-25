"""
The Windows helper scripts (S), run for real in a temporary copy of scripts/.

Python is replaced by a stub that records the arguments it was given, so these tests check what the scripts
would launch without starting a broker or using port 5555.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
JUAN = "casa-jaro.announcements,casa-jaro.maintenance,casa-jaro.billing"
EXPECTED = {
    "broker": "-m pubsub.broker",
    "tenant-juan": f"-m pubsub.subscriber --id tenant-juan --topics {JUAN}",
    "tenant-jerome": "-m pubsub.subscriber --id tenant-jerome --topics molo-hub.announcements,molo-hub.billing",
    "caretaker": "-m pubsub.subscriber --id caretaker --topics casa-jaro.maintenance,molo-hub.maintenance",
    "manager-office": "-m pubsub.publisher --id manager-office",
    "maintenance-desk": "-m pubsub.publisher --id maintenance-desk",
}


@unittest.skipUnless(os.name == "nt", "the helper scripts are Windows batch files")
class ScriptTests(unittest.TestCase):
    folder = "DormCare"

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        base = Path(self._tmp.name)
        self.project = base / self.folder
        shutil.copytree(ROOT / "scripts", self.project / "scripts")
        demo = self.project / "scripts" / "start_demo.bat"
        demo.write_bytes(re.sub(rb'(?m)^start "', b'start /min "', demo.read_bytes()))  # don't cover the screen

        self.bin = base / "bin"
        self.bin.mkdir()
        self.stub = self.bin / "python_stub.bat"
        self.stub.write_bytes(b'@echo off\r\n>>"%~dp0record_%4.txt" echo %*\r\nexit\r\n')
        (self.bin / "netstat.bat").write_bytes(
            b"@echo off\r\necho   TCP    127.0.0.1:5555    0.0.0.0:0    LISTENING\r\n")
        system = Path(os.environ["SYSTEMROOT"]) / "System32"
        self.env = {k: v for k, v in os.environ.items() if k.upper() != "PATH"}
        self.env["PATH"] = os.pathsep.join(map(str, (self.bin, system, system / "WindowsPowerShell" / "v1.0")))
        self.env["DORMCARE_PYTHON"] = str(self.stub)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def recorded(self) -> dict[str, str]:
        found = {}
        for path in self.bin.glob("record_*.txt"):
            key = path.stem[len("record_"):] or "broker"
            found[key] = " ".join(path.read_text(errors="replace").split())
        return found

    def check_start_demo(self) -> None:
        subprocess.run(["cmd", "/c", r".\scripts\start_demo.bat"], cwd=self.project, env=self.env, timeout=90,
                       stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                       creationflags=subprocess.CREATE_NO_WINDOW)
        deadline = time.time() + 15
        while time.time() < deadline and len(self.recorded()) < len(EXPECTED):
            time.sleep(0.25)
        self.assertEqual(self.recorded(), EXPECTED)

    def test_S1_start_demo_starts_all_six_programs(self):
        self.check_start_demo()

    def test_S2_start_demo_works_when_another_find_comes_first_on_the_path(self):
        (self.bin / "find.bat").write_bytes(b"@echo off\r\nexit /b 1\r\n")  # stands in for Git's find.exe
        self.check_start_demo()

    def run_from_powershell(self, arguments: str) -> str:
        subprocess.run(["powershell", "-NoProfile", "-Command", f".\\scripts\\run_subscriber.bat {arguments}"],
                       cwd=self.project, env=self.env, timeout=30, stdin=subprocess.DEVNULL, capture_output=True)
        return self.recorded().get("tenant-juan", "nothing was started")

    def test_S3_run_subscriber_accepts_a_topic_list_from_powershell(self):
        self.assertEqual(self.run_from_powershell(f'tenant-juan "{JUAN}" --crash-after 1'),
                         f"-m pubsub.subscriber --id tenant-juan --topics {JUAN} --crash-after 1")

    def test_S4_run_subscriber_accepts_options_without_topics(self):
        self.assertEqual(self.run_from_powershell("tenant-juan --crash-after 1"),
                         "-m pubsub.subscriber --id tenant-juan --crash-after 1")


class ScriptsInAwkwardFolderTests(ScriptTests):
    """The same checks in a folder name with spaces and brackets, e.g. after unzipping twice."""

    folder = "DormCare Submission (1)"


if __name__ == "__main__":
    unittest.main()
