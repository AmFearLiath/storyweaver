# 🕸️ Storyweaver — KI Game Master

> Vollständig lokales Browser-Textadventure mit einem lokalen LLM als Game Master. Keine Cloud. Keine API-Kosten. Komplett offline spielbar.

![Python](https://img.shields.io/badge/Python-3.10%2B-blue?logo=python)
![FastAPI](https://img.shields.io/badge/FastAPI-0.111%2B-009688?logo=fastapi)
![Ollama](https://img.shields.io/badge/Ollama-lokal-black)
![License](https://img.shields.io/badge/license-Apache%202.0-green)

---

## 📖 Über das Projekt

Storyweaver ist ein browserbasiertes Textadventure, bei dem ein lokal laufendes LLM (via [Ollama](https://ollama.ai)) die Rolle des Game Masters übernimmt. Die KI generiert Szenen, Entscheidungsoptionen und einen dynamischen Weltzustand — inklusive Gegenständen, Zugangscodes, Hindernissen, Charakterskills, Fraktionen und persistentem Gedächtnis.

**Kein Internetzugang nötig. Alle Daten bleiben lokal.**

---

## ✨ Features

### Kern-System

| Feature | Beschreibung |
|---------|-------------|
| 🤖 **5-Rollen-LLM-Pipeline** | Storyteller · Director · Cataloger · Choicemaker · Interpreter — jede Rolle kann ein eigenes Modell nutzen |
| 🌍 **Dynamischer Weltzustand** | KI verfolgt Gegenstände, Codes, Hindernisse & Fallen automatisch |
| ⚠️ **Hindernisse & Fallen** | Aktive Gefahren mit Rückschlägen (low / medium / high / lethal) |
| 🔑 **Zugangscodes** | Codes werden entdeckt, gespeichert und per Klick eingesetzt |
| 🎭 **Mehrere Geschichten** | Beliebig viele parallele Stories mit eigenen Charakteren & Welten |
| ⚙️ **Live-Konfiguration** | Alle LLM-Parameter live im Browser anpassbar |
| 🌐 **Mehrsprachig** | Ausgabesprache frei wählbar (Deutsch, Englisch, Französisch, …) |
| 🔐 **Benutzerverwaltung** | Login-System mit Admin-Panel |

### Charakter-System (v5)

| Feature | Beschreibung |
|---------|-------------|
| 🎯 **Skills-System** | Frei definierbare Fertigkeiten (0–100) pro Charakter; unsichtbare Würfelmechanik beeinflusst Ergebnisse |
| 🎒 **Charakter-Inventar** | Pro Charakter trackt die KI das Inventar; Transfer zwischen Chars & Ablegen über die Sidebar |
| 🎲 **Würfelmechanik** | Verdeckte Würfelwürfe (d20 + Skill-Bonus) — kein Zahlen-Spam, Ergebnis fließt unsichtbar ins Storytelling ein |
| 🖼️ **Charakter-Avatare** | Bild-Upload pro Charakter, Anzeige in der Charakterliste |
| 📋 **JSON-Export** | Charakter-Daten als JSON exportieren (z.B. für ChatGPT-GPTs) |

### Director-System (v5)

Der Director ist eine eigene LLM-Rolle die nach jeder Spieleraktion im Hintergrund eine **narrative Direktive** setzt — bevor der Storyteller die Szene schreibt. Er steuert:

- Pacing (Eskalation, Ruhephase, Wendepunkt, Klimax …)
- Anti-Repetition: erkennt wenn Szenen sich wiederholen und lenkt aktiv um
- Obstacle-Decay: Hindernisse ohne Interaktion verblassen nach mehreren Szenen automatisch

### Memory Vector DB (v6)

| Feature | Beschreibung |
|---------|-------------|
| 🧠 **Persistentes Gedächtnis** | Nach jeder Szene wird eine Erinnerung gespeichert |
| 🔍 **Semantischer Recall** | Vor jeder neuen Szene holt das System die thematisch passendsten Erinnerungen per Cosinus-Ähnlichkeit |
| 🔢 **Hash-Embedding-Fallback** | Deterministisches 256-d Char-Bigram + Word-MD5 Embedding — kein Modell nötig |
| 🤖 **Ollama-Embedding** | Optional: beliebiges Ollama-Embedding-Modell für bessere semantische Qualität |
| 🛠 **Memory-Manager** | Erinnerungen im Browser einsehen, suchen, manuell hinzufügen oder löschen |

### Fraktionssystem (v7)

| Feature | Beschreibung |
|---------|-------------|
| 🛡 **Fraktionen & Gruppen** | Gilden, Familien, Organisationen — jede mit Haltung zum Spieler (−100 … +100) |
| ↔️ **Fraktions-Relationen** | Haltungen zwischen Fraktionen untereinander (Allies, Feinde, …) |
| ⚡ **Automatische Anpassung** | Der Cataloger erkennt nach jeder Szene Haltungsänderungen und passt Werte an (+/−5 pro Szene) |
| 📊 **Lesbare Labels** | todfeindlich · feindselig · misstrauisch · neutral · wohlwollend · freundlich · treu ergeben |
| 🎭 **System-Prompt-Integration** | Fraktionen fließen automatisch in den KI-Kontext ein — die KI weiß wer Freund oder Feind ist |

### Save-Slots & Branching (v8)

| Feature | Beschreibung |
|---------|-------------|
| 💾 **Save-Slots** | Story-Snapshot (Welt, Charaktere, Events, Fraktionen, Erinnerungen) mit Name & Zeitstempel |
| ↻ **Wiederherstellen** | Story auf einen früheren Zustand zurücksetzen |
| 🌿 **Branching** | Aus einem Savepoint eine neue parallele Story erzeugen — die ursprüngliche bleibt unverändert |
| 🔢 **Auto-Namen** | Automatische Benennung „Auto-Save Szene N – TT.MM. HH:MM" falls kein Name angegeben |

---

## 🚀 Schnellstart

### Voraussetzungen

- **Python 3.10+** — [python.org](https://www.python.org/downloads/)
- **Ollama** — [ollama.ai](https://ollama.ai)

### 1. Repository klonen

```bash
git clone https://github.com/AmFearLiath/storyweaver.git
cd storyweaver
```

### 2. Ollama starten

```bash
# In einem separaten Terminal:
ollama serve

# Modell laden (einmalig, ~4–8 GB Download):
ollama pull llama3
```

> Empfohlene Modelle: `llama3`, `mistral`, `gemma3`, `phi4`

### 3. Storyweaver starten

**Windows (Doppelklick oder Terminal):**
```
start.bat
```

**Linux / macOS:**
```bash
pip install -r requirements.txt
python run.py
```

### 4. Browser öffnen

```
http://localhost:8000
```

**Ersten Admin-Account anlegen:**
```bash
python tools/create_admin.py
```

---

## 📁 Projektstruktur

```
storyweaver/
├── backend/
│   ├── __init__.py
│   ├── main.py          # FastAPI-App, alle API-Endpunkte (69 Routen)
│   ├── database.py      # SQLite-Schema, alle DB-Funktionen
│   ├── llm.py           # 5-Rollen-Pipeline, Prompt-Builder, Faction-Cataloger
│   └── memory.py        # Vector-Embedding, Hash-Fallback, Recall-Logik
├── frontend/
│   ├── index.html       # Haupt-Spiel-UI
│   ├── landing.html     # Login- & Registrierungsseite
│   ├── admin.html       # Admin-Panel
│   ├── style.css        # Dark-Fantasy-Theme
│   ├── app.js           # Frontend-Logik (Vanilla JS, ~2300 Zeilen)
│   └── assets/
│       ├── img/         # Logo, Hintergrundbild
│       └── avatars/     # Hochgeladene Charakter-Avatare (nicht im Repo)
├── logs/                # Laufzeit-Logs (nicht im Repo)
├── tools/
│   └── create_admin.py  # Admin-Account anlegen
├── requirements.txt
├── run.py               # Server-Einstiegspunkt
├── start.bat            # Windows-Starter
└── README.md
```

> **Nicht im Repository enthalten** (via `.gitignore` ausgeschlossen):
> - `backend/adventure.db` — SQLite-Datenbank (wird beim ersten Start automatisch erstellt)
> - `frontend/assets/avatars/*` — hochgeladene Nutzer-Avatare
> - `logs/*.log` — Laufzeit-Logs

---

## ⚙️ Konfiguration

Alle Einstellungen werden im Browser vorgenommen — kein manuelles Editieren von Konfigurationsdateien nötig.

### LLM-Parameter

| Parameter | Bereich | Effekt |
|-----------|---------|--------|
| Modell | beliebig | Ollama-Modell, z.B. `llama3` |
| Rollen-Modelle | je Rolle optional | Storyteller, Director, Cataloger, Choicemaker, Interpreter können eigene (kleinere/schnellere) Modelle nutzen |
| Temperatur | 0.1 – 1.5 | Kreativität der Antworten |
| Top-P | 0.1 – 1.0 | Vielfalt der Wortwahl |
| Repeat Penalty | 1.0 – 2.0 | Verhindert Wiederholungen |
| Kontextfenster | 1 024 – 32 768 Token | Größe des LLM-Kontextes |
| Max. Ausgabe-Token | 200 – 3 200 | Maximale Länge pro Szene |
| Spielgedächtnis | 1 – 10 Szenen | Wie viele vergangene Szenen im Kontext bleiben |
| Embedding-Modell | beliebig oder leer | Ollama-Modell für Erinnerungs-Embeddings (leer = Hash-Fallback) |
| Memory Top-K | 0 – 10 | Wie viele semantisch ähnliche Erinnerungen pro Szene eingeblendet werden |

### Ausgabesprache

Frei einstellbar — die KI antwortet in der gewählten Sprache (Deutsch, Englisch, Französisch, Spanisch, …).

---

## 🤖 Empfohlene Ollama-Modelle

| Modell | VRAM / RAM | Qualität | Geschwindigkeit | Empfehlung |
|--------|-----------|----------|----------------|-----------|
| `llama3` | ~8 GB | ⭐⭐⭐⭐ | schnell | Hauptmodell |
| `mistral` | ~8 GB | ⭐⭐⭐⭐ | schnell | Hauptmodell |
| `gemma3:12b` | ~12 GB | ⭐⭐⭐⭐⭐ | mittel | Hauptmodell |
| `phi4` | ~10 GB | ⭐⭐⭐⭐ | mittel | Hauptmodell |
| `llama3:70b` | ~40 GB | ⭐⭐⭐⭐⭐ | langsam | Beste Qualität |
| `nomic-embed-text` | ~1 GB | ⭐⭐⭐⭐ | sehr schnell | Embedding-Modell |

> **Tipp:** Für die Hilfsrollen (Director, Cataloger, Choicemaker) eignen sich kleine, schnelle Modelle wie `phi4` oder `mistral` — das spart deutlich Zeit pro Szene.

---

## 🎮 Spielsystem im Detail

### 5-Rollen-LLM-Pipeline

Jede Spieleraktion durchläuft eine mehrstufige Pipeline:

```
Spieleraktion
    │
    ├─ 1. Interpreter  — versteht die Aktion, erkennt den Intent
    ├─ 2. Director     — setzt eine narrative Direktive (Pacing, Wendepunkt, …)
    ├─ 3. Recall       — holt thematisch passende Erinnerungen aus der Vektor-DB
    ├─ 4. Storyteller  — schreibt die Szene (mit Direktive + Erinnerungen als Kontext)
    ├─ 5. Cataloger    — aktualisiert Weltzustand, Inventare, Fraktionshaltungen
    └─ 6. Choicemaker  — generiert die nächsten Handlungsoptionen
```

Alle Schritte laufen parallel wo möglich (Cataloger & Choicemaker gleichzeitig nach dem Storyteller).

### Weltzustand

| Typ | Symbol | Beschreibung |
|-----|--------|-------------|
| `item` | 📦 | Physische Gegenstände, können Charakteren zugewiesen werden |
| `code` | 🔑 | Zahlenkombinationen, Passwörter — per Klick einfügbar |
| `obstacle` | ⚠️ | Fallen, Gefahren, Bewachung — mit Gefahrenlevel low/medium/high/lethal |

### Hindernisse & Fallen

| Gefahrenlevel | Symbol | Konsequenz |
|---------------|--------|-----------|
| `low` | ⚠️ | Verlangsamung, kleiner Umweg |
| `medium` | 🔶 | Verletzung, Ressourcenverlust |
| `high` | 🔴 | Schwere Konsequenz |
| `lethal` | ☠️ | Lebensgefährlich |

Status-Übergänge: `active` → `triggered` → `overcome` / `avoided`
Hindernisse die lange ignoriert werden, verblassen durch **Obstacle-Decay** automatisch.

### Skills & Würfelmechanik

- Jeder Charakter hat frei definierbare Skills (0–100, z.B. „Nahkampf", „Schleichen", „Überredung")
- Die KI erkennt welcher Skill bei einer Aktion relevant ist
- Ein verdeckter d20-Würfelwurf wird addiert: `(Skill / 10) + d20`
- Das Ergebnis fließt **unsichtbar** ins Storytelling ein — kein Zahlen-Spam, echtes narratives Feedback

### Memory Vector DB

Embeddings werden als kompakter BLOB gespeichert. Ohne Ollama-Embedding-Modell greift ein deterministischer **Hash-Embedding-Fallback** (256-dimensional, Char-Bigram + Word-MD5, L2-normalisiert) — die Ähnlichkeitssuche funktioniert auch komplett ohne zusätzliche Modelle.

---

## 🔧 API-Übersicht (Auswahl)

| Methode | Pfad | Beschreibung |
|---------|------|-------------|
| `POST` | `/api/auth/login` | Login |
| `GET` | `/api/stories` | Alle Stories |
| `POST` | `/api/stories` | Neue Story anlegen |
| `GET/POST` | `/api/stories/{id}/config` | Story-Konfiguration lesen/schreiben |
| `GET/POST/DELETE` | `/api/characters` | Charakter CRUD |
| `POST` | `/api/game/action` | Spieleraktion → KI-Szene (Streaming) |
| `GET` | `/api/game/state/{story_id}` | Aktueller Spielzustand |
| `GET/POST/DELETE` | `/api/factions/{story_id}` | Fraktionen CRUD |
| `GET/POST/DELETE` | `/api/memories/{story_id}` | Erinnerungen CRUD |
| `POST` | `/api/memories/search` | Semantische Erinnerungssuche |
| `GET/POST/DELETE` | `/api/saves/{story_id}` | Save-Slots CRUD |
| `POST` | `/api/saves/{slot_id}/restore` | Spielstand wiederherstellen |
| `POST` | `/api/saves/{slot_id}/branch` | Branch-Story aus Savepoint erzeugen |
| `GET` | `/api/ollama/status` | Ollama-Verbindungsstatus + Modellliste |

---

## 🛠️ Entwicklung

```bash
# Entwicklungsserver mit Auto-Reload (nur backend + frontend beobachten):
uvicorn backend.main:app --reload --reload-dir backend --reload-dir frontend --host 127.0.0.1 --port 8000
```

> **Wichtig:** `--reload-dir` auf `backend` und `frontend` beschränken, sonst reagiert der Reloader auf alle Dateien im Root-Verzeichnis und startet sich in einer Schleife neu.

Abhängigkeiten installieren:

```bash
pip install -r requirements.txt
```

---

## 📄 Lizenz

MIT License — siehe [LICENSE](LICENSE)

---

## 🙏 Credits

- [FastAPI](https://fastapi.tiangolo.com/) — Backend-Framework
- [Ollama](https://ollama.ai) — Lokale LLM-Inferenz
- [SQLite](https://sqlite.org/) — Embedded Datenbank

