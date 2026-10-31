"""CLI -- manueller Aufruf und Wiring-Schicht für Hook-Provider.

Befehle:

  python -m workflowhooker check                manueller Aufruf
  python -m workflowhooker hook-run <event>       stdin-JSON lesen (Event:
                                                    Stop/PreCompact/
                                                    UserPromptSubmit), gibt
                                                    Claude-Code-Hook-Output aus
  python -m workflowhooker providers              Provider-Fallback-Kette
  python -m workflowhooker install-snippet        Provider-Hook-Snippet
                                                    ausgeben

Kein Check ist per Default aktiv (``[mode] checks = []``) -- ohne Config
bleibt das Modul vollstaendig stumm, wie im README gefordert.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from pathlib import Path

from .boot_context_lint import lint_path
from .candidates import (
    LIFECYCLE_EVENTS,
    BudgetSpec,
    CandidateSpool,
    CorruptSpoolError,
    LifecycleController,
    default_queue_path,
    read_all,
)
from .checks import CHECK_REGISTRY, CheckRunner
from .compaction import build_compaction_output
from .config import Config, load_config
from .decisions import Decision, EvidenceState, GateResult, HookSurface
from .baseline import take_snapshot
from .gates import evaluate_action_guard, evaluate_scope_paths, evaluate_stop_gate
from .injectors import GoalInjector, LocationInjector, LoopInjector, PolicyInjector
from .locks import canonical_project_root
from .providers import PROVIDER_REGISTRY, resolve_provider
from .providers.claude import ClaudeProvider
from .providers.shadow import (
    SHADOW_PROVIDERS,
    dispatch_shadow_lifecycle,
    merge_hook_snippets,
)
from .sources import (
    CompositeStateSource,
    FilesStateSource,
    GitStateSource,
    GoalStateSource,
    TaskplanStateSource,
)
from .state import (
    SessionState,
    StateTransactionTimeout,
    StopRuntimeIntegrityError,
    default_state_dir,
    state_path_for_session,
    state_transaction,
)


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="workflowhooker")
    parser.add_argument(
        "--config", type=Path, default=None, help="Pfad zu workflowhooker.toml"
    )
    parser.add_argument(
        "--state-dir", type=Path, default=None, help="Override fuer den State-Ordner"
    )
    parser.add_argument(
        "--session-id", default=None, help="Session-ID zur State-Trennung"
    )
    parser.add_argument(
        "--project-dir",
        type=Path,
        default=None,
        help="Override fuer den Projektordner (Default: cwd)",
    )

    sub = parser.add_subparsers(required=True)

    p_check = sub.add_parser(
        "check", help="Aktive Checks gegen den Projektzustand auswerten"
    )
    p_check.set_defaults(func=_cmd_check)

    p_hook = sub.add_parser(
        "hook-run", help="stdin-JSON lesen, Claude-Code-Hook-Output schreiben"
    )
    p_hook.add_argument(
        "event", choices=["PreToolUse", "Stop", "PreCompact", "UserPromptSubmit", "SessionStart"]
    )
    p_hook.add_argument(
        "--provider",
        choices=["claude", "codex", "kimi", "manual"],
        default="claude",
        help="Providervertrag fuer die Ausgabe (keine Host-Konfiguration wird geaendert)",
    )
    p_hook.add_argument(
        "--format",
        dest="output_format",
        choices=["json", "plain"],
        default="json",
        help="Ausgabeformat: json (Claude/Codex, hookSpecificOutput) oder plain (Kimi: Klartext)",
    )
    p_hook.add_argument(
        "--block",
        action="store_true",
        help="Nur fuer Stop: Befund auf stderr + Exit 2 (blockierendes Gate, z. B. Kimi-Weiterfuehrung).",
    )
    p_hook.set_defaults(func=_cmd_hook_run)

    p_goal = sub.add_parser(
        "goal",
        aliases=["goal-inject"],
        help="Ziel-/Task-Erinnerung fuer PreCompact ausgeben",
    )
    p_goal.add_argument(
        "--format",
        dest="output_format",
        choices=["plain", "json"],
        default="plain",
        help="Ausgabeformat (plain oder JSON mit additionalContext)",
    )
    p_goal.set_defaults(func=_cmd_goal)

    p_loop = sub.add_parser(
        "loop-briefing",
        aliases=["loop-inject"],
        help="Aktuelles Weck-Briefing fuer eine lokale Runtime erzeugen",
    )
    p_loop.add_argument(
        "--format",
        dest="output_format",
        choices=["plain", "json"],
        default="plain",
        help="Ausgabeformat (plain oder JSON mit briefing)",
    )
    p_loop.set_defaults(func=_cmd_loop_briefing)

    p_providers = sub.add_parser("providers", help="Provider-Fallback-Kette anzeigen")
    p_providers.set_defaults(func=_cmd_providers)

    p_install = sub.add_parser("install-snippet", help="Provider-Hook-Snippet ausgeben")
    p_install.add_argument(
        "--provider",
        default="claude",
        choices=list(PROVIDER_REGISTRY.keys()),
        help="Provider (claude, codex, ...)",
    )
    p_install.add_argument("--out", type=Path, default=None)
    p_install.add_argument(
        "--variant",
        choices=["default", "action-guard", "session-start", "compaction"],
        default="default",
        help="Default-Hinweise oder separater Opt-in-PreToolUse-Guard",
    )
    p_install.add_argument(
        "--shadow", action="store_true", help="Nur opt-in S4-Lifecycle-Shadow-Handler ausgeben"
    )
    p_install.add_argument(
        "--existing", type=Path, default=None,
        help="Vorhandene Hook-JSON nur zum deduplizierten hooks-only Merge lesen",
    )
    p_install.add_argument("--hook-config", type=Path, default=None)
    p_install.add_argument("--hook-state-dir", type=Path, default=None)
    p_install.add_argument("--session-root", type=Path, default=None)
    p_install.set_defaults(func=_cmd_install_snippet)

    p_provider_shadow = sub.add_parser(
        "provider-shadow",
        help=(
            "Stiller Codex-/Claude-Lifecycleadapter: validiert den expliziten "
            "Sessionpfad und schreibt nur in den opt-in Kandidatenspool"
        ),
    )
    p_provider_shadow.add_argument("provider", choices=SHADOW_PROVIDERS)
    p_provider_shadow.add_argument("event", choices=LIFECYCLE_EVENTS)
    p_provider_shadow.add_argument("--session-root", type=Path, default=None)
    p_provider_shadow.set_defaults(func=_cmd_provider_shadow)

    p_candidate_collect = sub.add_parser(
        "candidate-collect",
        help=(
            "LEICHTER Lifecycle-Hook: plant atomare, redigierte Jobs/Checkpoints "
            "(Zeiger, keine Transkriptinhalte) oder recovered abgelaufene Leases. "
            "Stumm, solange [candidates].enabled nicht gesetzt ist."
        ),
    )
    p_candidate_collect.add_argument("event", choices=LIFECYCLE_EVENTS)
    p_candidate_collect.add_argument(
        "--provider",
        default="unknown",
        help="Anbieterkennung fuer das Envelope (claude, codex, kimi, agy, ...)",
    )
    p_candidate_collect.set_defaults(func=_cmd_candidate_collect)

    p_candidate_extract = sub.add_parser(
        "candidate-extract",
        help=(
            "OFFLINE: gesammelte Kandidaten-Jobs/Receipts sichten und auf die "
            "manuellen Skills skill-extractor/workflow-extract verweisen. "
            "Fuehrt selbst KEINE Extraktion aus."
        ),
    )
    p_candidate_extract.add_argument(
        "--format", dest="output_format", choices=["plain", "json"], default="plain"
    )
    p_candidate_extract.add_argument(
        "--clear",
        action="store_true",
        help=(
            "Veraltete Kompatibilitaetsoption; bleibt read-only und entfernt "
            "weder v1-JSONL noch v2-Jobs"
        ),
    )
    p_candidate_extract.set_defaults(func=_cmd_candidate_extract)

    p_boot_lint = sub.add_parser(
        "boot-context-lint",
        help="Boot-Markdown und Sidecar-JSON rein lesend auf Laufprotokoll-Drift prüfen",
    )
    p_boot_lint.add_argument("paths", nargs="+", type=Path, help="Explizit zu prüfende Dateien")
    p_boot_lint.add_argument(
        "--format",
        dest="output_format",
        choices=["plain", "json"],
        default="plain",
        help="Ausgabeformat (plain oder JSON)",
    )
    p_boot_lint.set_defaults(func=_cmd_boot_context_lint)

    return parser


def _state_path(args) -> Path:
    return state_path_for_session(args.session_id, args.state_dir)


def _build_state_source(config: Config, project_dir: Path) -> CompositeStateSource:
    """Baut die Quellen aus ``[sources].order`` (Default: alle).

    Frueher waren alle drei hart verdrahtet -- ``order`` in der Config war
    wirkungslos und suggerierte eine Kontrolle, die es nicht gab.
    """
    files_dir = (
        Path(config.sources.project_dir) if config.sources.project_dir else project_dir
    )
    git_dir = Path(config.sources.git_dir) if config.sources.git_dir else project_dir

    builders = {
        "files": lambda: FilesStateSource(files_dir),
        "git": lambda: GitStateSource(git_dir),
        "taskplan": lambda: TaskplanStateSource(project_dir),
        "goal": lambda: GoalStateSource(files_dir),
    }
    return CompositeStateSource(
        [builders[name]() for name in config.sources.order if name in builders]
    )


def _run_active_checks(
    config: Config, project_dir: Path, state: SessionState, *, now: float | None = None
) -> str | None:
    now = time.time() if now is None else now

    if state.messages_sent >= config.mode.max_messages_per_session:
        return None
    if (
        state.last_message_ts is not None
        and (now - state.last_message_ts) < config.mode.cooldown_minutes * 60
    ):
        return None

    source = _build_state_source(config, project_dir)
    project_state = source.snapshot()

    runner = CheckRunner(CHECK_REGISTRY)
    for check_name in config.mode.checks:
        runtime = state.runtime_for(check_name)
        message = runner.run(check_name, project_state, config, runtime)
        if message is not None:
            state.messages_sent += 1
            state.last_message_ts = now
            return message

    return None


def _cmd_check(args) -> int:
    config = load_config(args.config)
    state_path = _state_path(args)
    state, state_reliable = SessionState.load_checked(state_path)
    project_dir = args.project_dir or Path.cwd()

    message = _run_active_checks(config, project_dir, state)

    if state_reliable:
        state.save(state_path)
    if message:
        print(message)
    return 0


def _cmd_hook_run(args) -> int:
    if getattr(args, "block", False) and args.event != "Stop":
        # Blockieren unterdrueckt bei UserPromptSubmit den Prompt und ist bei
        # PreCompact wirkungslos (Beobachtungs-Event) -- nur Stop hat eine
        # dokumentierte Weiterfuehr-Semantik.
        print("--block ist nur fuer das Stop-Event sinnvoll.", file=sys.stderr)
        return 1
    # stdin MUSS vor dem State gelesen werden: Die Sitzungskennung steht nur dort.
    # (Frueher wurde der Inhalt verworfen und nur konsumiert, damit kein Hook-
    # Prozess an ungelesenem stdin haengt -- das Konsumieren bleibt, der Inhalt
    # wird jetzt zusaetzlich ausgewertet.)
    payload, payload_reliable = _read_stdin_json_checked()

    try:
        config = load_config(args.config)
    except (OSError, UnicodeError, ValueError, TypeError) as exc:
        # Fail-open mit sichtbarem Hinweis (0.3.1): Eine kaputte Konfiguration
        # darf nie jede Dateiaenderung sperren -- auch nicht die Reparatur der
        # workflowhooker.toml selbst. Ohne lesbare Konfiguration gibt es keine
        # greifenden protected/allowed-Regeln, die abzulehnen waeren.
        detail = " ".join(f"{type(exc).__name__}: {exc}".split())[:300]
        notice = (
            f"[WorkflowHooker] Konfiguration '{args.config}' nicht ladbar ({detail}). "
            "Scope-Guard und Action-Guard sind inaktiv, bis die Datei repariert ist; "
            "Dateiaktionen werden bis dahin nicht geprueft."
        )
        if args.event == "PreToolUse":
            return _emit_notice(notice, args.provider)
        print(
            f"[WorkflowHooker] Kontextprüfung nicht verfügbar (Konfigurationsfehler in "
            f"'{args.config}': {detail}).",
            file=sys.stderr,
        )
        return 0

    if args.provider == "codex" and args.event == "SessionStart":
        if not payload_reliable:
            return 0
        if payload.get("source") == "compact":
            output = build_compaction_output(
                payload, provider=args.provider, enabled=config.compaction.enabled,
            )
            if output is not None:
                # ASCII JSON is lossless and independent of Windows console encoding.
                print(json.dumps(output, ensure_ascii=True))
            return 0

    project_dir = canonical_project_root(
        args.project_dir or _extract_cwd(payload) or Path.cwd()
    )

    if args.event in ("PreToolUse", "UserPromptSubmit", "SessionStart"):
        _ensure_baseline(args, config, payload, project_dir)

    if args.event == "PreToolUse":
        rules = config.checks.scope_guard
        notice = None
        if "scope_guard" in config.mode.checks and (rules.protected or rules.allowed):
            # Pfadregeln gelten ohne Identitaet/Lock; Nicht-Zustaendigkeit = None.
            if not payload_reliable:
                # Leitplanke, keine Sicherheitsgrenze: weiterarbeiten, aber sichtbar.
                notice = (
                    "[WorkflowHooker] Scope-Guard fuer diese Dateiaktion uebersprungen: "
                    "Hook-Payload nicht lesbar."
                )
            else:
                scope_result = evaluate_scope_paths(
                    payload, config, project_dir, config_path=args.config
                )
                if scope_result is not None:
                    if scope_result.decision is not Decision.ALLOW:
                        return _emit_action_result(scope_result, args.provider)
                    notice = scope_result.message or None
        if not config.action_guard.enabled:
            return _emit_notice(notice, args.provider)
        if not payload_reliable:
            result = GateResult(
                HookSurface.ACTION_GUARD,
                Decision.DENY,
                EvidenceState.UNKNOWN,
                "payload-unavailable",
                "Kritische Dateiaktion gesperrt: Hook-Payload nicht belastbar.",
                "pending-action",
            )
        else:
            result = evaluate_action_guard(payload, config, project_dir)
        if result.decision is Decision.ALLOW and notice:
            return _emit_notice(notice, args.provider)
        return _emit_action_result(result, args.provider)

    # Ohne diese Zeile landen ALLE Sitzungen in session-default.json, und die
    # beiden Budgets dieses Moduls verlieren ihren Sinn: Aus
    # `max_messages_per_session = 3` wird "3 Meldungen ueberhaupt" -- danach
    # schweigt das Modul dauerhaft statt nur bis zur naechsten Sitzung.
    # `--session-id` allein reicht nicht: Ein Hook-Kommando in settings.json kann
    # sie nicht fuellen, weil Claude Code dort keine Variablen ersetzt. Die
    # Kennung kommt ausschliesslich im stdin-JSON. Die CLI-Option behaelt Vorrang,
    # damit manuelle Aufrufe und Tests weiterhin steuern koennen.
    state_path = state_path_for_session(
        args.session_id or _extract_session_id(payload), args.state_dir
    )

    # Dieselbe Lektion wie eine Zeile hoeher, zweites Feld: Der Arbeitsordner
    # steht ebenfalls nur im stdin-JSON. Das Prozess-cwd eines Hooks ist der
    # Ordner, in dem die SITZUNG gestartet wurde -- nicht der, in dem gerade
    # gearbeitet wird. Startet der Nutzer in seinem Home (kein Repository),
    # meldet die git-Quelle "nicht verfuegbar", der Projektzustand bleibt leer
    # und KEIN Check kann je zutreffen: Das Modul laeuft, ohne je zu wirken.
    if args.event == "Stop" and config.stop_gate.enabled:
        source = _build_state_source(config, project_dir)
        project_state = source.snapshot()
        try:
            with state_transaction(state_path):
                state, state_reliable = SessionState.load_checked(state_path)
                result = evaluate_stop_gate(
                    payload,
                    config,
                    project_dir,
                    project_state,
                    state,
                    state_reliable=state_reliable,
                )
                try:
                    state.save(state_path)
                except (OSError, StopRuntimeIntegrityError):
                    # Ohne dauerhaft gespeicherten Rundenbeleg darf der Hook
                    # nicht blockieren: Sonst koennte jeder Stop erneut
                    # "Runde 1" sein.
                    result = GateResult(
                        HookSurface.STOP,
                        Decision.ALLOW,
                        EvidenceState.UNKNOWN,
                        "stop-state-save-failed",
                        "[WorkflowHooker] Abschlusszustand unbekannt: Rundenbeleg konnte nicht gespeichert werden; keine weitere Hookschleife.",
                        str(project_dir.resolve(strict=False)),
                    )
        except (OSError, StateTransactionTimeout):
            result = GateResult(
                HookSurface.STOP,
                Decision.ALLOW,
                EvidenceState.UNKNOWN,
                "stop-state-transaction-failed",
                "[WorkflowHooker] Abschlusszustand unbekannt: State-Transaktion nicht verfügbar; keine weitere Hookschleife.",
                str(project_dir.resolve(strict=False)),
            )
        return _emit_stop_result(
            result,
            args.provider,
            legacy_block=args.block,
            output_format=args.output_format,
        )

    state, state_reliable = SessionState.load_checked(state_path)
    message = _run_active_checks(config, project_dir, state)
    if message is None and args.event == "PreCompact" and config.injectors.goal:
        now = time.time()
        if _message_slot_available(config, state, now):
            message = GoalInjector(_build_state_source(config, project_dir)).generate()
            if message:
                _record_message(state, now)
    if message is None and args.event == "SessionStart" and (
        config.injectors.policy or config.injectors.location
    ):
        now = time.time()
        if _message_slot_available(config, state, now):
            message = _build_session_start_message(config, project_dir)
            if message:
                _record_message(state, now)
    if state_reliable:
        state.save(state_path)

    if message:
        output = {
            "hookSpecificOutput": {
                "hookEventName": args.event,
                "additionalContext": message,
            }
        }
        if getattr(args, "block", False):
            # Kimi-Stop-Gate: Exit 2 + stderr blockiert das Turn-Ende und
            # speist die Nachricht als Weiterfuehrung ein.
            # Loop-Bremse: max_messages_per_session + cooldown des Moduls.
            print(message, file=sys.stderr)
            return 2
        if args.output_format == "plain":
            # Kimi Code speist Klartext auf stdout als Kontext ein; eine
            # Auswertung von hookSpecificOutput-JSON ist dort nicht dokumentiert.
            print(message)
        else:
            print(json.dumps(output, ensure_ascii=False))
    return 0


def _ensure_baseline(args, config: Config, payload: dict, project_dir: Path) -> None:
    """Haelt beim ersten Hook einer Sitzung fest, was schon uncommittet war.

    Voraussetzung fuer ``[stop_gate].ownership = "session"``. Best effort: ein
    Fehler hier darf keinen Hook kippen (ohne Basislinie bleibt der Eigentums-
    beleg einfach aus).
    """
    if not (config.stop_gate.enabled and config.stop_gate.ownership == "session"):
        return
    path = state_path_for_session(args.session_id or _extract_session_id(payload), args.state_dir)
    try:
        with state_transaction(path):
            state, reliable = SessionState.load_checked(path)
            if not reliable or state.baseline is not None:
                return
            snapshot = take_snapshot(project_dir)
            if snapshot is None:
                return
            state.baseline = snapshot
            state.save(path)
    except (OSError, StateTransactionTimeout, StopRuntimeIntegrityError):
        return


def _emit_notice(message: str | None, provider: str) -> int:
    """Sichtbarer Hinweis ohne Entscheidung: die Aktion laeuft weiter (fail-open)."""

    if not message:
        return 0
    if provider == "kimi":
        print(message, file=sys.stderr)
    else:
        print(json.dumps({"systemMessage": message}, ensure_ascii=False))
    return 0


def _emit_action_result(result: GateResult, provider: str) -> int:
    """Formatiert ausschließlich PreToolUse-Entscheidungen.

    ``allow`` bleibt still und erteilt damit keine neue Host-Berechtigung.
    Claude kann ``ask`` nativ darstellen. Codex und Kimi koennen im
    PreToolUse-Vertrag kein belastbares ``ask`` erzwingen; dort wird die
    fehlende Autoritaet sicher als deny an den Master zurueckgegeben.
    """

    if result.decision is Decision.ALLOW:
        return 0
    decision = result.decision
    if decision is Decision.ASK and provider in {"codex", "kimi", "manual"}:
        decision = Decision.DENY
    if provider == "kimi":
        print(result.message, file=sys.stderr)
        return 2
    output = {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": decision.value,
            "permissionDecisionReason": result.message,
        }
    }
    print(json.dumps(output, ensure_ascii=False))
    return 0


def _emit_stop_result(
    result: GateResult,
    provider: str,
    *,
    legacy_block: bool,
    output_format: str,
) -> int:
    """Formatiert Stop getrennt vom PreToolUse-Guard."""

    if result.decision is Decision.DENY:
        if provider == "kimi" or legacy_block:
            print(result.message, file=sys.stderr)
            return 2
        print(
            json.dumps(
                {"decision": "block", "reason": result.message}, ensure_ascii=False
            )
        )
        return 0
    if result.message:
        if provider == "kimi" or output_format == "plain":
            print(result.message)
        else:
            # systemMessage macht den Restbefund sichtbar, ohne einen zweiten
            # Stop-Nachstoss anzufordern.
            print(json.dumps({"systemMessage": result.message}, ensure_ascii=False))
    return 0


def _extract_session_id(payload: dict) -> str | None:
    """Sitzungskennung aus dem Hook-stdin-JSON, dateinamentauglich gemacht.

    Der Wert wandert in einen Dateinamen (``session-<id>.json``), deshalb bleiben
    nur unbedenkliche Zeichen stehen. Claude Code liefert UUIDs, aber der Wert
    kommt von aussen -- ein ``..`` oder ein Pfadtrenner darf nicht durchschlagen.
    Bleibt nichts uebrig, wird ``None`` zurueckgegeben: dann greift wie bisher
    ``session-default.json``, statt einen kaputten Namen zu bauen.
    """
    raw = payload.get("session_id")
    if not isinstance(raw, str):
        return None
    sicher = "".join(z for z in raw if z.isalnum() or z in "-_")[:64]
    return sicher or None


def _extract_cwd(payload: dict) -> Path | None:
    """Arbeitsordner aus dem Hook-stdin-JSON.

    Anders als die Sitzungskennung wandert dieser Wert in keinen Dateinamen,
    sondern wird nur gelesen (``git status``, Lock-Suche) -- deshalb keine
    Zeichenfilterung, aber die Pruefung, dass der Ordner wirklich existiert.
    Ein nicht existierender Pfad faellt auf ``Path.cwd()`` zurueck, statt eine
    Quelle auf ein Nichts zeigen zu lassen.
    """
    raw = payload.get("cwd")
    if not isinstance(raw, str) or not raw.strip():
        return None
    candidate = Path(raw)
    return candidate if candidate.is_dir() else None


def _read_stdin_json() -> dict:
    payload, _ = _read_stdin_json_checked()
    return payload


def _read_stdin_json_checked() -> tuple[dict, bool]:
    # isatty() ist kein verlaesslicher Indikator, ob stdin sicher lesbar ist
    # (z. B. faengt pytest-Capture stdin durch ein Objekt ab, das weder ein
    # TTY ist noch echtes Lesen erlaubt und stattdessen OSError wirft).
    # Ein Hook darf dadurch niemals crashen -- deshalb defensiv abfangen.
    try:
        if sys.stdin.isatty():
            return {}, False
        raw = sys.stdin.read()
    except (OSError, ValueError):
        return {}, False
    if not raw.strip():
        return {}, False
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return {}, False
    if not isinstance(payload, dict):
        return {}, False
    return payload, True


def _cmd_providers(args) -> int:
    config = load_config(args.config)
    for name in config.providers.order:
        provider = PROVIDER_REGISTRY.get(name)
        available = provider.is_available() if provider else False
        marker = "verfuegbar" if available else "nicht verfuegbar"
        print(f"{name}: {marker}")
    chosen = resolve_provider(config.providers)
    print(f"gewaehlt: {chosen.name}")
    return 0


def _cmd_install_snippet(args) -> int:
    provider = PROVIDER_REGISTRY.get(args.provider, ClaudeProvider())
    if args.shadow:
        if args.variant != "default":
            print("--shadow cannot be combined with another variant.", file=sys.stderr)
            return 2
        builder = getattr(provider, "shadow_hook_snippet", None)
        if builder is None:
            print("Shadow-Snippet ist nur für claude und codex verfügbar.", file=sys.stderr)
            return 2
        snippet = builder(
            config_path=args.hook_config,
            state_dir=args.hook_state_dir,
            session_root=args.session_root,
        )
    elif args.variant == "compaction":
        builder = getattr(provider, "compaction_snippet", None)
        if builder is None:
            print("Komprimierungshinweis ist nur für codex verfügbar.", file=sys.stderr)
            return 2
        snippet = builder()
    elif args.variant == "session-start":
        builder = getattr(provider, "session_start_snippet", None)
        if builder is None:
            print("SessionStart-Snippet ist nur für claude und codex verfügbar.", file=sys.stderr)
            return 2
        snippet = builder()
    elif args.variant == "action-guard":
        builder = getattr(provider, "pretooluse_blocker_snippet", None)
        if builder is None:
            print(
                f"Provider {args.provider} hat keinen belegten Action-Guard-Kanal.",
                file=sys.stderr,
            )
            return 1
        snippet = builder()
    else:
        snippet = provider.hook_snippet()
    if args.existing:
        try:
            existing = json.loads(args.existing.read_text(encoding="utf-8"))
            snippet = merge_hook_snippets(existing, snippet)
        except (OSError, ValueError) as exc:
            print(f"Vorhandene Hook-JSON ist unlesbar: {exc}", file=sys.stderr)
            return 2
    text = json.dumps(snippet, indent=2, ensure_ascii=False)
    if args.out:
        args.out.write_text(text, encoding="utf-8")
        print(
            f"geschrieben nach {args.out} -- manuell in die Hook-Config einmischen",
            file=sys.stderr,
        )
    else:
        print(text)
    return 0


def _build_session_start_message(config: Config, project_dir: Path) -> str | None:
    """Combine Policy- and Ortsinjektor output into ONE message.

    Deliberately a single combined message, not two separate ones: two
    SessionStart injectors would otherwise spend two of
    ``mode.max_messages_per_session`` on the very first turn, starving the
    rest of the session's message budget for checks/goal (README, Abschnitt
    "Session-Start-Hooker"). Either half may be empty; the whole call
    returns ``None`` only if both are.
    """
    parts: list[str] = []
    if config.injectors.policy:
        policy_config = config.injectors.policy_config
        registry_path = Path(policy_config.registry_path) if policy_config.registry_path else None
        policy_message = PolicyInjector(
            registry_path=registry_path, max_entries=policy_config.max_entries
        ).generate(project_dir)
        if policy_message:
            parts.append(policy_message)
    if config.injectors.location:
        roles = tuple(config.injectors.location_config.roles)
        location_message = LocationInjector(roles=roles or None).generate(project_dir)
        if location_message:
            parts.append(location_message)
    if not parts:
        return None
    return "\n\n".join(parts)


def _cmd_goal(args) -> int:
    config = load_config(args.config)
    project_dir = args.project_dir or Path.cwd()
    message = GoalInjector(_build_state_source(config, project_dir)).generate()
    if message:
        if args.output_format == "json":
            print(
                json.dumps(
                    {
                        "hookSpecificOutput": {
                            "hookEventName": "PreCompact",
                            "additionalContext": message,
                        }
                    },
                    ensure_ascii=False,
                )
            )
        else:
            print(message)
    return 0


def _cmd_loop_briefing(args) -> int:
    config = load_config(args.config)
    project_dir = args.project_dir or Path.cwd()
    message = LoopInjector(_build_state_source(config, project_dir)).generate()
    if message:
        if args.output_format == "json":
            print(json.dumps({"briefing": message}, ensure_ascii=False))
        else:
            print(message)
    return 0


def _message_slot_available(config: Config, state: SessionState, now: float) -> bool:
    if state.messages_sent >= config.mode.max_messages_per_session:
        return False
    return not (
        state.last_message_ts is not None
        and (now - state.last_message_ts) < config.mode.cooldown_minutes * 60
    )


def _record_message(state: SessionState, now: float) -> None:
    state.messages_sent += 1
    state.last_message_ts = now


def _opaque_session_ref(value: str | None) -> str | None:
    """Map an external session ID to a stable, private contract identity."""

    if not isinstance(value, str) or not value.strip():
        return None
    digest = hashlib.sha256(value.strip().encode("utf-8")).hexdigest()
    return f"sha256:{digest}"


def _extract_session_ref(payload: dict) -> str | None:
    return _opaque_session_ref(payload.get("session_id"))


def _cmd_provider_shadow(args) -> int:
    """Provider runtime stays fail-open; evidence admission stays fail-closed."""
    try:
        config = load_config(args.config)
        if not config.candidates.enabled:
            return 0
        payload = _read_stdin_json()
        state_dir = args.state_dir or default_state_dir()
        controller = _candidate_controller(config, state_dir)
        dispatch_shadow_lifecycle(
            controller,
            provider=args.provider,
            event=args.event,
            payload=payload,
            session_root=args.session_root,
        )
    except Exception:
        # This is the outer provider-hook boundary.  Evidence validation and
        # spool writes fail closed above; no local adapter failure may turn a
        # provider shutdown into an error or produce hook output.
        return 0
    return 0


def _candidate_controller(config: Config, state_dir: Path) -> LifecycleController:
    candidates = config.candidates
    return LifecycleController(
        CandidateSpool(state_dir, max_records=candidates.max_records),
        extractor_version=candidates.extractor_version,
        privacy_class=candidates.privacy_class,
        budget=BudgetSpec(
            max_tokens_per_job=candidates.max_tokens_per_job,
            max_jobs_per_session=candidates.max_jobs_per_session,
            max_jobs_per_day=candidates.max_jobs_per_day,
        ),
        lease_seconds=candidates.lease_seconds,
    )


def _payload_text(payload: dict, *names: str) -> str | None:
    for name in names:
        value = payload.get(name)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _cmd_candidate_collect(args) -> int:
    """Cheap E1 lifecycle hook; never runs a model or copies transcript text."""
    config = load_config(args.config)
    if not config.candidates.enabled:
        return 0

    payload = _read_stdin_json()
    explicit_state_id = (
        _extract_session_id({"session_id": args.session_id})
        if args.session_id
        else None
    )
    state_session_id = explicit_state_id or _extract_session_id(payload)
    state_path = state_path_for_session(state_session_id, args.state_dir)
    state = SessionState.load(state_path)
    session_ref = (
        _opaque_session_ref(args.session_id)
        or _extract_session_ref(payload)
        or "default"
    )

    source_anchor = payload.get("transcript_path")
    if not isinstance(source_anchor, str) or not source_anchor:
        source_anchor = None

    observed = {
        "messages_sent": state.messages_sent,
        "has_transcript_path": source_anchor is not None,
        "has_cwd": isinstance(payload.get("cwd"), str),
    }
    for counter_name in ("message_count", "turn_count"):
        counter = payload.get(counter_name)
        if isinstance(counter, int) and not isinstance(counter, bool) and counter >= 0:
            observed[counter_name] = counter
    state_dir = args.state_dir or default_state_dir()
    controller = _candidate_controller(config, state_dir)
    try:
        controller.handle(
            event=args.event,
            provider=args.provider,
            session_ref=session_ref,
            goal_ref=_payload_text(payload, "goal_id", "goal_ref"),
            boundary_epoch=_payload_text(payload, "boundary_epoch"),
            horizon_hash=_payload_text(
                payload, "horizon_hash", "last_event_hash", "transcript_hash"
            ),
            source_anchor=source_anchor,
            observed=observed,
        )
    except (OSError, ValueError, CorruptSpoolError):
        # Lifecycle hooks stay fail-open; durable corruption is observable via
        # candidate-extract and is never reset or overwritten here.
        return 0
    return 0


def _cmd_candidate_extract(args) -> int:
    """OFFLINE: listet gesammelte Kandidaten-Jobs und Legacy-Signale auf und
    verweist auf die Skills, die die eigentliche (teure) Extraktion
    ausfuehren -- ``skill-extractor`` (Chatverlauf -> wiederverwendbarer
    Skill) bzw. ``workflow-extract`` (Chatverlauf/Automations-Prompt ->
    Cron-/Loop-Automatisierung). Dieser Befehl fuehrt selbst KEINE
    Extraktion aus. Der Befehl ist immer rein lesend; die veraltete
    Kompatibilitätsoption ``--clear`` ist absichtlich ein No-op.
    """
    state_dir = args.state_dir or default_state_dir()
    spool = CandidateSpool(state_dir)
    queue_path = default_queue_path(state_dir)  # v1 read-compatibility only
    events = read_all(queue_path)
    records: list[dict] = []
    for job in spool.list_jobs():
        record = job.to_dict()
        try:
            record["receipt"] = spool.load_receipt(job.job_key).to_dict()
        except CorruptSpoolError:
            record["receipt"] = {"status": "corrupt"}
        records.append(record)
    records.extend({**event.to_dict(), "legacy": True} for event in events)

    if args.output_format == "json":
        print(json.dumps(records, ensure_ascii=False))
    else:
        if not records:
            print("Keine Kandidaten-Signale in der Warteschlange.")
        for record in records:
            if record.get("legacy"):
                continue
            receipt = record.get("receipt", {})
            anchor = record.get("source_anchor") or "(kein Transkript-Zeiger)"
            print(
                f"[{record['provider']}] {record['event']} session={record['session_ref']} "
                f"job={record['job_key']} status={receipt.get('status', 'unknown')} "
                f"anchor={anchor}"
            )
        for event in events:
            anchor = event.source_anchor or "(kein Transkript-Zeiger)"
            print(
                f"[legacy:{event.provider}] {event.event} session={event.session_ref} "
                f"anchor={anchor} observed={event.observed}"
            )
        if records:
            print()
            print(
                "Hinweis: reine Beobachtung, keine Bewertung. Fuer eine "
                "Skill-Ableitung 'skill-extractor' auf die genannten "
                "Transkript-Zeiger anwenden; fuer eine Automations-/"
                "Workflow-Ableitung 'workflow-extract'."
            )

    return 0


def _cmd_boot_context_lint(args) -> int:
    """Opt-in Diagnose; keine Hook-Registrierung und keine Mutation."""
    findings = [finding for path in args.paths for finding in lint_path(path)]
    if args.output_format == "json":
        print(json.dumps([finding.to_dict() for finding in findings], ensure_ascii=False))
    else:
        for finding in findings:
            location = finding.path
            if finding.line is not None:
                location += f":{finding.line}"
            if finding.field:
                location += f" [{finding.field}]"
            print(f"{finding.code} {location}: {finding.message}")
    return 1 if findings else 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
