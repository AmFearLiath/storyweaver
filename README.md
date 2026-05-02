# 🕸️ Storyweaver — KI Game Master

> Vollständig lokales Browser-Textadventure mit einem lokalen LLM als Game Master. Keine Cloud. Keine API-Kosten. Komplett offline spielbar.

![Python](https://img.shields.io/badge/Python-3.10%2B-blue?logo=python)
![FastAPI](https://img.shields.io/badge/FastAPI-0.111%2B-009688?logo=fastapi)
![Ollama](https://img.shields.io/badge/Ollama-lokal-black)
![License](https://img.shields.io/badge/Lizenz-MIT-green)

---

## 📖 Über das Projekt

Storyweaver ist ein browserbasiertes Textadventure, bei dem ein lokal laufendes LLM (via [Ollama](https://ollama.ai)) die Rolle des Game Masters übernimmt. Die KI generiert Szenen, Entscheidungsoptionen und einen dynamischen Weltzustand — inklusive Gegenständen, Zugangscodes, Hindernissen und Fallen.

**Kein Internetzugang nötig. Alle Daten bleiben lokal.**

---

## ✨ Features

| Feature | Beschreibung |
|---------|-------------|
| 🤖 **KI Game Master** | Lokales LLM via Ollama — vollständig offline |
| 🌍 **Dynamischer Weltzustand** | KI verfolgt Gegenstände, Codes, Hindernisse & Fallen automatisch |
| ⚠️ **Hindernisse & Fallen** | Aktive Gefahren mit Rückschlägen (low / medium / high / lethal) |
| 🔑 **Zugangscodes** | Codes werden entdeckt, gespeichert und können eingesetzt werden |
| 🖼️ **Charakter-Avatare** | Bild-Upload pro Charakter, Anzeige in der Charakterliste |
| 📋 **JSON-Export** | Charakter-Daten als JSON exportieren (z.B. für ChatGPT-GPTs) |
| 🎭 **Mehrere Geschichten** | Beliebig viele parallele Stories mit eigenen Charakteren & Welten |
| ⚙️ **Live-Konfiguration** | LLM-Parameter (Temperatur, Top-P, Penalty) live anpassbar |
| 🌐 **Mehrsprachig** | Ausgabesprache frei wählbar (Deutsch, Englisch, Französisch, …) |
| 🎨 **Dark Fantasy Theme** | Vollständig responsives Browser-UI ohne externe Abhängigkeiten |
| 🔐 **Benutzerverwaltung** | Login-System mit Admin-Panel |
| 📝 **Freie Aktionseingabe** | Eigener Text gleichwertig zu den KI-Optionen |

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
│   ├── main.py          # FastAPI-App, alle API-Endpunkte
│   ├── database.py      # SQLite-Schema, Datenbankfunktionen
│   └── llm.py           # Ollama-Integration, Prompt-Builder, Weltzustand
├── frontend/
│   ├── index.html       # Haupt-Spiel-UI
│   ├── landing.html     # Login- & Registrierungsseite
│   ├── admin.html       # Admin-Panel
│   ├── style.css        # Dark-Fantasy-Theme
│   ├── app.js           # Frontend-Logik (Vanilla JS)
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
| Modell | beliebig | Ollama-Modell-Name, z.B. `llama3` |
| Temperatur | 0.1 – 1.5 | Kreativität der Antworten |
| Top-P | 0.1 – 1.0 | Vielfalt der Wortwahl |
| Repeat Penalty | 1.0 – 2.0 | Verhindert Wiederholungen |
| Detailgrad | niedrig / mittel / hoch | Länge der generierten Szenen |

### Ausgabesprache

Frei einstellbar — die KI antwortet in der gewählten Sprache (Deutsch, Englisch, Französisch, Spanisch, …).

---

## 🤖 Empfohlene Ollama-Modelle

| Modell | VRAM / RAM | Qualität | Geschwindigkeit |
|--------|-----------|----------|----------------|
| `llama3` | ~8 GB | ⭐⭐⭐⭐ | schnell |
| `mistral` | ~8 GB | ⭐⭐⭐⭐ | schnell |
| `gemma3:12b` | ~12 GB | ⭐⭐⭐⭐⭐ | mittel |
| `phi4` | ~10 GB | ⭐⭐⭐⭐ | mittel |
| `llama3:70b` | ~40 GB | ⭐⭐⭐⭐⭐ | langsam |

---

## 🎮 Spielsystem

### Weltzustand

Die KI verwaltet automatisch einen strukturierten Weltzustand, der nach jeder Szene aktualisiert wird:

- **Gegenstände** (`type=item`) — physische Objekte, Schlüssel, Ausrüstung
- **Zugangscodes** (`type=code`) — Zahlenkombinationen, Passwörter, PINs; im UI per Klick in die Aktionseingabe einfügbar
- **Hindernisse** (`type=obstacle`) — Fallen, Gefahrenbereiche, Bewachung, blockierte Wege

### Hindernisse & Fallen

| Gefahrenlevel | Symbol | Konsequenz |
|---------------|--------|-----------|
| `low` | ⚠️ | Verlangsamung, kleiner Umweg |
| `medium` | 🔶 | Verletzung, Ressourcenverlust |
| `high` | 🔴 | Schwere Konsequenz |
| `lethal` | ☠️ | Lebensgefährlich |

Status-Übergänge: `active` → `triggered` (Konsequenz wird beschrieben) → `overcome` / `avoided`

### Charakter-Avatare & JSON-Export

- Avatar-Bild per Dateiauswahl hochladbar (PNG, JPG, WebP)
- JSON-Export je Charakter — kompatibel mit ChatGPT Custom GPTs

---

## 🔧 API-Übersicht

| Methode | Pfad | Beschreibung |
|---------|------|-------------|
| `POST` | `/api/auth/login` | Login |
| `POST` | `/api/auth/register` | Registrierung |
| `GET` | `/api/stories` | Alle Stories |
| `POST` | `/api/stories` | Neue Story anlegen |
| `GET` | `/api/characters/{story_id}` | Charaktere einer Story |
| `POST` | `/api/characters` | Charakter anlegen / aktualisieren |
| `POST` | `/api/characters/{story_id}/{char_id}/avatar` | Avatar hochladen |
| `GET` | `/api/characters/{story_id}/{char_id}/export-json` | Charakter als JSON |
| `POST` | `/api/game/action` | Spieleraktion → KI-Szene |
| `GET` | `/api/game/state/{story_id}` | Aktueller Spielzustand |
| `GET` | `/api/ollama/status` | Ollama-Verbindungsstatus |

---

## 🛠️ Entwicklung

```bash
# Entwicklungsserver mit Auto-Reload:
uvicorn backend.main:app --reload --host 127.0.0.1 --port 8000
```

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

