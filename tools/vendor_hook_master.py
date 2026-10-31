"""Copy hook_master at the commit pinned in pyproject.toml into vendor/ for the Claude Code plugin.

A plugin is installed as a plain repository checkout without `pip install`, so the pinned dependency has to ship
inside the plugin. The pin in pyproject.toml stays the single source; this script only reproduces it.
Run after changing the pin:  python tools/vendor_hook_master.py
"""
from __future__ import annotations

import io
import re
import shutil
import tarfile
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
VENDOR = ROOT / "vendor"
PIN_RE = re.compile(r"hook-master @ git\+https://github\.com/ellmos-ai/hook-master\.git@([0-9a-f]{40})")


def pinned_sha() -> str:
    match = PIN_RE.search((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    if not match:
        raise SystemExit("hook-master pin not found in pyproject.toml")
    return match.group(1)


def main() -> None:
    sha = pinned_sha()
    url = f"https://codeload.github.com/ellmos-ai/hook-master/tar.gz/{sha}"
    with urllib.request.urlopen(url, timeout=60) as resp:
        archive = tarfile.open(fileobj=io.BytesIO(resp.read()), mode="r:gz")
    target = VENDOR / "hook_master"
    shutil.rmtree(target, ignore_errors=True)
    for member in archive.getmembers():
        parts = Path(member.name).parts  # "<repo>-<sha>/src/hook_master/..." or "<repo>-<sha>/LICENSE"
        if ".." in parts:
            continue
        if member.isfile() and parts[1:3] == ("src", "hook_master") and "__pycache__" not in parts:
            dest = target.joinpath(*parts[3:])
        elif member.isfile() and parts[1:] == ("LICENSE",):
            dest = target / "LICENSE"
        else:
            continue
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(archive.extractfile(member).read())
    (VENDOR / "HOOK_MASTER_PIN").write_text(sha + "\n", encoding="utf-8")
    print(f"vendored hook_master@{sha[:7]} -> {target.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
