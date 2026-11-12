import json
import shutil
import subprocess
from pathlib import Path

import pytest
from hook_master.providers.codex import powershell_command

from workflowhooker.cli import main


def _git(args: list[str], cwd: Path) -> None:
    subprocess.run(
        ["git"] + args, cwd=str(cwd), check=True, capture_output=True, text=True
    )


needs_git = pytest.mark.skipif(
    shutil.which("git") is None, reason="git nicht installiert"
)


def _write_config(
    tmp_path: Path, *, checks: list[str], max_messages=3, cooldown_minutes=0
) -> Path:
    checks_toml = json.dumps(checks)
    path = tmp_path / "workflowhooker.toml"
    path.write_text(
        f"""
[mode]
checks = {checks_toml}
max_messages_per_session = {max_messages}
cooldown_minutes = {cooldown_minutes}

[checks.scope_guard]
max_changed_files = 1
""",
        encoding="utf-8",
    )
    return path


def test_no_active_checks_by_default_is_silent(tmp_path, capsys):
    config_path = _write_config(tmp_path, checks=[])
    state_dir = tmp_path / "state"

    exit_code = main(
        [
            "--config",
            str(config_path),
            "--state-dir",
            str(state_dir),
            "--project-dir",
            str(tmp_path),
            "check",
        ]
    )
    assert exit_code == 0
    assert capsys.readouterr().out == ""


def test_closing_gate_fires_on_lock_file(tmp_path, capsys):
    (tmp_path / "LOCK.txt").write_text("owner: test\n", encoding="utf-8")
    config_path = _write_config(tmp_path, checks=["closing_gate"])
    state_dir = tmp_path / "state"

    exit_code = main(
        [
            "--config",
            str(config_path),
            "--state-dir",
            str(state_dir),
            "--project-dir",
            str(tmp_path),
            "check",
        ]
    )
    assert exit_code == 0
    out = capsys.readouterr().out
    assert "WorkflowHooker" in out
    assert "LOCK.txt" in out


def test_message_budget_enforced(tmp_path, capsys):
    (tmp_path / "LOCK.txt").write_text("owner: test\n", encoding="utf-8")
    config_path = _write_config(
        tmp_path, checks=["closing_gate"], max_messages=1, cooldown_minutes=0
    )
    state_dir = tmp_path / "state"
    common = [
        "--config",
        str(config_path),
        "--state-dir",
        str(state_dir),
        "--project-dir",
        str(tmp_path),
    ]

    main(common + ["check"])
    out1 = capsys.readouterr().out
    main(common + ["check"])
    out2 = capsys.readouterr().out

    assert "WorkflowHooker" in out1
    assert out2 == ""  # Budget von 1 Meldung/Sitzung ausgeschoepft


def test_cooldown_blocks_rapid_repeated_calls(tmp_path, capsys):
    (tmp_path / "LOCK.txt").write_text("owner: test\n", encoding="utf-8")
    config_path = _write_config(
        tmp_path, checks=["closing_gate"], max_messages=5, cooldown_minutes=5
    )
    state_dir = tmp_path / "state"
    common = [
        "--config",
        str(config_path),
        "--state-dir",
        str(state_dir),
        "--project-dir",
        str(tmp_path),
    ]

    main(common + ["check"])
    first = capsys.readouterr().out
    main(common + ["check"])
    second = capsys.readouterr().out

    assert "WorkflowHooker" in first
    assert second == ""  # Cooldown von 5 Minuten noch nicht abgelaufen


def test_providers_command(tmp_path, capsys):
    exit_code = main(["--state-dir", str(tmp_path), "providers"])
    assert exit_code == 0
    out = capsys.readouterr().out
    assert "claude: verfuegbar" in out
    assert "manual: verfuegbar" in out
    assert "gewaehlt: claude" in out


def test_install_snippet_has_no_pretooluse(tmp_path, capsys):
    out_path = tmp_path / "snippet.json"
    exit_code = main(
        ["--state-dir", str(tmp_path), "install-snippet", "--out", str(out_path)]
    )
    assert exit_code == 0
    data = json.loads(out_path.read_text(encoding="utf-8"))
    assert "PreToolUse" not in data["hooks"]
    assert set(data["hooks"]) == {"Stop", "PreCompact", "UserPromptSubmit"}


def test_install_snippet_supports_codex(tmp_path, capsys):
    out_path = tmp_path / "snippet-codex.json"
    exit_code = main(
        [
            "--state-dir",
            str(tmp_path),
            "install-snippet",
            "--provider",
            "codex",
            "--out",
            str(out_path),
        ]
    )
    assert exit_code == 0
    data = json.loads(out_path.read_text(encoding="utf-8"))
    command = data["hooks"]["Stop"][0]["hooks"][0]
    assert command["commandWindows"] == powershell_command(command["command"])
    assert command["statusMessage"] == "WorkflowHooker: Stop"


def test_install_snippet_action_guard_is_separate_opt_in(tmp_path, capsys):
    out_path = tmp_path / "guard.json"
    exit_code = main(
        [
            "--state-dir",
            str(tmp_path),
            "install-snippet",
            "--provider",
            "codex",
            "--variant",
            "action-guard",
            "--out",
            str(out_path),
        ]
    )
    assert exit_code == 0
    data = json.loads(out_path.read_text(encoding="utf-8"))
    assert set(data["hooks"]) == {"PreToolUse"}
    assert data["hooks"]["PreToolUse"][0]["matcher"] == "^apply_patch$"


@needs_git
def test_hook_run_stop_reports_uncommitted_changes(tmp_path, capsys):
    _git(["init"], tmp_path)
    _git(["config", "user.email", "t@example.com"], tmp_path)
    _git(["config", "user.name", "T"], tmp_path)
    (tmp_path / "a.txt").write_text("x", encoding="utf-8")
    _git(["add", "a.txt"], tmp_path)
    _git(["commit", "-m", "init"], tmp_path)
    (tmp_path / "a.txt").write_text("changed", encoding="utf-8")

    config_path = _write_config(tmp_path, checks=["closing_gate"])
    state_dir = tmp_path / "state"

    exit_code = main(
        [
            "--config",
            str(config_path),
            "--state-dir",
            str(state_dir),
            "--project-dir",
            str(tmp_path),
            "hook-run",
            "Stop",
        ]
    )
    assert exit_code == 0
    out = capsys.readouterr().out
    payload = json.loads(out)
    assert payload["hookSpecificOutput"]["hookEventName"] == "Stop"
    assert "uncommittete" in payload["hookSpecificOutput"]["additionalContext"]


def test_scope_guard_fires_when_many_files_changed(tmp_path, capsys, monkeypatch):
    # scope_guard.max_changed_files = 1 (siehe _write_config); files-Quelle
    # allein liefert kein uncommitted_files, also injizieren wir einen
    # gefaketen git-Runner ueber die Konfiguration ist hier nicht moeglich --
    # stattdessen direkt zwei getrackte Dateien via echten git-Adapter simulieren.
    pytest.importorskip("shutil")
    if shutil.which("git") is None:
        pytest.skip("git nicht installiert")

    _git(["init"], tmp_path)
    _git(["config", "user.email", "t@example.com"], tmp_path)
    _git(["config", "user.name", "T"], tmp_path)
    (tmp_path / "a.txt").write_text("a", encoding="utf-8")
    (tmp_path / "b.txt").write_text("b", encoding="utf-8")

    config_path = _write_config(tmp_path, checks=["scope_guard"])
    state_dir = tmp_path / "state"

    exit_code = main(
        [
            "--config",
            str(config_path),
            "--state-dir",
            str(state_dir),
            "--project-dir",
            str(tmp_path),
            "check",
        ]
    )
    assert exit_code == 0
    out = capsys.readouterr().out
    assert "Umfangswaechter" in out


def _hook_run(config_path, state_dir, project_dir, payload, monkeypatch, event="Stop"):
    import io

    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(payload)))
    return main(
        [
            "--config",
            str(config_path),
            "--state-dir",
            str(state_dir),
            "--project-dir",
            str(project_dir),
            "hook-run",
            event,
        ]
    )


def test_hook_run_trennt_state_nach_session_id_aus_stdin(tmp_path, capsys, monkeypatch):
    """Regression: Sitzungskennung kommt NUR aus dem stdin-JSON.

    Vor dem Fix (2026-07-27) wurde der State-Pfad aus ``args.session_id``
    gebildet, bevor stdin gelesen war -- alle Sitzungen teilten sich
    ``session-default.json``. Damit wurde aus ``max_messages_per_session`` ein
    Budget "ueberhaupt": Nach N Meldungen schwieg das Modul dauerhaft statt nur
    bis zur naechsten Sitzung. ``--session-id`` konnte das nicht heilen, weil
    Claude Code in Hook-Kommandos keine Variablen ersetzt.
    """
    (tmp_path / "LOCK.txt").write_text("owner: test\n", encoding="utf-8")
    config_path = _write_config(
        tmp_path, checks=["closing_gate"], max_messages=1, cooldown_minutes=0
    )
    state_dir = tmp_path / "state"

    assert (
        _hook_run(config_path, state_dir, tmp_path, {"session_id": "A"}, monkeypatch)
        == 0
    )
    assert capsys.readouterr().out.strip(), "erste Meldung der Sitzung A"

    # Budget von 1 ist in Sitzung A aufgebraucht.
    assert (
        _hook_run(config_path, state_dir, tmp_path, {"session_id": "A"}, monkeypatch)
        == 0
    )
    assert capsys.readouterr().out == "", "Budget gilt innerhalb der Sitzung"

    # Neue Sitzung: eigenes Budget.
    assert (
        _hook_run(config_path, state_dir, tmp_path, {"session_id": "B"}, monkeypatch)
        == 0
    )
    assert capsys.readouterr().out.strip(), "neue Sitzung startet mit frischem Budget"

    namen = sorted(p.name for p in state_dir.iterdir())
    assert namen == ["session-A.json", "session-B.json"], namen


def test_session_id_aus_stdin_wird_dateinamentauglich_entschaerft(
    tmp_path, monkeypatch
):
    """Die Kennung wandert in einen Dateinamen und kommt von aussen."""
    (tmp_path / "LOCK.txt").write_text("owner: test\n", encoding="utf-8")
    config_path = _write_config(tmp_path, checks=["closing_gate"])
    state_dir = tmp_path / "state"

    assert (
        _hook_run(
            config_path,
            state_dir,
            tmp_path,
            {"session_id": "../../boese/x"},
            monkeypatch,
        )
        == 0
    )

    geschrieben = list(state_dir.iterdir())
    assert len(geschrieben) == 1
    assert geschrieben[0].parent == state_dir, "darf den State-Ordner nicht verlassen"
    assert geschrieben[0].name == "session-boesex.json", geschrieben[0].name


def test_cli_session_id_schlaegt_stdin(tmp_path, monkeypatch):
    """Manuelle Aufrufe und Tests muessen weiter steuern koennen."""
    (tmp_path / "LOCK.txt").write_text("owner: test\n", encoding="utf-8")
    config_path = _write_config(tmp_path, checks=["closing_gate"])
    state_dir = tmp_path / "state"

    import io

    monkeypatch.setattr(
        "sys.stdin", io.StringIO(json.dumps({"session_id": "aus-stdin"}))
    )
    assert (
        main(
            [
                "--config",
                str(config_path),
                "--state-dir",
                str(state_dir),
                "--project-dir",
                str(tmp_path),
                "--session-id",
                "explizit",
                "hook-run",
                "Stop",
            ]
        )
        == 0
    )

    assert [p.name for p in state_dir.iterdir()] == ["session-explizit.json"]


def test_hook_run_plain_format_prints_bare_message(tmp_path, capsys, monkeypatch):
    """--format plain (Kimi): kein JSON-Wrapper auf stdout; Klartext wird als
    Weiterfuehr-Nachricht eingespeist."""
    (tmp_path / "LOCK.txt").write_text("owner: test\n", encoding="utf-8")
    config_path = _write_config(tmp_path, checks=["closing_gate"])
    state_dir = tmp_path / "state"

    import io

    monkeypatch.setattr(
        "sys.stdin", io.StringIO(json.dumps({"session_id": "kimi-probe"}))
    )
    exit_code = main(
        [
            "--config",
            str(config_path),
            "--state-dir",
            str(state_dir),
            "--project-dir",
            str(tmp_path),
            "hook-run",
            "--format",
            "plain",
            "Stop",
        ]
    )
    assert exit_code == 0
    out = capsys.readouterr().out
    assert "WorkflowHooker" in out
    assert not out.strip().startswith("{")


def test_hook_run_block_exits_2_with_message_on_stderr(tmp_path, capsys, monkeypatch):
    """--block (Kimi-Stop-Gate): Befund geht auf stderr, Exit 2 blockiert das
    Turn-Ende und speist die Nachricht als Weiterfuehrung ein."""
    (tmp_path / "LOCK.txt").write_text("owner: test\n", encoding="utf-8")
    config_path = _write_config(tmp_path, checks=["closing_gate"])
    state_dir = tmp_path / "state"

    import io

    monkeypatch.setattr(
        "sys.stdin", io.StringIO(json.dumps({"session_id": "kimi-block-probe"}))
    )
    exit_code = main(
        [
            "--config",
            str(config_path),
            "--state-dir",
            str(state_dir),
            "--project-dir",
            str(tmp_path),
            "hook-run",
            "--block",
            "Stop",
        ]
    )
    assert exit_code == 2
    captured = capsys.readouterr()
    assert "WorkflowHooker" in captured.err
    assert captured.out == ""


def test_hook_run_block_stays_silent_without_findings(tmp_path, capsys, monkeypatch):
    """Ohne Befund darf --block niemals blockieren (Exit 0, keine Ausgabe)."""
    config_path = _write_config(tmp_path, checks=["closing_gate"])
    state_dir = tmp_path / "state"

    import io

    monkeypatch.setattr(
        "sys.stdin", io.StringIO(json.dumps({"session_id": "kimi-block-clean"}))
    )
    exit_code = main(
        [
            "--config",
            str(config_path),
            "--state-dir",
            str(state_dir),
            "--project-dir",
            str(tmp_path),
            "hook-run",
            "--block",
            "Stop",
        ]
    )
    assert exit_code == 0
    captured = capsys.readouterr()
    assert captured.out == "" and captured.err == ""


def test_hook_run_block_is_rejected_for_userpromptsubmit(tmp_path, capsys, monkeypatch):
    """--block bei UserPromptSubmit wuerde den Prompt unterdruecken -- abgelehnt."""
    config_path = _write_config(tmp_path, checks=["closing_gate"])
    state_dir = tmp_path / "state"

    import io

    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps({})))
    exit_code = main(
        [
            "--config",
            str(config_path),
            "--state-dir",
            str(state_dir),
            "--project-dir",
            str(tmp_path),
            "hook-run",
            "--block",
            "UserPromptSubmit",
        ]
    )
    assert exit_code == 1
    assert "--block" in capsys.readouterr().err


def _hook_run_ohne_project_dir(
    config_path, state_dir, payload, monkeypatch, event="Stop"
):
    """Wie ``_hook_run``, aber OHNE ``--project-dir`` -- so laeuft der Hook real."""
    import io

    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(payload)))
    return main(
        [
            "--config",
            str(config_path),
            "--state-dir",
            str(state_dir),
            "hook-run",
            event,
        ]
    )


def test_hook_run_nimmt_arbeitsordner_aus_stdin(tmp_path, capsys, monkeypatch):
    """Regression: Der Arbeitsordner kommt aus dem stdin-JSON, nicht aus dem cwd.

    Dieselbe Fehlerklasse wie bei der Sitzungskennung, zweites Feld. Das
    Prozess-cwd eines Hooks ist der Ordner, in dem die SITZUNG gestartet
    wurde. Startet der Nutzer im Home (kein Repository), meldet die
    git-Quelle "nicht verfuegbar", der Projektzustand bleibt leer und KEIN
    Check kann je zutreffen -- das Modul laeuft, ohne je zu wirken.
    Gemessen auf ASUS-GEI am 2026-08-01: 17 Auswertungen, 0 Ausloesungen,
    bei gleichzeitig gesetzten Locks und uncommitteten Aenderungen.
    """
    projekt = tmp_path / "projekt"
    projekt.mkdir()
    (projekt / "LOCK.txt").write_text("owner: test\n", encoding="utf-8")
    woanders = tmp_path / "woanders"
    woanders.mkdir()
    config_path = _write_config(tmp_path, checks=["closing_gate"])
    state_dir = tmp_path / "state"

    monkeypatch.chdir(woanders)  # cwd zeigt bewusst NICHT auf das Projekt
    assert (
        _hook_run_ohne_project_dir(
            config_path,
            state_dir,
            {"session_id": "S", "cwd": str(projekt)},
            monkeypatch,
        )
        == 0
    )
    assert "LOCK.txt" in capsys.readouterr().out


def test_hook_run_faellt_auf_cwd_zurueck_wenn_stdin_ordner_fehlt(
    tmp_path, capsys, monkeypatch
):
    """Ein unbrauchbarer Ordner im Payload darf keine Quelle ins Leere zeigen
    lassen: fehlend, leer oder nicht existent -> zurueck auf das cwd."""
    projekt = tmp_path / "projekt"
    projekt.mkdir()
    (projekt / "LOCK.txt").write_text("owner: test\n", encoding="utf-8")
    config_path = _write_config(tmp_path, checks=["closing_gate"])

    monkeypatch.chdir(projekt)  # cwd IST diesmal das Projekt
    for i, payload in enumerate(
        [
            {"session_id": "F"},  # Feld fehlt
            {"session_id": "G", "cwd": "   "},  # leer
            {"session_id": "H", "cwd": str(tmp_path / "gibtsnicht")},  # existiert nicht
        ]
    ):
        assert (
            _hook_run_ohne_project_dir(
                config_path, tmp_path / f"state{i}", payload, monkeypatch
            )
            == 0
        )
        assert "LOCK.txt" in capsys.readouterr().out, payload

    # Und --project-dir behaelt Vorrang vor dem stdin-Wert.
    monkeypatch.chdir(tmp_path)
    assert (
        _hook_run(
            config_path,
            tmp_path / "state_x",
            projekt,
            {"session_id": "X", "cwd": str(tmp_path)},
            monkeypatch,
        )
        == 0
    )
    assert "LOCK.txt" in capsys.readouterr().out
