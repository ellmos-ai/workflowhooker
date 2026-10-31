"""Config-Schema fuer WorkflowHooker (siehe README.md, Abschnitt "Modi").

Default: ``checks = []`` -- keiner der Checks ist ohne explizite Zustimmung
aktiv (README: "keiner ist per Default an, bis er sich bewaehrt hat").
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from . import _toml

VALID_CHECKS = ("closing_gate", "drift_warning", "scope_guard")


@dataclass
class ModeConfig:
    checks: list[str] = field(default_factory=list)
    max_messages_per_session: int = 3
    cooldown_minutes: int = 5
    # "Bedingung, die selten wahr ist" (README) generisch erzwungen: ein
    # Check, der ueber diese Zahl aufeinanderfolgender Auswertungen ohne
    # Fund bleibt, schaltet sich fuer den Rest der Sitzung selbst ab.
    idle_disable_after: int = 20


@dataclass
class DriftWarningConfig:
    max_touched_dirs: int = 4


@dataclass
class ScopeGuardConfig:
    max_changed_files: int = 15
    # PreToolUse-Pfadregeln (Edit/Write/MultiEdit/NotebookEdit), relativ zum
    # Projektordner: ``protected`` ist verboten, ``allowed`` (wenn gesetzt) ist
    # der einzige erlaubte Bereich. Muster: ``vendor/``, ``docs/**``, ``*.lock``.
    protected: list[str] = field(default_factory=list)
    allowed: list[str] = field(default_factory=list)


@dataclass
class ChecksConfig:
    drift_warning: DriftWarningConfig = field(default_factory=DriftWarningConfig)
    scope_guard: ScopeGuardConfig = field(default_factory=ScopeGuardConfig)


@dataclass
class ProvidersConfig:
    order: list[str] = field(
        default_factory=lambda: ["claude", "codex", "kimi", "git", "manual"]
    )
    claude_events: list[str] = field(
        default_factory=lambda: ["Stop", "PreCompact", "UserPromptSubmit"]
    )


@dataclass
class IdentityConfig:
    """Explizite Identitaet fuer Eigentums- und Scope-Abgleiche.

    Leere Werte werden nicht geraten. Ein aktiver Guard behandelt fehlende
    Autoritaet als ``ask``; ein Stop-Gate erzwingt ohne passenden Eigentums-
    beleg keine Nacharbeitsrunde.
    """

    owner: str = ""
    scope: str = ""
    host: str = ""
    target: str = ""


@dataclass
class ActionGuardConfig:
    enabled: bool = False


@dataclass
class StopGateConfig:
    enabled: bool = False
    # Woher der Eigentumsbeleg fuer offene Aenderungen kommt: "lock" = nur ein
    # eigener Lock mit voller Identitaet (streng, Default); "session" = zusaetzlich
    # die Sitzungs-Basislinie (Aenderungen seit dem ersten Hook dieser Sitzung).
    ownership: str = "lock"
    # E02=B ist absichtlich nicht als frei erhoehbares Schleifenbudget
    # modelliert: genau eine Nacharbeitsrunde ist der feste Vertrag.
    max_rework_rounds: int = 1


@dataclass
class PolicyInjectorConfig:
    """See ``injectors.PolicyInjector``. ``registry_path`` overrides the
    default ``~/.policy-registry/registry.json``; ``None`` keeps the
    default. ``max_entries`` bounds the token cost of one SessionStart
    message (README: "Injektor darf kein zweites CLAUDE.md werden")."""

    registry_path: str | None = None
    max_entries: int = 5


@dataclass
class LocationInjectorConfig:
    """See ``injectors.LocationInjector``. ``roles`` are source-resolver
    role names; the default set deliberately excludes ``policy.registry``
    (see LocationInjector docstring -- PolicyInjector covers that role)."""

    roles: list[str] = field(
        default_factory=lambda: [
            "resources.inventory",
            "decisions.ledger",
            "user.model",
            "memory.curated",
        ]
    )


@dataclass
class InjectorsConfig:
    """Opt-in context injectors.

    Checks remain silent by default, and so do the injectors.  ``goal`` is
    intended for the ``PreCompact`` hook; ``loop`` is exposed through the
    explicit briefing command and is never a scheduler.  ``policy`` and
    ``location`` are both intended for the ``SessionStart`` hook; their
    output is combined into a single message so two SessionStart injectors
    never spend two slots of ``mode.max_messages_per_session`` (README,
    Abschnitt "Session-Start-Hooker").
    """

    goal: bool = False
    loop: bool = False
    policy: bool = False
    location: bool = False
    policy_config: PolicyInjectorConfig = field(default_factory=PolicyInjectorConfig)
    location_config: LocationInjectorConfig = field(default_factory=LocationInjectorConfig)


@dataclass
class CandidatesConfig:
    """Opt-in lifecycle spool for later skill/workflow extraction.

    Bleibt wie alle anderen Mechanismen per Default aus: Der Live-Hook
    schreibt NUR, wenn ``enabled = true`` gesetzt ist -- selbst ein
    versehentlich verdrahteter Hook bleibt sonst ein stiller No-Op.
    ``max_records`` begrenzt terminale Job-/Receipt-Paare; aktive oder
    aufschiebbare Arbeit wird nie nur fuer ein Groessenlimit verworfen.
    """

    enabled: bool = False
    max_records: int = 500
    extractor_version: str = "workflow-extract@1.1.0+skill-extractor@1.3.1"
    privacy_class: str = "local-private"
    max_tokens_per_job: int = 12_000
    max_jobs_per_session: int = 3
    max_jobs_per_day: int = 20
    lease_seconds: int = 900


VALID_SOURCES = ("files", "git", "taskplan", "goal")


@dataclass
class SourcesConfig:
    """Welche Zustandsquellen den Projekt-Snapshot bilden.

    ``order`` waehlt die Quellen aus (Default: alle). Wer nur einen Teil will,
    listet ihn auf::

        [sources]
        order = ["git", "files"]

    Die Reihenfolge bestimmt, wer bei ``meta``-Kollisionen gewinnt (spaetere
    ueberschreiben fruehere); boolesche Felder werden ODER-verknuepft, die
    Zaehler per Maximum. Eine nicht verfuegbare Quelle wird uebersprungen --
    eine fehlende Quelle ist nie ein Fehler.
    """

    project_dir: str | None = None  # fuer files-StateSource (LOCK*.txt)
    git_dir: str | None = None  # fuer git-StateSource (Default: project_dir/cwd)
    order: list[str] = field(default_factory=lambda: ["files", "git", "taskplan"])


@dataclass
class CompactionConfig:
    enabled: bool = False


@dataclass
class Config:
    mode: ModeConfig = field(default_factory=ModeConfig)
    checks: ChecksConfig = field(default_factory=ChecksConfig)
    providers: ProvidersConfig = field(default_factory=ProvidersConfig)
    sources: SourcesConfig = field(default_factory=SourcesConfig)
    identity: IdentityConfig = field(default_factory=IdentityConfig)
    action_guard: ActionGuardConfig = field(default_factory=ActionGuardConfig)
    stop_gate: StopGateConfig = field(default_factory=StopGateConfig)
    injectors: InjectorsConfig = field(default_factory=InjectorsConfig)
    candidates: CandidatesConfig = field(default_factory=CandidatesConfig)
    compaction: CompactionConfig = field(default_factory=CompactionConfig)

    def validate(self) -> None:
        if type(self.compaction.enabled) is not bool:
            raise ValueError("[compaction].enabled muss ein Boolean sein")
        unknown = [c for c in self.mode.checks if c not in VALID_CHECKS]
        if unknown:
            raise ValueError(
                f"[mode].checks enthaelt unbekannte Checks {unknown}; gueltig sind {VALID_CHECKS}"
            )
        if self.mode.max_messages_per_session < 0:
            raise ValueError("[mode].max_messages_per_session darf nicht negativ sein")
        if self.mode.cooldown_minutes < 0:
            raise ValueError("[mode].cooldown_minutes darf nicht negativ sein")
        unknown_sources = [s for s in self.sources.order if s not in VALID_SOURCES]
        if unknown_sources:
            raise ValueError(
                f"[sources].order enthaelt unbekannte Quellen {unknown_sources}; "
                f"gueltig sind {VALID_SOURCES}"
            )
        if self.stop_gate.ownership not in ("lock", "session"):
            raise ValueError('[stop_gate].ownership muss "lock" oder "session" sein')
        for key in ("protected", "allowed"):
            value = getattr(self.checks.scope_guard, key)
            if not all(isinstance(item, str) and item.strip() for item in value):
                raise ValueError(f"[checks.scope_guard].{key} muss eine Liste nichtleerer Strings sein")
        if self.stop_gate.max_rework_rounds != 1:
            raise ValueError(
                "[stop_gate].max_rework_rounds muss fuer E02=B genau 1 sein"
            )
        if self.candidates.max_records < 0:
            raise ValueError("[candidates].max_records darf nicht negativ sein")
        if self.candidates.max_tokens_per_job < 0:
            raise ValueError("[candidates].max_tokens_per_job darf nicht negativ sein")
        if self.candidates.max_jobs_per_session < 0:
            raise ValueError("[candidates].max_jobs_per_session darf nicht negativ sein")
        if self.candidates.max_jobs_per_day < 0:
            raise ValueError("[candidates].max_jobs_per_day darf nicht negativ sein")
        if self.candidates.lease_seconds <= 0:
            raise ValueError("[candidates].lease_seconds muss positiv sein")
        if not self.candidates.extractor_version.strip():
            raise ValueError("[candidates].extractor_version darf nicht leer sein")
        if not self.candidates.privacy_class.strip():
            raise ValueError("[candidates].privacy_class darf nicht leer sein")
        if self.injectors.policy_config.max_entries < 0:
            raise ValueError("[injectors.policy_config].max_entries darf nicht negativ sein")


def default_config() -> Config:
    return Config()


def load_config(path: Path | str | None) -> Config:
    """Laedt eine ``workflowhooker.toml``. Fehlt die Datei, gelten die
    Defaults -- und die Defaults bedeuten ``checks = []``: das Modul ist
    ohne explizite Konfiguration voellig stumm.
    """
    if path is None:
        return default_config()

    path = Path(path)
    if not path.exists():
        return default_config()

    data = _toml.loads(path.read_text(encoding="utf-8"))
    config = _config_from_dict(data)
    config.validate()
    return config


def _config_from_dict(data: dict) -> Config:
    compaction_data = data.get("compaction", {})
    if not isinstance(compaction_data, dict):
        raise ValueError("[compaction] muss eine Tabelle sein")
    compaction = CompactionConfig(enabled=compaction_data.get("enabled", False))
    mode_data = data.get("mode", {})
    mode = ModeConfig(
        checks=list(mode_data.get("checks", [])),
        max_messages_per_session=mode_data.get(
            "max_messages_per_session", ModeConfig.max_messages_per_session
        ),
        cooldown_minutes=mode_data.get("cooldown_minutes", ModeConfig.cooldown_minutes),
        idle_disable_after=mode_data.get(
            "idle_disable_after", ModeConfig.idle_disable_after
        ),
    )

    checks_data = data.get("checks", {})
    drift_data = checks_data.get("drift_warning", {})
    scope_data = checks_data.get("scope_guard", {})
    checks = ChecksConfig(
        drift_warning=DriftWarningConfig(
            max_touched_dirs=drift_data.get(
                "max_touched_dirs", DriftWarningConfig.max_touched_dirs
            )
        ),
        scope_guard=ScopeGuardConfig(
            max_changed_files=scope_data.get(
                "max_changed_files", ScopeGuardConfig.max_changed_files
            ),
            protected=_string_list(scope_data.get("protected", []), "protected"),
            allowed=_string_list(scope_data.get("allowed", []), "allowed"),
        ),
    )

    providers_data = data.get("providers", {})
    claude_data = providers_data.get("claude", {})
    providers = ProvidersConfig(
        order=list(providers_data.get("order", ProvidersConfig().order)),
        claude_events=list(claude_data.get("events", ProvidersConfig().claude_events)),
    )

    injectors_data = data.get("injectors", {})
    # Accept both the compact boolean form (``goal = true``) and a small
    # ``{ enabled = true }`` table so hand-written configs remain forgiving.
    goal_value = injectors_data.get("goal", injectors_data.get("goal_injector", False))
    loop_value = injectors_data.get("loop", injectors_data.get("loop_injector", False))
    policy_value = injectors_data.get("policy", injectors_data.get("policy_injector", False))
    location_value = injectors_data.get("location", injectors_data.get("location_injector", False))

    policy_config_data = injectors_data.get("policy_config", {})
    policy_config = PolicyInjectorConfig(
        registry_path=policy_config_data.get("registry_path") or None,
        max_entries=policy_config_data.get("max_entries", PolicyInjectorConfig.max_entries),
    )

    location_config_data = injectors_data.get("location_config", {})
    location_config = LocationInjectorConfig(
        roles=[
            str(role)
            for role in location_config_data.get("roles", LocationInjectorConfig().roles)
            if str(role)
        ],
    )

    injectors = InjectorsConfig(
        goal=_enabled_value(goal_value),
        loop=_enabled_value(loop_value),
        policy=_enabled_value(policy_value),
        location=_enabled_value(location_value),
        policy_config=policy_config,
        location_config=location_config,
    )

    sources_data = data.get("sources", {})
    sources = SourcesConfig(
        project_dir=sources_data.get("project_dir") or None,
        git_dir=sources_data.get("git_dir") or None,
        order=[
            str(name) for name in sources_data.get("order", SourcesConfig().order) if str(name)
        ],
    )

    candidates_data = data.get("candidates", {})
    candidates = CandidatesConfig(
        enabled=_enabled_value(candidates_data.get("enabled", False)),
        max_records=candidates_data.get("max_records", CandidatesConfig.max_records),
        extractor_version=str(candidates_data.get("extractor_version", CandidatesConfig.extractor_version)),
        privacy_class=str(candidates_data.get("privacy_class", CandidatesConfig.privacy_class)),
        max_tokens_per_job=candidates_data.get("max_tokens_per_job", CandidatesConfig.max_tokens_per_job),
        max_jobs_per_session=candidates_data.get("max_jobs_per_session", CandidatesConfig.max_jobs_per_session),
        max_jobs_per_day=candidates_data.get("max_jobs_per_day", CandidatesConfig.max_jobs_per_day),
        lease_seconds=candidates_data.get("lease_seconds", CandidatesConfig.lease_seconds),
    )

    identity_data = data.get("identity", {})
    identity = IdentityConfig(
        owner=str(identity_data.get("owner", "")),
        scope=str(identity_data.get("scope", "")),
        host=str(identity_data.get("host", "")),
        target=str(identity_data.get("target", "")),
    )
    action_guard_data = data.get("action_guard", {})
    action_guard = ActionGuardConfig(
        enabled=bool(action_guard_data.get("enabled", False))
    )
    stop_gate_data = data.get("stop_gate", {})
    stop_gate = StopGateConfig(
        enabled=bool(stop_gate_data.get("enabled", False)),
        ownership=str(stop_gate_data.get("ownership", "lock")),
        max_rework_rounds=int(stop_gate_data.get("max_rework_rounds", 1)),
    )

    return Config(
        mode=mode,
        checks=checks,
        providers=providers,
        sources=sources,
        injectors=injectors,
        candidates=candidates,
        identity=identity,
        action_guard=action_guard,
        stop_gate=stop_gate,
        compaction=compaction,
    )


def _string_list(value: object, key: str) -> list[str]:
    if not isinstance(value, list):
        raise ValueError(f"[checks.scope_guard].{key} muss eine Liste sein")
    return list(value)


def _enabled_value(value: object) -> bool:
    if isinstance(value, dict):
        return bool(value.get("enabled", False))
    return bool(value)
