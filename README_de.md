# WorkflowHooker

WorkflowHooker führt kleine, konfigurierbare Ablaufprüfungen an
Lebenszyklus-Ereignissen unterstützter Coding-Agenten aus. Das Modul arbeitet
lokal, nutzt `hook-master` zur Providerformatierung, ruft in den eingebauten
Prüfungen kein Netzwerk auf und bleibt stumm,
bis Prüfungen ausdrücklich aktiviert werden.

## Funktionen

- `closing_gate`: meldet einen aktiven Projekt-Lock oder uncommittete Änderungen.
- `drift_warning`: warnt, wenn Änderungen mehr Top-Level-Ordner als erlaubt
  betreffen.
- `scope_guard`: warnt, wenn die Zahl geänderter Dateien einen Grenzwert
  überschreitet.
- Zustandsquellen für Dateien, Git und Task-Pläne.
- Provider für Claude Code, Codex CLI, Kimi Code CLI, Git und manuelle Aufrufe.
- Sitzungsbudgets, Cooldowns und selbstständige Deaktivierung inaktiver Checks.
- Optionaler Aktionsguard: Ein technischer Guard-Ausfall sperrt nur die
  betroffene kritische Dateiaktion und bleibt unabhängig vom Hinweisbudget.
- Optionales Abschluss-Gate: genau eine belegte Nacharbeitsrunde, danach eine
  wahrheitsgemäße Restmeldung ohne weitere Stop-Schleife.

Drift- und Umfangsprüfung sind Heuristiken. Sie melden beobachtbare Zahlen,
behaupten aber weder Aufgabenverständnis noch einen erfolgten Testlauf.

## Installation und Konfiguration

WorkflowHooker benötigt Python 3.10 oder neuer.

```shell
python -m pip install .
```

Beispiel für `workflowhooker.toml`:

```toml
[mode]
checks = ["closing_gate"]
max_messages_per_session = 3
cooldown_minutes = 5
idle_disable_after = 20

[sources]
order = ["files", "git", "taskplan"]

[identity]
owner = "agent-oder-operator-id"
scope = "ticket-oder-aufgabenumfang"
host = "host-id"
target = "kanonische-ziel-id"

[action_guard]
enabled = false

[stop_gate]
enabled = false
max_rework_rounds = 1
```

Ohne Konfigurationsdatei ist die Check-Liste leer und das Modul gibt keine
Ablaufwarnungen aus.

```shell
python -m workflowhooker --config workflowhooker.toml check
python -m workflowhooker install-snippet --provider codex
python -m workflowhooker install-snippet --provider codex --variant action-guard
```

`install-snippet` gibt nur einen Konfigurationsbaustein aus. Das Modul verändert
keine Host-Konfiguration. Prüfe und übernimm den Baustein manuell.

Der Variant `action-guard` ist eine getrennte, ausdrückliche Aktivierung. Er
wertet nur Dateiaktionen aus, deren Ziel ohne Raten bestimmt werden kann:

| Provider | Belegter Aktionskanal | Rückgabe |
|---|---|---|
| Claude Code | Pfadfelder von `Edit`, `Write`, `MultiEdit`, `NotebookEdit` | `deny` oder natives `ask`; eine stille Freigabe lässt den normalen Berechtigungsweg des Hosts bestehen |
| Codex | kanonische Pfadzeilen im `apply_patch`-Input | `deny`; internes `ask` fällt sicher auf deny zurück, weil Codex PreToolUse derzeit kein `ask` umsetzt |
| Kimi Code | Pfadfelder von `WriteFile`, `StrReplaceFile`, `DeleteFile`, `MoveFile` | deny über Exit 2; internes `ask` fällt sicher auf deny zurück |

Die Adapter folgen den dokumentierten Verträgen der Provider und unterstellen
kein einheitliches Schema: [Claude-Code-Hooks](https://code.claude.com/docs/en/hooks),
[Codex-Hooks](https://developers.openai.com/codex/hooks) und
[Kimi-Code-Hooks](https://moonshotai.github.io/kimi-code/en/customization/hooks).

Nicht belegt wird eine zielgenaue Abdeckung von Bash, PowerShell, Unified Exec
oder beliebigen MCP-Argumenten. Solche Kanäle können Pfade in freiem oder
werkzeugspezifischem Inhalt transportieren, den diese Version nicht parst.
Auch der Host selbst kann abgestürzte oder abgelaufene Hooks fail-open
behandeln. Der Guard ist deshalb zusätzliche Absicherung, nicht die einzige
Sicherheitsgrenze.

Das interne Resultatmodell trennt Aktionsguard, Kontext, Stop und Nebenwirkung.
Der Belegzustand ist `clean`, `finding` oder `unknown`; anwendbare Entscheide
werden als `deny > ask > allow` zusammengeführt. Ein technischer Ausfall eines
aktivierten harten Guards ergibt `unknown + deny` ausschließlich für die
betroffene Aktion. Fehler eines Hinweises oder Kontextchecks sperren normale,
bereits autorisierte Arbeit nicht global.

Das optionale Abschluss-Gate korreliert Eigentümer, Scope, Host, Sitzung,
konfiguriertes Identitätsziel, kanonisches Projektziel, Lockart und
Git-Worktree. Sein Einmalrunden-State ist an diese vollständige Identität
gebunden. Ein Widerspruch zwischen State-Hash und Runtime-Identität wird als
unbelastbarer Reststatus gemeldet und kann keine neue Runde beginnen. Mit einem
pro State-Datei gesetzten Prozess-Lock wird außerdem die gesamte
Stop-Transaktion aus Laden, Auswerten und Speichern serialisiert. Lockfehler
oder Timeouts liefern denselben nichtblockierenden unbekannten Reststatus. Bei
einem Worktree prüft es auch den Hauptklon. Nur ein passend zugeordneter eigener
Befund kann genau eine Nacharbeitsrunde anfordern. Beim zweiten Stop, bei
`stop_hook_active`, fremdem Zustand oder beschädigtem State folgt nur eine
wahrheitsgemäße Restmeldung ohne erneuten Block. WorkflowHooker löscht keine
Locks, setzt keine Diffs zurück und räumt fremde Arbeit niemals automatisch auf.

## Claude-Code-Plugin

Dieses Repository ist zugleich das Claude-Code-Plugin `workflowhooker`. Es verdrahtet `hook-run` mit `Stop`,
`PreCompact` und `UserPromptSubmit` und bleibt still, solange das Projekt keine `workflowhooker.toml` enthält;
`/workflowhooker:gates init` legt eine Startdatei an (Abschluss-Gate mit genau einer Nacharbeitsrunde, ohne
`taskplan`-Quelle). Skill: `workflow-gates`.

```
/plugin marketplace add ellmos-ai/ellmos-plugins
/plugin install workflowhooker@ellmos
```

Datenfluss: nur lokale Dateien und `git`, nichts geht ins Netz. Voraussetzung ist Python 3.10+ (`py -3` unter Windows
oder `ELLMOS_PYTHON` setzen). Kompatibilität: Claude Code (Hooks, Befehl, Skill); Codex über das eigene Hook-Snippet
(`install-snippet --provider codex`). Die Plugin-Hooks lesen die Hook-Eingabe, `workflowhooker.toml`, Projektdateien
und `git status`; Gesprächstranskripte lesen sie nicht. Gespeichert werden nur Zähler und Hashes in
`~/.workflowhooker/` (änderbar über `WORKFLOWHOOKER_STATE_DIR`). Seine einzige Abhängigkeit `hook_master` bringt das
Plugin in `vendor/` mit, auf dem in `pyproject.toml` gepinnten Commit (`python tools/vendor_hook_master.py` erzeugt es
neu). Datenschutz und Support: <https://ellmos-ai.github.io/plugins.html>.

Beispiele:

1. `/workflowhooker:gates init`, dann Claude einen fehlschlagenden Test reparieren lassen. Hört Claude auf, obwohl
   das Projekt noch offene Arbeit zeigt, verlangt das Abschluss-Gate genau eine Nacharbeitsrunde mit dem gemessenen
   Beleg.
2. `"drift_warning"` in `[mode] checks` aufnehmen und einen Umbau über viele Ordner verlangen. Sobald mehr als
   `max_touched_dirs` Ordner der obersten Ebene Änderungen haben, meldet der Hook einen Drift-Hinweis mit ihren
   Namen.
3. `/workflowhooker:gates status` führt die konfigurierten Prüfungen einmal aus und meldet, welche Gates aktiv sind
   und was sie gerade finden.

## Datenschutz und Lizenz

WorkflowHooker liest lokalen Projektzustand und speichert je Sitzung eine kleine
JSON-Zustandsdatei. Projektdaten werden nicht übertragen. Sicherheitsmeldungen
beschreibt [SECURITY.md](SECURITY.md), die Herkunft
[PROVENANCE.md](PROVENANCE.md). Lizenz: MIT, siehe [LICENSE](LICENSE).

Guard-Fehlertexte sind fest formuliert und spiegeln weder Hook-Eingaben noch
Lock-Inhalte oder Exception-Texte. Der Abschlusszustand wird atomar ersetzt und
über eine kleine benachbarte `.lock`-Datei serialisiert. Rundenzahl und
Beleg-Fingerprint gehören zum Integritätsdigest, damit unterbrochene,
gleichzeitige oder nachträglich veränderte Zustände keine neue Einmalrunde
erzeugen.

## Verwandte Projekte

- [COMA](https://github.com/ellmos-ai/coma): Lokale dateibasierte Jobkoordination und Prozessstart/-status für Agenten; ergänzt WorkflowHookers Lebenszyklusprüfungen.

## Codex-Kontext nach Komprimierung

In einer ausdrücklich ausgewählten TOML-Datei aktivieren:

```toml
[compaction]
enabled = true
```

Der Hinweis nutzt Codex `SessionStart` mit `source = compact` und liefert
`hookSpecificOutput.additionalContext`. Er erinnert daran, dass erneut angezeigte
Nutzernachrichten historisch sein können, erledigte Schritte zu berücksichtigen
und die Aufgabe am aktuellen Stand fortzusetzen. Tatsächlich neue Nachrichten
und Korrekturen bleiben maßgeblich. Dies ist eine Orientierung für das Modell,
keine Garantie gegen Wiederholungsschleifen. `PostCompact` ist kein Ersatzkanal.

```shell
python -m workflowhooker install-snippet --provider codex --variant compaction
```

Das Ergebnis enthält nur `SessionStart`, Matcher `^compact$` und Timeout 10.
Vor dem Einmischen beide Befehlsfelder `command` und `commandWindows` auf den
installierten Interpreter und einen absoluten Konfigurationspfad setzen.
Shellgerechte Anführungszeichen verwenden. Unter PowerShell benötigt
`commandWindows` vor dem zitierten Programmpfad den Aufrufoperator `&`.
Das POSIX-Befehlsfeld `command` bleibt ohne diesen Operator. Beispiel für
`commandWindows`:

```powershell
& "C:/path/to/venv/Scripts/python.exe" -m workflowhooker --config "C:/path/to/workflowhooker.toml" hook-run SessionStart --provider codex
```

Genau einen passenden Handler registrieren. Eine ältere Registrierung desselben
SessionStart-Befehls ohne Matcher vorher durch die Variante `session-start`
ersetzen; deren Matcher schließt Komprimierung aus. `--existing` erzeugt nur ein
zusammengeführtes Hook-Snippet, keinen Ersatz für die gesamte Hostkonfiguration.
Andere Einstellungen und Vertrauensdaten erhalten. Die native Vertrauensprüfung
von Codex nicht umgehen.

Der Komprimierungspfad liest nur Eingabe und Konfiguration, keine Projektquellen
oder normalen Sitzungszustände. Ein ausgeschöpftes Hinweisbudget unterdrückt keine
spätere Komprimierungsmeldung. Ungültige Eingaben bleiben stumm. Zur Rücknahme die
Option ausschalten oder nur diese Registrierung entfernen. Manuelle und
automatische Komprimierung müssen mit der installierten Codex-Version separat
abgenommen werden; Unit-Tests ersetzen diesen Nachweis nicht.

## Gemeinsamer Kern und optionale Funktionen

Dieses öffentliche Repository ist der Entwicklungsort des Laufzeitkerns. Private
Installationen konsumieren einen festen öffentlichen Commit und halten ihre
Einstellungen, Daten und historischen Nachweise getrennt. Ein privates Python-Modul
ist nur für tatsächlich private ausführbare Funktionen erforderlich.

`goal` liest lokale Ziel-/Aufgabendateien; `loop-briefing` formatiert den Arbeitsstand.
`[injectors] goal = true` aktiviert PreCompact-Briefings. Policy- und Ortsinjektoren
werden getrennt aktiviert (`policy = true`, `location = true`) und benötigen
`install-snippet --variant session-start`. Registry-Inhalte bleiben lokal. Die
Scope-Ableitung unterstützt `.TOPICS/<scope>` und `_Local_DEV/repos/<name>`; für
andere Verzeichnisse wird kein Scope abgeleitet. Die optionalen Integrationen
`taskplan` und `source_resolver` sind nicht mitgeliefert.

`candidate-collect`, `candidate-extract`, `provider-shadow` und `boot-context-lint`
stellen lokale Lebenszyklus-, Extraktions- und Prüfwerkzeuge bereit. Details stehen
in der jeweiligen `--help`-Ausgabe. Kandidatenaufzeichnung erfordert
`[candidates] enabled = true`; kein Extraktor wird automatisch eingeplant.
Standardregistrierungen und bisherige Aktions-/Abschluss-Guards bleiben erhalten.
