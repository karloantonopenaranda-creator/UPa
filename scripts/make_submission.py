"""
Builds the submission archive.

    scripts\\make_submission.bat          (or: python scripts/make_submission.py)

Details that only the group can supply (names, page numbers, ...) are marked "[TODO: <key>]" in the README and
in the Word documents. Values from team/submission_details.ini replace them in the COPY that goes into the
archive; the working files are never changed. Filling them in by hand in the documents works as well.

    DormCare_Submission.zip         nothing is left to fill in
    DormCare_Submission_DRAFT.zip   something is; the missing keys are listed

A value of "-" for "member 5 name" means the group has four members: that member's lines are left out.
"""
from __future__ import annotations

import configparser
import io
import re
import sys
import time
import zipfile
from pathlib import Path
from xml.dom import minidom

ROOT = Path(__file__).resolve().parents[1]
NAME = "DormCare_Submission"
DETAILS = ROOT / "team" / "submission_details.ini"
INCLUDE = ("README.md", "requirements.txt", ".gitignore", "pubsub", "tests", "scripts", "docs",
           "index.html", "app.js", "styles.css", "marketplace.js", "marketplace.css", "assets",
           "DormCare_App_Specification.md")
REQUIRED = ("README.md", "requirements.txt", "pubsub/broker/broker.py", "pubsub/publisher/publisher.py",
            "pubsub/subscriber/subscriber.py", "docs/System_Design_Document.docx", "docs/Fault_Test_Report.docx")
TEAM_ONLY = {"docs/Presentation_and_QA_Guide.md"}

TOKEN = re.compile(r"\[TODO: ([^\]]+)\]")
PAGE = re.compile(r"\d{1,4}( ?[-–] ?\d{1,4})?")
DROP = "-"


def load_details() -> dict[str, str]:
    if not DETAILS.is_file():
        return {}
    parser = configparser.ConfigParser(interpolation=None)
    parser.read(DETAILS, encoding="utf-8-sig")
    values = {key: value.strip() for key, value in parser["details"].items() if value.strip()}
    for key, value in values.items():
        if key.startswith("page ") and not PAGE.fullmatch(value):
            raise SystemExit(f"{key}: {value!r} is not a page number or range such as 123 or 123-125")
        if value == DROP and not key.startswith("member 5 "):
            raise SystemExit(f"{key}: only member 5 may be left out, because a group has 4 or 5 members")
    if values.get("member 5 name") == DROP:
        values["member 5 contribution"] = DROP
    return values


def substitute(text: str, values: dict[str, str]) -> str:
    return TOKEN.sub(lambda m: values.get(m.group(1), m.group(0)), text)


def dropped(text: str, values: dict[str, str]) -> bool:
    return any(values.get(key) == DROP for key in TOKEN.findall(text))


def fill_markdown(data: bytes, values: dict[str, str], missing: set[str]) -> bytes:
    lines = [substitute(line, values) for line in data.decode("utf-8").splitlines(keepends=True)
             if not dropped(line, values)]
    text = "".join(lines)
    missing.update(TOKEN.findall(text))
    return text.encode("utf-8")


def fill_docx(data: bytes, values: dict[str, str], missing: set[str]) -> bytes:
    with zipfile.ZipFile(io.BytesIO(data)) as source:
        document = minidom.parseString(source.read("word/document.xml"))
        for paragraph in list(document.getElementsByTagName("w:p")):
            texts = [t for t in paragraph.getElementsByTagName("w:t") if t.firstChild is not None]
            if dropped("".join(t.firstChild.data for t in texts), values):
                holder = paragraph.parentNode
                if sum(node.nodeName == "w:p" for node in holder.childNodes) > 1:
                    holder.removeChild(paragraph)
                else:  # a table cell must keep one paragraph, so just empty it
                    for t in texts:
                        t.firstChild.data = ""
                continue
            for t in texts:
                new = substitute(t.firstChild.data, values)
                if new == t.firstChild.data:
                    continue
                t.firstChild.data = new
                if not TOKEN.search(new):  # filled in: no longer highlighted
                    for tag in ("w:highlight", "w:highlightCs"):
                        for mark in list(t.parentNode.getElementsByTagName(tag)):
                            mark.parentNode.removeChild(mark)
            missing.update(TOKEN.findall("".join(t.firstChild.data for t in texts)))
        out = io.BytesIO()
        with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as target:
            for info in source.infolist():
                part = document.toxml("UTF-8") if info.filename == "word/document.xml" else source.read(info)
                target.writestr(info, part, compress_type=zipfile.ZIP_DEFLATED)
    return out.getvalue()


def submission_files():
    for item in INCLUDE:
        path = ROOT / item
        candidates = sorted(path.rglob("*")) if path.is_dir() else [path]
        for file in candidates:
            name = file.relative_to(ROOT).as_posix()
            if (not file.is_file() or name in TEAM_ONLY or "__pycache__" in file.parts
                    or file.name.startswith("~$") or file.suffix == ".pyc"):
                continue
            yield file, name


def main() -> int:
    absent = [name for name in REQUIRED if not (ROOT / name).is_file()]
    if absent:
        raise SystemExit("Required file(s) missing: " + ", ".join(absent))
    values, missing = load_details(), set()
    contents = []
    for file, name in submission_files():
        data = file.read_bytes()
        if file.suffix == ".md":
            data = fill_markdown(data, values, missing)
        elif file.suffix == ".docx":
            data = fill_docx(data, values, missing)
        contents.append((name, data, time.localtime(file.stat().st_mtime)[:6]))

    draft, final = ROOT / f"{NAME}_DRAFT.zip", ROOT / f"{NAME}.zip"
    out, stale = (draft, final) if missing else (final, draft)
    with zipfile.ZipFile(out, "w") as archive:
        for name, data, modified in contents:
            info = zipfile.ZipInfo(f"{NAME}/{name}", date_time=modified)  # forward slashes on every system
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, data)
    if stale.exists():
        stale.unlink()  # built earlier from older files

    print(f"Created {out.name} ({len(contents)} files, {out.stat().st_size // 1024} KB)")
    if missing:
        print(f"DRAFT: {len(missing)} detail(s) still to fill in, in team/submission_details.ini:")
        for key in sorted(missing):
            print(f"  {key}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
