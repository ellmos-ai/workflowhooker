# -*- coding: utf-8 -*-
"""Tests for workflowhooker package version and module manifest integrity."""

import json
from pathlib import Path

import workflowhooker


def test_package_metadata():
    """Verify workflowhooker package version is defined."""
    assert hasattr(workflowhooker, "__version__")
    assert workflowhooker.__version__ == "0.3.1"


def test_manifest_validity():
    """Verify ellmos-module.v2.json manifest structure."""
    manifest_path = Path(__file__).parent.parent / "ellmos-module.v2.json"
    assert manifest_path.exists()

    with open(manifest_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    assert data.get("schema") == "ellmos.module.v2"
    assert data.get("id") == "workflowhooker"
    assert data.get("category") == "control"
    assert data.get("kind") == "workflow"
    # `status` wird gegen den Katalog-Enum geprueft, nicht gegen einen fest
    # verdrahteten Wert: "release-candidate" stand hier, ist aber in
    # _scripts/build_catalog.py (VALID_STATUSES) gar nicht zulaessig -- ein
    # Manifest mit diesem Wert faellt aus modules.catalog.json heraus und
    # reisst seine registry:modules-Bindings. Dasselbe war 2026-08-16 schon
    # bei memoryhooker passiert (dortiger Fix-Commit 252243f).
    assert data.get("status") in {
        "active",
        "released",
        "development",
        "alpha",
        "experimental",
        "staging",
        "planned",
        "deprecated",
    }
    assert data.get("visibility") == "public"

    # Das Modul IST gesplittet (siehe CONTRIBUTING.md): oeffentliche
    # Distribution + privater provenance-Zwilling. Der Kanon ist das
    # oeffentliche Repo, der Zwilling laeuft ueber repo_aliases.
    # Die fruehere Zusicherung "repository nicht gesetzt" hat genau das
    # verhindert und den Katalog das Modul als privat fuehren lassen
    # (Ticket T-20260830-168423367, Entscheid D-20260906-005/E02).
    source = data.get("source_of_truth", {})
    assert source.get("type") == "git-repository"
    assert source.get("repository") == "https://github.com/ellmos-ai/workflowhooker"
    assert data.get("repo_aliases") == ["ellmos-ai/workflowhooker-provenance"]
