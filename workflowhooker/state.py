"""Session-Zustand: Meldungsbudget + Cooldown + Check-Laufzeitdaten.

Wie beim Schwestermodul MemoryHooker eine direkte Umsetzung einer
zurueckhaltenden Hook-Regel: harte Obergrenze
(``max_messages_per_session``) + Cooldown, plus je Check ``usage_count`` und
die Auto-Deaktivierung nach dem MetaFeedbackInjector-Muster aus README/
ROADMAP ("ein Check, der nichts mehr findet, schaltet sich selbst ab").
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
import time
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from pathlib import Path


def default_state_dir() -> Path:
    return Path(
        os.environ.get("WORKFLOWHOOKER_STATE_DIR", Path.home() / ".workflowhooker")
    )


def state_path_for_session(
    session_id: str | None, state_dir: Path | None = None
) -> Path:
    directory = state_dir or default_state_dir()
    name = f"session-{session_id}.json" if session_id else "session-default.json"
    return directory / name


@dataclass
class CheckRuntime:
    usage_count: int = 0
    idle_streak: int = 0
    disabled: bool = False


@dataclass
class StopGateRuntime:
    rounds_requested: int = 0
    evidence_fingerprint: str = ""
    owner: str = ""
    scope: str = ""
    host: str = ""
    session: str = ""
    target: str = ""
    identity_target: str = ""
    integrity_digest: str = ""


class StopRuntimeIntegrityError(ValueError):
    """Persistierter Stop-State passt nicht zu seinem Identitaets-Hash."""


def _stop_gate_key(runtime: StopGateRuntime) -> str:
    identity = (
        runtime.target,
        runtime.owner,
        runtime.scope,
        runtime.host,
        runtime.session,
        runtime.identity_target,
    )
    encoded = json.dumps(identity, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _stop_runtime_digest(runtime: StopGateRuntime) -> str:
    evidence = (
        runtime.target,
        runtime.owner,
        runtime.scope,
        runtime.host,
        runtime.session,
        runtime.identity_target,
        runtime.rounds_requested,
        runtime.evidence_fingerprint,
    )
    encoded = json.dumps(evidence, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _validate_stop_gates(stop_gates: dict[str, StopGateRuntime]) -> None:
    for key, runtime in stop_gates.items():
        if key != _stop_gate_key(
            runtime
        ) or runtime.integrity_digest != _stop_runtime_digest(runtime):
            raise StopRuntimeIntegrityError("stop-runtime-identity-mismatch")


def _baseline_from(value: object) -> dict[str, str] | None:
    if value is None:
        return None
    if not isinstance(value, dict) or not all(
        isinstance(k, str) and isinstance(v, str) for k, v in value.items()
    ):
        raise TypeError("baseline-malformed")
    return value


class StateTransactionTimeout(TimeoutError):
    """Der State konnte nicht innerhalb des Hook-Budgets serialisiert werden."""


def _try_file_lock(stream) -> None:
    stream.seek(0)
    if os.name == "nt":
        import msvcrt

        msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
    else:
        import fcntl

        fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)


def _unlock_file(stream) -> None:
    stream.seek(0)
    if os.name == "nt":
        import msvcrt

        msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
    else:
        import fcntl

        fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


@contextmanager
def state_transaction(path: Path, timeout_seconds: float | None = None):
    """Serialisiert Load/Evaluate/Save fuer genau eine State-Datei."""

    if timeout_seconds is None:
        try:
            timeout_seconds = float(
                os.environ.get("WORKFLOWHOOKER_STATE_LOCK_TIMEOUT", "2.0")
            )
        except ValueError:
            timeout_seconds = 2.0
    timeout_seconds = max(0.0, timeout_seconds)
    path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = path.with_name(f"{path.name}.lock")
    stream = lock_path.open("a+b")
    if stream.seek(0, os.SEEK_END) == 0:
        stream.write(b"\0")
        stream.flush()
    deadline = time.monotonic() + timeout_seconds
    acquired = False
    try:
        while True:
            try:
                _try_file_lock(stream)
                acquired = True
                break
            except OSError as exc:
                if time.monotonic() >= deadline:
                    raise StateTransactionTimeout("state-lock-timeout") from exc
                time.sleep(0.02)
        yield
    finally:
        if acquired:
            _unlock_file(stream)
        stream.close()


@dataclass
class SessionState:
    messages_sent: int = 0
    # None = "noch keine Meldung in dieser Sitzung" -- kein 0.0-Sentinel
    # (siehe MemoryHooker state.py fuer den dort gefangenen Bug mit genau
    # diesem Muster).
    last_message_ts: float | None = None
    checks: dict[str, CheckRuntime] = field(default_factory=dict)
    stop_gates: dict[str, StopGateRuntime] = field(default_factory=dict)
    candidate_enqueued: bool = False
    # Pfad -> Hash der beim ersten Sitzungs-Hook uncommitteten Dateien; None = nie erfasst.
    baseline: dict[str, str] | None = None

    @classmethod
    def load(cls, path: Path) -> "SessionState":
        state, _ = cls.load_checked(path)
        return state

    @classmethod
    def load_checked(cls, path: Path) -> tuple["SessionState", bool]:
        """Liefert State plus Belastbarkeitsflag.

        Ein fehlender State ist ein sauberer Sitzungsstart. Ein vorhandener,
        aber beschädigter State ist ``unknown`` und darf insbesondere keinen
        weiteren Stop-Nachstoß auslösen.
        """
        if not path.exists():
            return cls(), True
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                raise ValueError("state-not-object")
        except (OSError, UnicodeError, json.JSONDecodeError, ValueError):
            return cls(), False

        try:
            checks_data = data.get("checks", {})
            if not isinstance(checks_data, dict):
                raise TypeError("checks-not-object")
            checks = {
                name: CheckRuntime(**runtime_data)
                for name, runtime_data in checks_data.items()
            }
            stop_data = data.get("stop_gates", {})
            if not isinstance(stop_data, dict):
                raise TypeError("stop-gates-not-object")
            stop_gates = {
                key: StopGateRuntime(**runtime_data)
                for key, runtime_data in stop_data.items()
            }
            _validate_stop_gates(stop_gates)
            state = cls(
                messages_sent=data.get("messages_sent", 0),
                candidate_enqueued=bool(data.get("candidate_enqueued", False)),
                last_message_ts=data.get("last_message_ts"),
                checks=checks,
                stop_gates=stop_gates,
                baseline=_baseline_from(data.get("baseline")),
            )
        except (TypeError, ValueError):
            return cls(), False
        return state, True

    def save(self, path: Path) -> None:
        _validate_stop_gates(self.stop_gates)
        path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "messages_sent": self.messages_sent,
            "candidate_enqueued": self.candidate_enqueued,
            "last_message_ts": self.last_message_ts,
            "baseline": self.baseline,
            "checks": {name: asdict(runtime) for name, runtime in self.checks.items()},
            "stop_gates": {
                key: asdict(runtime) for key, runtime in self.stop_gates.items()
            },
        }
        # Atomarer Austausch: Ein Prozessabbruch darf nicht aus einem bereits
        # belegten Nachstoß einen beschädigten State und damit eine Schleife
        # machen.
        handle, temporary = tempfile.mkstemp(
            prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
        )
        try:
            with os.fdopen(handle, "w", encoding="utf-8", newline="") as stream:
                json.dump(data, stream, ensure_ascii=False)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
        except Exception:
            try:
                os.unlink(temporary)
            except OSError:
                pass
            raise

    def runtime_for(self, check_name: str) -> CheckRuntime:
        return self.checks.setdefault(check_name, CheckRuntime())

    def stop_runtime_for(
        self,
        target_key: str,
        *,
        owner: str = "",
        scope: str = "",
        host: str = "",
        session: str = "",
        identity_target: str = "",
    ) -> StopGateRuntime:
        # Lokale Pfade bleiben aus dem JSON-Schlüssel heraus; der kanonische
        # Zielwert und die vollstaendige Identitaet liegen separat im Beleg.
        _validate_stop_gates(self.stop_gates)
        candidate = StopGateRuntime(
            target=target_key,
            owner=owner,
            scope=scope,
            host=host,
            session=session,
            identity_target=identity_target,
        )
        key = _stop_gate_key(candidate)
        runtime = self.stop_gates.get(key)
        if runtime is not None:
            return runtime
        candidate.integrity_digest = _stop_runtime_digest(candidate)
        self.stop_gates[key] = candidate
        return candidate

    def seal_stop_runtime(self, runtime: StopGateRuntime) -> None:
        runtime.integrity_digest = _stop_runtime_digest(runtime)
