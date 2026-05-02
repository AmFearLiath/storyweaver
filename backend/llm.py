import json
import httpx
from backend.database import get_story_config, get_characters, get_recent_events, get_llm_config, get_world_state, save_world_state, update_character_state

OLLAMA_URL = "http://localhost:11434/api/generate"


def _flatten_field(v) -> str:
    """Convert a field value to a readable string.

    Handles:
    - dict / JSON object  → "Key1: val1, Key2: val2"
    - list / JSON array   → "item1, item2"
    - plain string        → returned as-is
    """
    if not v:
        return ""
    if isinstance(v, dict):
        return ", ".join(f"{k}: {val}" for k, val in v.items())
    if isinstance(v, list):
        return ", ".join(str(i) for i in v)
    if isinstance(v, str):
        s = v.strip()
        if s.startswith("{"):
            try:
                obj = json.loads(s)
                if isinstance(obj, dict):
                    return ", ".join(f"{k}: {val}" for k, val in obj.items())
            except Exception:
                pass
        if s.startswith("["):
            try:
                lst = json.loads(s)
                if isinstance(lst, list):
                    return ", ".join(str(i) for i in lst)
            except Exception:
                pass
    return str(v)


def _format_world_state_block(world_state: dict | None) -> str:
    """Format the world state as an authoritative constraint block at the top of the system prompt."""
    if not world_state:
        return ""
    loc     = (world_state.get("location") or "").strip()
    time_   = (world_state.get("time")     or "").strip()
    weather = (world_state.get("weather")  or "").strip()
    chars   = [c for c in world_state.get("characters_present", []) if isinstance(c, str) and c.strip()]
    facts   = [f for f in world_state.get("established_facts",   []) if isinstance(f, str) and f.strip()]
    if not loc and not facts:
        return ""
    lines = [
        "⚠️ AKTUELLER SPIELZUSTAND — ABSOLUT VERBINDLICH ⚠️",
        "Dieser Zustand IST die Realität der Spielwelt. Er darf NICHT ignoriert, vergessen oder unbeabsichtigt verändert werden.\n",
    ]
    if loc:
        lines.append(f"📍 Ort:         {loc}")
    if time_:
        lines.append(f"🕐 Zeit:        {time_}")
    if weather:
        lines.append(f"🌤️ Atmosphäre: {weather}")
    if chars:
        lines.append(f"👥 Anwesend:    {', '.join(chars)}")
    if facts:
        lines.append("\n📋 Etablierte Fakten — UNVERLÄNDERT beibehalten:")
        for f in facts:
            lines.append(f"   • {f}")

    # World items section
    items = [i for i in world_state.get("world_items", []) if isinstance(i, dict) and i.get("name")]
    if items:
        lines.append("\n🎒 WELTGEGENSTÄNDE & CODES — PERSISTENT, VERBINDLICH, NICHT VERGESSEN:")
        for item in items:
            status     = item.get("status", "available")
            itype      = item.get("type", "item")
            name       = item.get("name", "")
            desc       = item.get("description", "")
            loc        = item.get("location", "")
            needed_for = item.get("required_for", "")
            held_by    = item.get("held_by", "")
            cval       = item.get("code_value", "")

            if itype == "obstacle":
                # Obstacles: traps, hazards, barriers that can set players back
                danger = item.get("danger_level", "")
                danger_tag = {"low": "⚠️", "medium": "🔶", "high": "🔴", "lethal": "☠️"}.get(danger, "⚠️")
                overcome_by = item.get("required_for", "")  # reuse field for "overcome_by" hint
                if status == "overcome":
                    lines.append(f"   ✅ [HINDERNIS] {name} — ÜBERWUNDEN")
                elif status == "avoided":
                    lines.append(f"   ↩️ [HINDERNIS] {name} — UMGANGEN")
                elif status == "triggered":
                    loc_str = f" @ {loc}" if loc else ""
                    lines.append(f"   💥 [HINDERNIS — AUSGELÖST] {name}{loc_str} — Konsequenz: {desc}")
                else:  # active
                    loc_str      = f" @ {loc}" if loc else ""
                    overcome_str = f" | Überwindung: {overcome_by}" if overcome_by else ""
                    lines.append(f"   {danger_tag} [HINDERNIS — AKTIV] {name}{loc_str} — {desc}{overcome_str}")
            elif itype == "code":
                # Code-Items: track discovery and usage of access codes/combinations
                if status == "used":
                    lines.append(f'   ✅ [CODE] {name} — BEREITS EINGEGEBEN (Wert war: {cval})')
                elif status == "found":
                    need_str = f" | WIRD BENÖTIGT FÜR: {needed_for}" if needed_for else ""
                    lines.append(f'   🔑 [CODE] {name} — WERT: "{cval}" (Spieler kennt diesen Code){need_str}')
                else:  # unknown
                    loc_str  = f" (zu finden: {loc})" if loc else ""
                    need_str = f" | BENÖTIGT FÜR: {needed_for}" if needed_for else ""
                    lines.append(f"   🔒 [CODE] {name}{loc_str} — Spieler kennt den Code noch NICHT{need_str}")
            else:
                # Regular physical items
                if status == "used":
                    lines.append(f"   ✅ {name} — BEREITS BENUTZT/VERBRAUCHT")
                elif status == "held":
                    detail = f" | {desc}" if desc else ""
                    lines.append(f"   ✋ {name} — getragen von {held_by}{detail}")
                else:
                    loc_str  = f" (Fundort: {loc})" if loc else ""
                    need_str = f" | BENÖTIGT FÜR: {needed_for}" if needed_for else ""
                    lines.append(f"   📦 {name}{loc_str} — {desc}{need_str}")

    lines.append(
        "\nSchreibe AUSSCHLIESSLICH in diesem Kontext. Kein anderer Ort, kein anderes Wetter, "
        "keine anderen Charaktere als die aufgeführten — außer durch explizite Spieleraktion.\n"
    )
    return "\n".join(lines) + "\n"


def build_system_prompt(config: dict, characters: list, output_language: str = "Deutsch", world_state: dict = None) -> str:
    # ── Forbidden phrases ──────────────────────────────────────────────────────
    forbidden = config.get("forbidden_phrases", [])
    if isinstance(forbidden, str):
        try:
            forbidden = json.loads(forbidden)
        except Exception:
            forbidden = []
    forbidden_str = "\n".join(f'- "{p}"' for p in forbidden) if forbidden else "- (keine)"

    # ── Forbidden words with (potentially multiple) alternatives ──────────────
    forbidden_alts = config.get("forbidden_words_alts", [])
    if isinstance(forbidden_alts, str):
        try:
            forbidden_alts = json.loads(forbidden_alts)
        except Exception:
            forbidden_alts = []
    forbidden_alts_block = ""
    if forbidden_alts:
        forbidden_alts_block = "\n## VERBOTENE WÖRTER — ZWINGEND ERSETZEN DURCH\n"
        for entry in forbidden_alts:
            w = entry.get("word", "").strip()
            a = entry.get("alts", entry.get("alt", "")).strip()
            if w:
                forbidden_alts_block += f'- "{w}" → {a if a else "(keine Alternative angegeben)"}\n'

    # ── Characters ─────────────────────────────────────────────────────────────
    protagonists = [c for c in characters if c.get("is_protagonist")]
    others = [c for c in characters if not c.get("is_protagonist")]

    def char_block(c):
        tag = "⭐ HAUPTPROTAGONIST" if c.get("is_protagonist") else c.get("role", "")
        lines = [f"### {c['name']} [{tag}] — Status: {c.get('status', 'alive')}"]
        if c.get("description"):
            lines.append(f"Persönlichkeit/Hintergrund: {_flatten_field(c['description'])}")
        if c.get("age"):
            lines.append(f"Alter: {_flatten_field(c['age'])}")
        if c.get("physical_traits"):
            lines.append(f"Körperliche Merkmale: {_flatten_field(c['physical_traits'])}")
        if c.get("default_clothing"):
            lines.append(f"Standard-Kleidung: {_flatten_field(c['default_clothing'])}")
        if c.get("current_clothing"):
            lines.append(f"⚠️ Trägt AKTUELL: {_flatten_field(c['current_clothing'])}")
        if c.get("superpowers"):
            lines.append(f"Fähigkeiten/Kräfte: {_flatten_field(c['superpowers'])}")
        if c.get("likes"):
            lines.append(f"Vorlieben: {_flatten_field(c['likes'])}")
        if c.get("dislikes"):
            lines.append(f"Abneigungen: {_flatten_field(c['dislikes'])}")
        if c.get("favorite_weapon"):
            lines.append(f"Lieblingswaffe/-ausrüstung: {_flatten_field(c['favorite_weapon'])}")
        # Inventory
        inv = c.get("inventory", [])
        if isinstance(inv, str):
            try: inv = json.loads(inv)
            except: inv = []
        if inv:
            lines.append(f"🎒 Inventar: {', '.join(str(i) for i in inv)}")
        # Experiences (last 5)
        exp = c.get("experiences", [])
        if isinstance(exp, str):
            try: exp = json.loads(exp)
            except: exp = []
        if exp:
            lines.append("Erfahrungen/Erlebnisse (neueste zuerst):")
            for e in exp[-5:]:
                lines.append(f"  • {_flatten_field(e)}")
        # Relationships
        rels = c.get("relationships", [])
        if isinstance(rels, str):
            try:
                rels = json.loads(rels)
            except Exception:
                rels = []
        if rels:
            lines.append("Beziehungen:")
            for r in rels:
                t = r.get("target", "")
                rt = r.get("type", "")
                rd = r.get("description", "")
                lines.append(f"  → {t}: {rt}{(' — ' + rd) if rd else ''}")
        return "\n".join(lines)

    prot_section = ""
    if protagonists:
        prot_section = "## HAUPTPROTAGONISTEN (werden vom Spieler gesteuert)\n"
        prot_section += "\n\n".join(char_block(c) for c in protagonists)

    other_section = ""
    if others:
        other_section = "\n\n## WEITERE CHARAKTERE\n"
        other_section += "\n\n".join(char_block(c) for c in others)

    char_descriptions = prot_section + other_section

    # ── Style ──────────────────────────────────────────────────────────────────
    style_map = {
        "einfach":    "einfache, klare Sprache ohne Schnörkel",
        "neutral":    "neutraler, sachlicher Stil",
        "literarisch":"gehobene, literarische Prosa mit starken Bildern und Metaphern",
        "düster":     "düsterer, bedrohlicher Ton — knappe, wuchtige Sätze",
        "humorvoll":  "leicht ironischer, witziger Unterton",
    }
    detail_map = {
        "niedrig": ("kurze Absätze, maximal 3–4 Sätze pro Szene",    "1 Absatz"),
        "mittel":  ("mittellange Szenen, 1–2 Absätze",                   "1–2 Absätze"),
        "hoch":    ("ausführliche, detailreiche Beschreibungen",          "MINDESTENS 3 vollständige Absätze, gerne 4"),
    }
    style_desc  = style_map.get(config.get("language_style", "literarisch"), "literarischer Stil")
    detail_entry = detail_map.get(config.get("detail_level", "hoch"), ("ausführliche Beschreibungen", "MINDESTENS 3 Absätze"))
    detail_desc  = detail_entry[0]
    detail_len   = detail_entry[1]

    # ── Style examples (multiple pairs) ────────────────────────────────────────
    examples = config.get("style_examples", [])
    if isinstance(examples, str):
        try:
            examples = json.loads(examples)
        except Exception:
            examples = []
    examples_block = ""
    if examples:
        examples_block = "\n## STILBEISPIELE\n"
        for i, ex in enumerate(examples, 1):
            good = ex.get("good", "").strip()
            bad  = ex.get("bad", "").strip()
            if good or bad:
                examples_block += f"\nBeispiel {i}:\n"
                if bad:
                    examples_block += f"SCHLECHT: \"{bad}\"\n"
                if good:
                    examples_block += f"GUT:      \"{good}\"\n"

    # ── World & story ──────────────────────────────────────────────────────────
    world_lines = []
    if config.get("world_name"):
        world_lines.append(f"Name: {config['world_name']}")
    if config.get("world_era"):
        world_lines.append(f"Zeitalter: {config['world_era']}")
    if config.get("world_atmosphere"):
        world_lines.append(f"Atmosphäre: {config['world_atmosphere']}")
    if config.get("world_description"):
        world_lines.append(config["world_description"])
    world_block = "\n".join(world_lines)

    # Effective genre
    genre = config.get("story_genre_custom") or config.get("story_genre", "Fantasy")

    # ── Rules (presets + custom) ────────────────────────────────────────────────
    preset_rules = config.get("preset_rules", [])
    if isinstance(preset_rules, str):
        try:
            preset_rules = json.loads(preset_rules)
        except Exception:
            preset_rules = []
    custom_rules = config.get("custom_rules", "").strip()
    all_rules = list(preset_rules)
    if custom_rules:
        # Custom rules may be comma-separated or multi-line
        for r in custom_rules.replace("\n", ",").split(","):
            r = r.strip()
            if r:
                all_rules.append(r)
    rules_str = "\n".join(f"- {r}" for r in all_rules) if all_rules else "- (keine besonderen Regeln)"

    world_state_section = _format_world_state_block(world_state)

    return f"""Du bist ein erfahrener, kreativer Game Master für ein interaktives Textadventure auf {output_language}.

{world_state_section}## WELT
{world_block}

## SZENARIO
{config.get("scenario", "")}

## GENRE & STORY-RAHMEN
Genre: {genre}
{config.get("story_frame", "")}

{char_descriptions}

## SPIELREGELN
{rules_str}

## SCHREIBSTIL
- Stil: {style_desc}
- Detailgrad: {detail_desc}
- PFLICHT-LÄNGE: Der "story"-Text MUSS {detail_len} lang sein. Kurze Antworten sind VERBOTEN.
{examples_block}
## VERBOTENE AUSDRÜCKE (NIEMALS verwenden)
{forbidden_str}
{forbidden_alts_block}
## ABSOLUTE REGELN
1. Schreibe AUSSCHLIESSLICH auf {output_language}
2. Der "story"-Text MUSS {detail_len} lang sein — NIEMALS kürzer
3. Halte dich STRIKT an den vorgegebenen Stil
4. Entscheidungen des Spielers sind PERMANENT und haben Konsequenzen
5. Erzeuge KEINE Widersprüche zur bestehenden Welt oder den Charakteren
6. Verwende NIEMALS die verbotenen Ausdrücke oder verbotenen Wörter
7. Beschreibe Charaktere mit ihren spezifischen körperlichen Merkmalen und Kleidung
8. Berücksichtige Alter, Persönlichkeit, Vorlieben und Abneigungen der Charaktere aktiv
9. Du KANNST und SOLLST handlungsrelevante Gegenstände, Codes UND Hindernisse in der Welt erfinden:
   — Physische Gegenstände: Schlüssel, Messgeräte, Schutzausrüstung, Heilmittel usw.
   — Zugangscodes: Zahlenkombinationen für Safes, Passwörter für Terminals, PIN-Codes für Türen,
     Chiffren auf Zetteln, eingravierte Nummern auf Objekten — type='code' im Weltzustand
   — Hindernisse/Fallen (type='obstacle'): Stolperfallen, vergiftete Räume, instabile Böden,
     bewachte Türen, blockierte Ausgänge, radioaktive Bereiche, Sicherheitsmechanismen,
     kollabierte Strukturen, Feindposten — können Rückschläge, Verletzungen oder Umwege erzeugen.
     danger_level: 'low' (Verlangsamung), 'medium' (Verletzung), 'high' (schwere Konsequenz),
     'lethal' (lebensgefährlich). required_for = Wie überwindet man es?
     Obstacle-Status: active=aktiv/vorhanden, triggered=Spieler hat Konsequenz erlebt,
     overcome=erfolgreich überwunden, avoided=umgangen ohne Konsequenz.
   — Beschreibe Gegenstände, Codes und Hindernisse im Story-Text auffindbar/spürbar
     (z.B. „Auf dem Zettel steht die Zahl 4817" oder „Du bemerkst eine Stolperdraht quer durch den Flur")
   — Ein Code wechselt von status='unknown' auf status='found' sobald der Spieler ihn liest/entdeckt
   — Ein Code wechselt auf status='used' sobald der Spieler ihn korrekt eingegeben hat
   — Physische Gegenstände bleiben bestehen bis ein Charakter sie aufnimmt oder benutzt
   — Gegenstände und bekannte Codes aus dem Inventar können im richtigen Moment eingesetzt werden
   — Aktive Hindernisse müssen im Story-Kontext präsent bleiben bis überwunden/umgangen
   — PFLICHT: Mindestens eine der drei Optionen MUSS es dem Spieler ermöglichen,
     unentdeckte Gegenstände (status=available) oder unbekannte Codes (status=unknown)
     an ihrem konkreten Fundort zu suchen, zu untersuchen oder zu öffnen.
     Bei aktiven Hindernissen (status=active): mindestens eine Option zum Umgang damit anbieten.
     Beispiel: „Durchsuche die Schreibtisch-Schublade", „Deaktiviere den Stolperdraht" oder „Umgehe das Hindernis vorsichtig"

## OUTPUT FORMAT
Antworte IMMER und AUSSCHLIESSLICH mit genau diesen zwei Feldern im JSON-Format:
{{
  "story": "Vollständiger Szenentext in mehreren Absätzen...",
  "options": ["Entscheidung 1", "Entscheidung 2", "Entscheidung 3"]
}}
Kein anderes Format. Kein anderes JSON-Schema. Nur "story" und "options"."""


def build_user_prompt(player_action: str, recent_events: list, scene_number: int, detail_len: str = "MINDESTENS 3 Absätze", memory_depth: int = 3, unfound_items: list = None, active_obstacles: list = None) -> str:
    memory_block = ""
    if recent_events:
        events_to_show = recent_events[-memory_depth:]
        memory_block = "\n\n## BISHERIGE SZENEN (KONTEXT — NICHT VERGESSEN)\n"
        memory_block += "Die folgenden Szenen haben stattgefunden. Führe die Geschichte EXAKT in diesem Kontext fort.\n"
        for ev in events_to_show:
            action = ev.get("interpreted_action") or ev.get("player_action", "")
            story_excerpt = (ev.get("story_text") or "").strip()
            # Include a substantial excerpt: first 250 chars (scene setup) + last 150 chars (outcome)
            if len(story_excerpt) > 450:
                story_excerpt = story_excerpt[:250] + " […] " + story_excerpt[-150:]
            header = f"\n--- Szene {ev['scene_number']}"
            if action and action.lower() not in ("[spielstart]", "spielstart", ""):
                action_short = action[:80] + ("…" if len(action) > 80 else "")
                header += f" | Spieler: \"{action_short}\""
            header += " ---"
            memory_block += header + "\n"
            if story_excerpt:
                memory_block += story_excerpt + "\n"

    # Build mandatory investigation hint for unfound items/codes
    investigation_block = ""
    if unfound_items:
        lines_inv = []
        for it in unfound_items:
            name = it.get("name", "")
            loc  = it.get("location", "")
            if name and loc:
                lines_inv.append(f"   • {name} → Fundort: {loc}")
        if lines_inv:
            investigation_block = (
                "\n\n## ⚠️ PFLICHT — UNENTDECKTE GEGENSTÄNDE / CODES\n"
                "Folgende Objekte wurden noch NICHT gefunden. Mindestens eine der drei Optionen\n"
                "MUSS dem Spieler ermöglichen, genau diesen Fundort zu untersuchen oder zu durchsuchen:\n"
                + "\n".join(lines_inv)
            )

    # Build warning block for active obstacles
    obstacle_block = ""
    if active_obstacles:
        DANGER_TAG = {"low": "⚠️", "medium": "🔶", "high": "🔴", "lethal": "☠️"}
        lines_obs = []
        for obs in active_obstacles:
            name    = obs.get("name", "")
            loc     = obs.get("location", "")
            desc    = obs.get("description", "")
            danger  = obs.get("danger_level", "medium")
            status  = obs.get("status", "active")
            tag     = DANGER_TAG.get(danger, "⚠️")
            overcome= obs.get("required_for", "")
            loc_str = f" @ {loc}" if loc else ""
            ov_str  = f" | Überwindung: {overcome}" if overcome else ""
            state_str = " [AUSGELÖST — Konsequenz beschreiben]" if status == "triggered" else ""
            lines_obs.append(f"   {tag} {name}{loc_str}{state_str} — {desc}{ov_str}")
        obstacle_block = (
            "\n\n## ⚠️ AKTIVE HINDERNISSE / FALLEN — UNBEDINGT BEACHTEN\n"
            "Diese Hindernisse bestehen noch. Mindestens eine Option muss dem Spieler\n"
            "ermöglichen, damit umzugehen (überwinden, umgehen oder erleiden):\n"
            + "\n".join(lines_obs)
        )

    if scene_number == 1 and not player_action:
        return f"""## SZENE 1 — SPIELSTART
{memory_block}

Beginne die Geschichte. Stelle die Welt, den/die Hauptprotagonisten und die aktuelle Situation vor.
Schreibe eine packende Eröffnungsszene. WICHTIG: Schreibe {detail_len} — nicht kürzer!
Biete am Ende drei erste Entscheidungsmöglichkeiten an.{investigation_block}{obstacle_block}"""

    return f"""## SZENE {scene_number}
{memory_block}

## SPIELER-AKTION
Der Spieler entscheidet: "{player_action}"

Schreibe die nächste Szene. WICHTIG: Der "story"-Text MUSS {detail_len} lang sein — nicht kürzer!
Berücksichtige alle bisherigen Ereignisse und die Charaktereigenschaften.{investigation_block}{obstacle_block}"""


async def call_ollama_json(prompt: str, system: str = "") -> dict:
    """Call Ollama with a plain prompt and return parsed JSON from the response.
    
    Prefers small fast models for JSON tasks. Falls back to configured model.
    """
    from backend.database import get_llm_config as _get_llm_config
    llm_cfg = _get_llm_config()
    configured_model = llm_cfg.get("ollama_model", "")

    # Prefer small reliable models for JSON generation; fall back to configured model
    FAST_JSON_MODELS = ["llama3.2:3b", "phi:latest", "tinyllama:latest"]
    try:
        tags = await check_ollama_connection()
        available = tags.get("models", [])
    except Exception:
        available = []

    # Pick a fast model if available, otherwise use configured/first available
    model = ""
    for candidate in FAST_JSON_MODELS:
        if candidate in available:
            model = candidate
            break
    if not model:
        model = configured_model if (configured_model and configured_model != "llama3") else ""
        if not model and available:
            model = available[0]
    if not model:
        model = "llama3.2:3b"  # last resort

    temp    = float(llm_cfg.get("temperature", "0.7"))
    top_p   = float(llm_cfg.get("top_p", "0.9"))
    rep_pen = float(llm_cfg.get("repeat_penalty", "1.1"))

    payload: dict = {
        "model": model,
        "prompt": prompt,
        "stream": False,
        "format": "json",
        "options": {
            "temperature": temp,
            "top_p": top_p,
            "repeat_penalty": rep_pen,
            "num_predict": 600,
        },
    }
    if system:
        payload["system"] = system

    async with httpx.AsyncClient(timeout=90.0) as client:
        resp = await client.post(OLLAMA_URL, json=payload)
        resp.raise_for_status()
        data = resp.json()
        # Ollama can return {"error": "..."} with status 200
        if "error" in data:
            raise ValueError(f"Ollama Fehler: {data['error']}")
        raw = data.get("response", "")

    text = raw.strip()
    # Fix common LLM JSON mangling before parsing
    text = text.replace("\\_", "_")
    start = text.find("{")
    end   = text.rfind("}") + 1
    if start == -1 or end <= start:
        raise ValueError(f"LLM hat kein JSON zurückgegeben (Modell: {model})")

    candidate = text[start:end]
    try:
        return json.loads(candidate)
    except json.JSONDecodeError:
        # Try to sanitize: fix keys with leading dots or spaces
        import re
        fixed = re.sub(r'"\.([^"]+)"', r'"\1"', candidate)  # ".world.name" → "world.name"
        fixed = re.sub(r'"([^"]*\.)([^".]+)"(\s*:)', lambda m: f'"{m.group(2)}"{m.group(3)}', fixed)
        try:
            return json.loads(fixed)
        except json.JSONDecodeError:
            pass
    raise ValueError(f"LLM hat kein gültiges JSON zurückgegeben: {candidate[:200]}")


async def _extract_world_state(
    story_text: str,
    old_state: dict,
    config: dict,
    model: str,
    num_ctx: int = 4096,
    output_language: str = "Deutsch",
) -> dict | None:
    """Call Ollama to extract the current world state from a story passage.
    Returns a dict with location/time/weather/characters_present/established_facts, or None on failure."""
    old_location = (old_state or {}).get("location", "")
    old_facts = (old_state or {}).get("established_facts", [])
    old_facts_text = "; ".join(old_facts[:6]) if old_facts else "keine"
    old_items = (old_state or {}).get("world_items", [])
    old_items_text = json.dumps(old_items, ensure_ascii=False)[:800] if old_items else "[]"
    sys_prompt = (
        "Du bist ein Zustandsanalysator für ein Textadventure. "
        "Antworte NUR mit einem JSON-Objekt (kein Markdown). "
        "Schema: {\"location\":\"...\",\"time\":\"...\",\"weather\":\"...\","
        "\"characters_present\":[\"Name1\",\"Name2\"],"
        "\"established_facts\":[\"Fakt1\",\"Fakt2\"],"
        "\"world_items\":[{\"id\":\"kurz_eindeutig\",\"name\":\"...\",\"description\":\"...\","
        "\"location\":\"wo genau\",\"required_for\":\"wofür benötigt oder wie zu überwinden\","
        "\"status\":\"available|held|used|unknown|found|active|triggered|overcome|avoided\",\"held_by\":null,"
        "\"type\":\"item|code|obstacle\",\"code_value\":null,\"danger_level\":null}]} "
        "type='code' für Zugangscodes/Kombinationen/Passwörter (code_value=der_code_string). "
        "Code-Status: unknown=Spieler hat den Code noch nicht gefunden, found=Spieler hat den Code gelesen, used=Code wurde erfolgreich eingegeben. "
        "type='item' für physische Gegenstände (Standardfall, status=available|held|used). "
        "type='obstacle' für Hindernisse/Fallen/Gefahren (Fallen, verschlossene Türen mit Kraftaufwand, giftige Bereiche, Bewachung, Abgründe). "
        "Obstacle-Status: active=Hindernis besteht, triggered=Falle/Setback wurde ausgelöst, overcome=überwunden, avoided=umgangen. "
        "danger_level für obstacles: 'low'|'medium'|'high'|'lethal'. required_for=Wie kann es überwunden werden. "
        "world_items: ALLE handlungsrelevanten Gegenstände, Codes UND Hindernisse. "
        "BEHALTE bestehende Einträge — aktualisiere nur deren Status wenn nötig. "
        "Füge neue hinzu wenn sie in der Szene auftauchen oder erwähnt werden. "
        "established_facts: max 8 kurze Fakten. Keine Erklärungen."
    )
    user_prompt = (
        f"Bisheriger Ort: {old_location}\n"
        f"Bisherige Fakten: {old_facts_text}\n"
        f"Bestehende Gegenstände: {old_items_text}\n\n"
        f"=== SZENENTEXT ===\n{story_text[:2000]}\n\n"
        f"Extrahiere den aktuellen Weltzustand. Sprache der Felder: {output_language}."
    )
    payload = {
        "model": model,
        "system": sys_prompt,
        "prompt": user_prompt,
        "stream": False,
        "format": "json",
        "options": {
            "temperature": 0.1,
            "num_predict": 450,
            "num_ctx": num_ctx,
        },
    }
    try:
        async with httpx.AsyncClient(timeout=45.0) as client:
            resp = await client.post(OLLAMA_URL, json=payload)
            resp.raise_for_status()
            raw = resp.json().get("response", "")
        parsed = _extract_json(raw)
        if not isinstance(parsed, dict):
            return None
        # Keep old established_facts if new ones are empty
        if not parsed.get("established_facts") and old_facts:
            parsed["established_facts"] = old_facts[:8]
        else:
            parsed["established_facts"] = (parsed.get("established_facts") or [])[:8]
        # Merge world_items: LLM result + any old items the LLM forgot to include
        new_items = [i for i in (parsed.get("world_items") or []) if isinstance(i, dict) and i.get("name")]
        if old_items:
            new_item_names = {i.get("name", "").lower() for i in new_items}
            for old_item in old_items:
                if old_item.get("name", "").lower() not in new_item_names:
                    new_items.append(old_item)
        parsed["world_items"] = new_items[:20]
        return parsed
    except Exception:
        return None


async def _extract_character_states(
    story_text: str,
    characters: list,
    model: str,
    num_ctx: int = 4096,
    output_language: str = "Deutsch",
) -> list:
    """Call Ollama to extract updated character states (clothing, inventory, new experiences).
    Returns a list of dicts with name/current_clothing/inventory/new_experiences, or [] on failure."""
    if not characters:
        return []
    # Build compact character summary for the prompt
    char_lines = []
    for c in characters:
        inv = c.get("inventory") or []
        inv_text = ", ".join(inv[:5]) if inv else "keins"
        clothing = c.get("current_clothing") or c.get("default_clothing") or ""
        char_lines.append(f"- {c['name']} | Trägt: {clothing} | Inventar: {inv_text}")
    char_summary = "\n".join(char_lines)
    sys_prompt = (
        "Du bist ein Zustandsanalysator für Charaktere in einem Textadventure. "
        "Antworte NUR mit einem JSON-Array (kein Markdown). "
        "Schema: [{\"name\":\"...\",\"current_clothing\":\"...\","
        "\"inventory\":[\"Gegenstand1\",\"Gegenstand2\"],"
        "\"new_experiences\":[\"Kurze Beschreibung\"]}] "
        "Gib NUR Charaktere zurück, die sich wirklich veraendert haben. "
        "inventory: vollstaendige aktuelle Liste (max 15 Eintraege). "
        "new_experiences: nur wirklich neue Ereignisse, max 2 pro Szene, kurze Saetze. "
        "Leere Felder als leeren String oder leere Liste. Keine Erlaeuterungen."
    )
    user_prompt = (
        f"Aktuelle Charaktere:\n{char_summary}\n\n"
        f"=== SZENENTEXT ===\n{story_text[:2000]}\n\n"
        f"Welche Charakterzustaende haben sich veraendert? Sprache: {output_language}."
    )
    payload = {
        "model": model,
        "system": sys_prompt,
        "prompt": user_prompt,
        "stream": False,
        "format": "json",
        "options": {
            "temperature": 0.15,
            "num_predict": 500,
            "num_ctx": num_ctx,
        },
    }
    try:
        async with httpx.AsyncClient(timeout=45.0) as client:
            resp = await client.post(OLLAMA_URL, json=payload)
            resp.raise_for_status()
            raw = resp.json().get("response", "")
        # Try direct array parse first (before _extract_json which only finds {})
        stripped = raw.strip()
        if stripped.startswith("["):
            try:
                arr = json.loads(stripped)
                if isinstance(arr, list) and all(isinstance(x, dict) for x in arr):
                    return arr
            except json.JSONDecodeError:
                pass
        parsed = _extract_json(raw)
        if isinstance(parsed, list):
            # Only accept if all items are dicts (character updates, not bare strings)
            if all(isinstance(x, dict) for x in parsed):
                return parsed
        if isinstance(parsed, dict):
            # Model returned a single character update — wrap it
            if "name" in parsed:
                return [parsed]
            # Model wrapped the list: {"updates": [...]} or similar
            for v in parsed.values():
                if isinstance(v, list) and all(isinstance(x, dict) for x in v):
                    return v
        return []
    except Exception:
        return []


def _get_unfound_items(world_state: dict | None) -> list:
    """Return items/codes that have a known location but haven't been found/used yet."""
    if not world_state:
        return []
    result = []
    for item in world_state.get("world_items", []):
        if not isinstance(item, dict) or not item.get("name") or not item.get("location"):
            continue
        itype  = item.get("type", "item")
        status = item.get("status", "available")
        if itype == "obstacle":
            continue  # obstacles handled separately
        if itype == "code" and status == "unknown":
            result.append(item)
        elif itype != "code" and status == "available":
            result.append(item)
    return result


def _get_active_obstacles(world_state: dict | None) -> list:
    """Return obstacles that are currently active (unresolved threats)."""
    if not world_state:
        return []
    result = []
    for item in world_state.get("world_items", []):
        if not isinstance(item, dict):
            continue
        if item.get("type") == "obstacle" and item.get("status") in ("active", "triggered"):
            result.append(item)
    return result


def _ensure_investigation_options(options: list, world_state: dict | None, language: str) -> list:
    """Post-processing safety net: if unfound items or active obstacles exist but no relevant
    option is present, inject one based on an actual item/obstacle location."""
    unfound   = _get_unfound_items(world_state)
    obstacles = _get_active_obstacles(world_state)
    if not unfound and not obstacles:
        return options

    options_lower = " ".join(options).lower()

    # ── Check coverage for unfound items/codes ────────────────────────────────
    covered_items = set()
    for item in unfound:
        loc = (item.get("location") or "").strip().lower()
        if loc and loc in options_lower:
            covered_items.add(item["name"])

    # ── Check coverage for active obstacles ───────────────────────────────────
    covered_obs = set()
    for obs in obstacles:
        name = obs.get("name", "").lower()
        loc  = (obs.get("location") or "").strip().lower()
        if (name and name in options_lower) or (loc and loc in options_lower):
            covered_obs.add(obs.get("name", ""))

    # If everything is already covered → nothing to inject
    all_items_covered = len(covered_items) >= len(unfound)
    all_obs_covered   = len(covered_obs) >= len(obstacles)
    if all_items_covered and all_obs_covered:
        return options

    lang = language.lower()

    # Prefer injecting an obstacle option if there's an uncovered active obstacle
    uncovered_obs = [o for o in obstacles if o.get("name", "") not in covered_obs]
    if uncovered_obs and not all_obs_covered:
        target = uncovered_obs[0]
        name   = target.get("name", "")
        loc    = target.get("location", "")
        overcome_hint = (target.get("required_for") or "").strip()
        if "en" in lang:
            opt = f"Deal with {name}" if not overcome_hint else f"{overcome_hint[:60]}"
        elif "fr" in lang:
            opt = f"Gérer {name}" if not overcome_hint else f"{overcome_hint[:60]}"
        elif "es" in lang:
            opt = f"Enfrentar {name}" if not overcome_hint else f"{overcome_hint[:60]}"
        else:
            if overcome_hint:
                opt = overcome_hint[:60]
            elif loc:
                opt = f"Gehe behutsam mit {name} um"
            else:
                opt = f"Überwinde {name}"
        if len(options) >= 3:
            return options[:-1] + [opt]
        return options + [opt]

    # Otherwise inject an item/code investigation option
    target = next(
        (i for i in unfound if i.get("name") not in covered_items and i.get("location")),
        unfound[0]
    )
    name  = target.get("name", "")
    loc   = target.get("location", "")
    itype = target.get("type", "item")

    if "en" in lang:
        opt = f"Search {loc}" if loc else f"Look for {name}"
    elif "fr" in lang:
        opt = f"Fouiller {loc}" if loc else f"Chercher {name}"
    elif "es" in lang:
        opt = f"Examinar {loc}" if loc else f"Buscar {name}"
    else:
        if itype == "code":
            opt = f"Durchsuche {loc}" if loc else f"Suche nach dem Code '{name}'"
        else:
            opt = f"Untersuche {loc}" if loc else f"Suche nach {name}"

    if len(options) >= 3:
        return options[:-1] + [opt]
    return options + [opt]


async def generate_scene(player_action: str, scene_number: int, story_id: int) -> dict:
    config     = get_story_config(story_id)
    characters = get_characters(story_id)
    llm_cfg    = get_llm_config()

    # Read configurable parameters
    memory_depth    = int(llm_cfg.get("memory_depth", "3"))
    cfg_num_predict = int(llm_cfg.get("num_predict", "1600"))
    num_ctx         = int(llm_cfg.get("num_ctx", "4096"))
    output_language = llm_cfg.get("output_language", "Deutsch")

    world_state = get_world_state(story_id)
    events      = get_recent_events(story_id, limit=max(memory_depth, 8))

    # Collect unfound items/codes that need an investigation option in every scene
    unfound_items    = _get_unfound_items(world_state)
    active_obstacles = _get_active_obstacles(world_state)

    system_prompt = build_system_prompt(config, characters, output_language, world_state)
    _detail_map = {
        "niedrig": "1 Absatz",
        "mittel":  "1–2 Absätze",
        "hoch":    "MINDESTENS 3 vollständige Absätze, gerne 4",
    }
    detail_len  = _detail_map.get(config.get("detail_level", "hoch"), "MINDESTENS 3 Absätze")
    user_prompt = build_user_prompt(player_action, events, scene_number, detail_len, memory_depth, unfound_items, active_obstacles)

    model = llm_cfg.get("ollama_model", "")
    if not model or model == "llama3":
        tags = await check_ollama_connection()
        available = tags.get("models", [])
        model = available[0] if available else "llama3"
    temp     = float(llm_cfg.get("temperature", "0.7"))
    top_p    = float(llm_cfg.get("top_p", "0.9"))
    rep_pen  = float(llm_cfg.get("repeat_penalty", "1.1"))

    async def _call_ollama(sys_prompt: str, usr_prompt: str, num_predict: int = cfg_num_predict) -> str:
        payload = {
            "model": model,
            "system": sys_prompt,
            "prompt": usr_prompt,
            "stream": False,
            "format": "json",
            "options": {
                "temperature": temp,
                "top_p": top_p,
                "repeat_penalty": rep_pen,
                "num_predict": num_predict,
                "num_ctx": num_ctx,
            },
        }
        async with httpx.AsyncClient(timeout=300.0) as client:
            resp = await client.post(OLLAMA_URL, json=payload)
            resp.raise_for_status()
            return resp.json().get("response", "")

    def _parse_result(raw: str) -> dict | None:
        text = raw.strip().replace("\\_", "_")
        parsed = _extract_json(text)
        if parsed is None:
            return None
        story = _clean_story_text(parsed.get("story", ""))
        if not story:
            story = _deep_extract_story(parsed) or _strip_json_artifacts(text)
        if not story:
            return None
        options = [o for o in (parsed.get("options") or []) if isinstance(o, str) and o.strip()]
        if not options:
            options = ["Weiter →", "Erkunde die Umgebung", "Warte und beobachte"]
        return {
            "story":                     story,
            "options":                   options,
            "events":                    parsed.get("events", ""),
            "world_changes":             parsed.get("world_changes", ""),
            "character_updates":         parsed.get("character_updates", ""),
            "interpreted_player_action": parsed.get("interpreted_player_action", player_action),
        }

    # Attempt 1: full prompt
    raw = await _call_ollama(system_prompt, user_prompt)
    result = _parse_result(raw)
    if result and len(result["story"]) >= 150:
        new_ws = await _extract_world_state(result["story"], world_state, config, model, num_ctx, output_language)
        if new_ws:
            new_ws["scene"] = scene_number
            save_world_state(story_id, new_ws)
        char_updates = await _extract_character_states(result["story"], characters, model, num_ctx, output_language)
        for upd in char_updates:
            update_character_state(
                story_id, upd["name"],
                current_clothing=upd.get("current_clothing"),
                inventory=upd.get("inventory"),
                new_experiences=upd.get("new_experiences"),
            )
        result["world_items"] = (new_ws or world_state or {}).get("world_items", [])
        # Safety net: ensure at least one investigation option for unfound items/codes
        result["options"] = _ensure_investigation_options(result["options"], new_ws or world_state, output_language)
        return result

    # Attempt 2: minimal fallback prompt (avoids context overflow for small models)
    protagonists = [c for c in characters if c.get("is_protagonist")]
    prot_name = protagonists[0]["name"] if protagonists else "der Held"
    genre = config.get("story_genre_custom") or config.get("story_genre", "Fantasy")
    world = config.get("world_name", "eine unbekannte Welt")
    fallback_system = (
        f"Du bist ein Game Master für ein {genre}-Textadventure in {world}. "
        f"Hauptprotagonist: {prot_name}. "
        f"Schreibe ausschließlich auf {output_language}. Antworte nur mit JSON: {{\"story\": \"...\", \"options\": [\"a\", \"b\", \"c\"]}}"
    )
    action_desc = player_action or "Beginne die Geschichte."
    fallback_user = f"Szene {scene_number}: {action_desc}\nSchreibe 2–3 Absätze immersiven Storytime-Text auf {output_language}."
    raw2 = await _call_ollama(fallback_system, fallback_user, num_predict=max(cfg_num_predict // 2, 400))
    result2 = _parse_result(raw2)
    if result2:
        new_ws = await _extract_world_state(result2["story"], world_state, config, model, num_ctx, output_language)
        if new_ws:
            new_ws["scene"] = scene_number
            save_world_state(story_id, new_ws)
        char_updates = await _extract_character_states(result2["story"], characters, model, num_ctx, output_language)
        for upd in char_updates:
            update_character_state(
                story_id, upd["name"],
                current_clothing=upd.get("current_clothing"),
                inventory=upd.get("inventory"),
                new_experiences=upd.get("new_experiences"),
            )
        result2["world_items"] = (new_ws or world_state or {}).get("world_items", [])
        # Safety net: ensure at least one investigation option for unfound items/codes
        result2["options"] = _ensure_investigation_options(result2["options"], new_ws or world_state, output_language)
        return result2

    # Last resort: return whatever we got from attempt 1
    if result:
        return result
    clean = _strip_json_artifacts(raw.strip())
    return {
        "story":                     clean or "Der Game Master konnte keine Antwort generieren.",
        "options":                   ["Weiter →", "Erkunde die Umgebung", "Warte und beobachte"],
        "events":                    "",
        "world_changes":             "",
        "character_updates":         "",
        "interpreted_player_action": player_action,
    }



def _deep_extract_story(data, min_len: int = 80) -> str:
    """Deep-search a parsed JSON dict for story text when model used a non-standard schema."""
    import re
    if not isinstance(data, (dict, list)):
        return ""
    # Collect all string values
    strings = []
    def collect(obj):
        if isinstance(obj, str) and len(obj) >= min_len:
            strings.append(obj)
        elif isinstance(obj, dict):
            # Priority keys first
            for k, v in obj.items():
                if isinstance(k, str) and k.lower() in ("story", "text", "content", "narrative", "scene", "beschreibung"):
                    collect(v)
            for k, v in obj.items():
                if isinstance(k, str) and k.lower() not in ("story", "text", "content", "narrative", "scene", "beschreibung"):
                    collect(v)
        elif isinstance(obj, list):
            for item in obj:
                collect(item)
    collect(data)
    if not strings:
        return ""

    def _looks_like_story(s: str) -> bool:
        """Check that s looks like prose, not JSON keys or garbage."""
        # Reject if mostly snake_case (>25% underscores relative to word chars)
        word_chars = sum(c.isalpha() or c.isdigit() for c in s)
        underscores = s.count('_')
        if word_chars > 0 and underscores / word_chars > 0.25:
            return False
        # Reject if contains too many JSON structural chars
        json_chars = sum(1 for c in s if c in '{}[]')
        if json_chars > 5:
            return False
        # Must contain at least 2 spaces (prose has spaces between words)
        if s.count(' ') < 2:
            return False
        # Must contain at least one German indicator word
        german_indicators = ('der ', 'die ', 'das ', ' und ', ' ist ', ' ein ', ' in ', ' zu ', ' mit ', ' hatte ', ' auf ', ' war ')
        return any(w in s.lower() for w in german_indicators)

    for s in sorted(strings, key=len, reverse=True):
        if _looks_like_story(s):
            return _clean_story_text(s)
    return ""


def _extract_json(text: str) -> dict | None:
    """Try several strategies to extract a valid JSON dict from LLM output."""
    import re

    start = text.find("{")
    end   = text.rfind("}") + 1
    if start == -1 or end <= start:
        return None

    candidate = text[start:end]

    # Strategy 1: direct parse
    try:
        return json.loads(candidate)
    except json.JSONDecodeError:
        pass

    # Strategy 2: remove common LLM quirks
    fixed = candidate
    fixed = re.sub(r'\.\.\.',  '', fixed)          # ellipsis …
    fixed = re.sub(r'\.\.',    '', fixed)           # double dot ..
    fixed = re.sub(r',\s*([}\]])', r'\1', fixed)   # trailing commas
    fixed = re.sub(r'//[^\n]*', '', fixed)          # // comments
    try:
        return json.loads(fixed)
    except json.JSONDecodeError:
        pass

    # Strategy 3: fix truncated JSON by closing unclosed structure
    try:
        trimmed = fixed.rstrip()
        if not trimmed.endswith('}'):
            trimmed = re.sub(r',?\s*"[^"]*$', '', trimmed)
            trimmed = trimmed.rstrip(',') + '}'
        result3 = json.loads(trimmed)
        if result3.get("story"):
            return result3
    except Exception:
        pass

    # Strategy 4: regex-extract individual fields (tolerant: no closing quote needed)
    result: dict = {}
    for key in ("story", "events", "world_changes", "character_updates", "interpreted_player_action"):
        m = re.search(rf'"\s*{key}\s*"\s*:\s*"((?:[^"\\]|\\.)*)', text, re.DOTALL)
        if m:
            val = m.group(1)
            # Stop at next JSON key
            val = re.split(r'"\s*,?\s*"[a-z_]+"\s*:', val)[0]
            result[key] = val.replace('\\n', '\n').replace('\\"', '"').strip()
    m = re.search(r'"options"\s*:\s*\[(.*?)(?:\]|$)', text, re.DOTALL)
    if m:
        result["options"] = re.findall(r'"((?:[^"\\]|\\.)*?)"', m.group(1))
    return result if result.get("story") else None


def _clean_story_text(text: str) -> str:
    """Remove JSON artifacts that leaked into extracted story text."""
    import re
    if not text:
        return ""
    # Remove event-list blocks that the model mistakenly put inside story
    text = re.sub(r',?\s*["\']?event[is]*["\']?\s*:\s*[\n\r](?:[-*•].+[\n\r]?)*', '', text, flags=re.IGNORECASE)
    # Strip HTML tags (model sometimes outputs <p>, <br> etc.)
    text = re.sub(r'<[^>]{1,50}>', ' ', text)
    # Remove {template placeholder} markers like {Beschreibung der Werkstatt...}
    text = re.sub(r'\{[^}]{0,150}\}', '', text)
    # Remove leading ellipsis / dots
    text = re.sub(r'^[.\s]+', '', text.lstrip())
    # Remove leading format-instruction echoes like '"...text in Absätzen...'
    text = re.sub(r'^["\']?\.\.\.[\w\s,]+\.\.\.\s*', '', text.lstrip())
    # Remove JSON field prefixes at start of text: " story": or "status": ...
    text = re.sub(r'^[\s"\']?[a-z_][a-zA-Z0-9_\s]*["\']?\s*:\s*', '', text.lstrip())
    # Remove trailing JSON continuation debris: ...", [  or  ":[ etc.
    text = re.sub(r'\s*["\':,\[\{]+\s*$', '', text.rstrip())
    # Remove trailing JSON comma/bracket debris
    text = re.sub(r',?\s*[}\]]+\s*$', '', text.rstrip())
    # Remove lines that are pure JSON artifacts
    lines = text.split('\n')
    cleaned = []
    for line in lines:
        stripped = line.strip()
        # Skip pure-symbol lines
        if re.match(r'^[.{},\[\]"\s]*$', stripped):
            continue
        # Skip JSON field-assignment lines with no story value
        if re.match(r'^["\']?[a-z_][a-zA-Z0-9_]*["\']?\s*:\s*(?:null|true|false|\d+|\.\.\.|\.\.|""|,|\[\]|\{\})?\s*,?\s*$', stripped):
            continue
        # Remove field-name prefix from line (e.g. '"scene_1_story": Real text here')
        line = re.sub(r'^["\']?[a-z_][a-zA-Z0-9_]*["\']?\s*:\s*["\']?', '', line)
        # Remove trailing quote+comma+bracket
        line = re.sub(r'["\'],?\s*$', '', line)
        line = re.sub(r'\s*["\':,\[\{]+\s*$', '', line)
        if line.strip():
            cleaned.append(line)
    return '\n'.join(cleaned).strip()


def _strip_json_artifacts(text: str) -> str:
    """Remove JSON wrapper and return just the story text if present."""
    import re
    # Try to extract story value (handles space in key like '" story":')
    m = re.search(r'"\s*story\s*"\s*:\s*"((?:[^"\\]|\\.)*)', text, re.DOTALL)
    if m:
        val = m.group(1)
        # Stop at the next JSON key
        val = re.split(r'"\s*,?\s*"[a-z_]+"\s*:', val)[0]
        return _clean_story_text(val.replace('\\n', '\n').replace('\\"', '"'))
    # Otherwise strip obvious JSON chars and clean
    clean = re.sub(r'^\s*[{"]*', '', text)
    clean = re.sub(r'[}"]*\s*$', '', clean)
    clean = re.sub(r'"[a-z_]+"\s*:\s*\[.*?\]', '', clean, flags=re.DOTALL)
    clean = re.sub(r'"[a-z_]+"\s*:\s*', '', clean)
    clean = re.sub(r'",?\s*$', '', clean, flags=re.MULTILINE)
    return _clean_story_text(clean)


async def check_ollama_connection() -> dict:
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get("http://localhost:11434/api/tags")
            resp.raise_for_status()
            data = resp.json()
            models = [m["name"] for m in data.get("models", [])]
            return {"status": "ok", "models": models}
    except Exception:
        return {"status": "error", "models": []}
