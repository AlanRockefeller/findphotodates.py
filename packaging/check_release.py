"""Fail tagged builds when source version and changelog disagree."""
import re
import os
import sys
from pathlib import Path

root = Path(__file__).resolve().parent.parent
source = (root / "findphotodates.py").read_text(encoding="utf-8")
match = re.search(r'^__version__\s*=\s*"([^"]+)"', source, re.MULTILINE)
tag = sys.argv[1] if len(sys.argv) > 1 else os.environ.get("RELEASE_TAG", "")
if not match or tag != "v" + match.group(1):
    raise SystemExit(f"Tag {tag!r} does not match findphotodates.py version")
changelog = (root / "CHANGELOG.md").read_text(encoding="utf-8")
if not re.search(rf"^## {re.escape(tag)}(?: |$)", changelog, re.MULTILINE):
    raise SystemExit(f"CHANGELOG.md needs an entry for {tag}")
print(f"Validated {tag}")
