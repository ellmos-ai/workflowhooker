"""Sitzungs-Basislinie: was war beim Sitzungsstart schon uncommittet?

Ohne Lock gibt es keinen Eigentumsbeleg fuer offene Aenderungen. Die Basislinie
liefert ihn aus Messwerten: Beim ersten Hook einer Sitzung wird je geaendertem
Pfad ein Inhalts-Hash festgehalten; beim Stop gilt als "eigene Aenderung", was
neu oder anders ist. Bereits vorher Offenes bleibt unangetastet.
ponytail: Hash-Vergleich kennt keinen zweiten Agenten im selben Arbeitsbaum;
dafuer bleibt der Lock-Weg (``ownership = "lock"``).
"""

from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path

_MAX_BYTES = 5_000_000


def take_snapshot(project_dir: Path) -> dict[str, str] | None:
    """Pfad -> Inhalts-Hash aller uncommitteten Pfade; ``None`` ohne belastbares git."""
    try:
        result = subprocess.run(
            ["git", "status", "--porcelain=v1", "-z", "--untracked-files=all"],
            cwd=str(project_dir),
            capture_output=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    snapshot: dict[str, str] = {}
    entries = result.stdout.decode("utf-8", "replace").split("\0")
    skip_next = False
    for entry in entries:
        if skip_next:  # Ursprungspfad einer Umbenennung
            skip_next = False
            continue
        if len(entry) < 4:
            continue
        if entry[0] in "RC":
            skip_next = True
        path = entry[3:]
        snapshot[path] = _digest(project_dir / path)
    return snapshot


def _digest(path: Path) -> str:
    try:
        if not path.is_file():
            return "absent"
        if path.stat().st_size > _MAX_BYTES:
            return f"size:{path.stat().st_size}"
        return hashlib.sha1(path.read_bytes()).hexdigest()
    except OSError:
        return "unreadable"


def owned_changes(
    baseline: dict[str, str], current: dict[str, str]
) -> tuple[list[str], list[str]]:
    """(eigene, vorbestehende) offene Pfade."""
    own = sorted(p for p, d in current.items() if baseline.get(p) != d)
    older = sorted(p for p, d in current.items() if baseline.get(p) == d)
    return own, older
