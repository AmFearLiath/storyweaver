import json
import httpx
from backend.database import get_story_config, get_characters, get_recent_events, get_llm_config, get_world_state, save_world_state, update_character_state, get_factions, update_faction_state
from backend.memory import remember, recall

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


# ── Genre-Anti-Drift ──────────────────────────────────────────────────────────
# Tropes/elements that should NEVER appear in a given genre, baked into the
# system prompt so even small LLMs stay on-topic.
_GENRE_FORBIDDEN = {
    "postapokalypse": [
        "klassische Magie / Zaubersprüche",
        "Götter, Engel, Dämonen, übernatürliche Wesen",
        "esoterische 'Energienetze', 'Resonanzen', 'Auras' oder 'Chakren'",
        "Drachen, Elfen, Zwerge, Orks oder andere Fantasy-Rassen",
        "mittelalterliche Schwerter, Bögen oder Plattenrüstungen als Hauptwaffen",
    ],
    "scifi": [
        "klassische Magie und Zaubersprüche",
        "Götter und religiöse Wunder als physische Realität",
        "mittelalterliche Schwerter und Bögen als Hauptwaffen",
    ],
    "cyberpunk": [
        "klassische Magie und Götter (außer als kulturelle Metapher)",
        "Drachen, Elfen, Zwerge oder Fantasy-Rassen",
        "mittelalterliche Settings (Burgen, Bauernhöfe, Königshöfe)",
    ],
    "highfantasy": [
        "moderne Hochtechnologie (Computer, Smartphones, Autos, Schusswaffen)",
        "Atomwaffen, Cyber-Implantate, Hologramme",
    ],
    "fantasy": [
        "moderne Schusswaffen, Computer, Autos",
        "Atomtechnologie, Cyber-Implantate",
    ],
    "horror": [
        "klamaukige Comedy-Wendungen",
        "happy-go-lucky Sofortlösungen ohne Konsequenz",
    ],
    "krimi": [
        "übernatürliche Erklärungen (Magie, Geister) für reale Verbrechen",
        "Deus-ex-machina-Auflösungen",
    ],
    "romance": [
        "abrupte Genre-Wechsel zu Action/Horror ohne Setup",
    ],
}


def _genre_anti_drift_block(genre: str) -> str:
    """Return a block listing genre-incompatible elements to avoid drift."""
    if not genre:
        return ""
    key = (genre or "").lower().replace(" ", "").replace("-", "").replace("_", "")
    # Try exact match, then fuzzy substring match
    forbidden: list[str] = _GENRE_FORBIDDEN.get(key, [])
    if not forbidden:
        for k, v in _GENRE_FORBIDDEN.items():
            if k in key or key in k:
                forbidden = v
                break
    if not forbidden:
        return ""
    lines = "\n".join(f"   ✗ {item}" for item in forbidden)
    return (
        f"\n## GENRE-ANKER ({genre.upper()}) — NIEMALS in der Geschichte verwenden:\n"
        f"{lines}\n"
        "Die Welt ist KONSISTENT in ihrem Genre. Wenn der Spieler so etwas vorschlägt,\n"
        "interpretiere es im Genre-Rahmen um (z.B. 'Magie' → 'unbekannte Hochtechnologie' im SciFi).\n"
    )


# ── Locked Identifiers (Anti Name-Drift) ─────────────────────────────────────
def _build_locked_glossary(
    world_state: dict | None,
    characters: list | None = None,
) -> str:
    """Compact list of canonical proper names that sub-LLMs must never alter.
    Includes character names, current location, world items.
    Designed to be injected into all sub-prompts where the LLM might shorten or
    misspell a name (e.g. 'Annika' → 'Anna')."""
    names: list[str] = []
    seen = set()

    def add(n: str):
        if not n:
            return
        n = str(n).strip()
        if not n or n.lower() in seen:
            return
        seen.add(n.lower())
        names.append(n)

    for c in characters or []:
        add(c.get("name", ""))
    if world_state:
        for n in world_state.get("characters_present", []) or []:
            add(n)
        add(world_state.get("location", ""))
        for it in world_state.get("world_items", []) or []:
            if isinstance(it, dict):
                add(it.get("name", ""))
                add(it.get("held_by", ""))
        # Extract canonical place/org names from established_facts
        # German proper nouns after prepositions or in capitalized compound words
        import re as _re
        _STOP_CAPS = {"Der", "Die", "Das", "Ein", "Eine", "Und", "Auch", "Dass",
                      "Nicht", "Wenn", "Wird", "Hat", "Ist", "Von", "Aus", "Mit",
                      "Bei", "Nach", "Vor", "Zur", "Zum", "Durch", "Über"}
        for fact in (world_state.get("established_facts") or [])[:8]:
            # Names after common German prepositions indicating places/orgs
            for token in _re.findall(
                r'(?:in|von|nach|zu|bei|aus|für|durch|um|auf|unter|an)\s+'
                r'([A-ZÄÖÜ][a-zäöüßA-ZÄÖÜ-]{3,}(?:\s+[A-ZÄÖÜ][a-zäöüß-]{3,})?)',
                str(fact)
            ):
                word = token.strip()
                if word not in _STOP_CAPS and len(word) > 4:
                    add(word)

    if not names:
        return ""
    listed = "; ".join(f'"{n}"' for n in names[:25])
    return (
        "🔒 EXAKTE EIGENNAMEN — niemals ändern, kürzen oder umbenennen:\n"
        f"   {listed}\n"
    )


# ── Obstacle-Auto-Decay ───────────────────────────────────────────────────────
_OBSTACLE_IGNORE_THRESHOLD = 2  # Szenen ohne Erwähnung → faded


# ── Multi-LLM Rollen-Auflösung ───────────────────────────────────────────────
_VALID_ROLES = ("storyteller", "director", "cataloger", "choicemaker", "interpreter")


def _resolve_role_model(role: str, llm_cfg: dict, available: list | None, fallback: str) -> str:
    """Picks a role-specific model from global_config (key 'model_<role>').
    Falls back to the main model if unset or not currently available in Ollama."""
    if role not in _VALID_ROLES:
        return fallback
    val = (llm_cfg.get(f"model_{role}") or "").strip()
    if not val:
        return fallback
    if available and val not in available:
        return fallback
    return val


def _decay_obstacles(
    new_state: dict,
    old_state: dict | None,
    scene_text: str,
    player_action: str,
) -> dict:
    """Increase ignore_count for active obstacles that are not mentioned in the
    new scene/player action. After threshold reached, mark them as 'faded' so
    they no longer clog the UI or prompts.
    Pure function — modifies and returns new_state."""
    if not new_state or not isinstance(new_state, dict):
        return new_state
    items = new_state.get("world_items") or []
    if not items:
        return new_state

    haystack = (scene_text or "") + " " + (player_action or "")
    haystack_low = haystack.lower()
    old_items_by_id = {}
    for oi in (old_state or {}).get("world_items", []) or []:
        if isinstance(oi, dict):
            key = (oi.get("id") or oi.get("name") or "").lower()
            if key:
                old_items_by_id[key] = oi

    out = []
    for it in items:
        if not isinstance(it, dict):
            out.append(it)
            continue
        if it.get("type") != "obstacle":
            out.append(it)
            continue
        status = it.get("status", "active")
        if status not in ("active", "triggered"):
            out.append(it)
            continue
        name_low = (it.get("name") or "").lower()
        prev = old_items_by_id.get((it.get("id") or it.get("name") or "").lower(), {})
        prev_ignore = int(prev.get("ignore_count", 0) or 0)
        mentioned = bool(name_low) and name_low in haystack_low
        if mentioned:
            it["ignore_count"] = 0
        else:
            it["ignore_count"] = prev_ignore + 1
            if it["ignore_count"] >= _OBSTACLE_IGNORE_THRESHOLD:
                it["status"] = "faded"
        out.append(it)
    new_state["world_items"] = out
    return new_state


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
    # Story-Direktive (Director-System) — direkt ganz oben sichtbar
    directive = (world_state.get("current_directive") or "").strip()
    beat = (world_state.get("current_beat") or "").strip()
    if directive:
        beat_str = f" [{beat}]" if beat else ""
        lines.append(f"🎯 STORY-ZIEL{beat_str}: {directive}")
        lines.append("   → Mindestens eine Spieler-Option MUSS auf dieses Ziel hinarbeiten.")
        lines.append("")
    if loc:
        lines.append(f"📍 Ort:         {loc}")
    if time_:
        lines.append(f"🕐 Zeit:        {time_}")
    if weather:
        lines.append(f"🌤️ Atmosphäre: {weather}")
    tone = (world_state.get("tone") or "").strip()
    if tone:
        lines.append(f"🎭 Stimmung:    {tone}")
    if chars:
        lines.append(f"👥 Anwesend:    {', '.join(chars)}")
    if facts:
        lines.append("\n📋 Etablierte Fakten — UNVERLÄNDERT beibehalten:")
        for f in facts:
            lines.append(f"   • {f}")

    # World items section
    items = [
        i for i in world_state.get("world_items", [])
        if isinstance(i, dict) and i.get("name") and i.get("status") != "faded"
    ]
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


def build_system_prompt(config: dict, characters: list, output_language: str = "Deutsch", world_state: dict = None, factions: list | None = None) -> str:
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
        # Skills (Phase 5) — int values 0..10 represent proficiency
        skills = c.get("skills", {})
        if isinstance(skills, str):
            try: skills = json.loads(skills)
            except: skills = {}
        if isinstance(skills, dict) and skills:
            def _skill_label(v):
                v = int(v) if isinstance(v, (int, float)) else 0
                if v >= 8: return "meisterhaft"
                if v >= 6: return "geübt"
                if v >= 4: return "solide"
                if v >= 2: return "anfangs"
                return "schwach"
            sk_pairs = ", ".join(
                f"{k} {int(v)}/10 ({_skill_label(v)})"
                for k, v in skills.items() if isinstance(v, (int, float))
            )
            if sk_pairs:
                lines.append(f"🎯 Skills: {sk_pairs}")
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

    # ── Factions (Phase 7) ────────────────────────────────────────────────────
    factions_block = ""
    if factions:
        def _att_label(v: int) -> str:
            v = int(v or 0)
            if v >= 75:  return "treu ergeben"
            if v >= 40:  return "freundlich"
            if v >= 15:  return "wohlwollend"
            if v >= -14: return "neutral"
            if v >= -39: return "misstrauisch"
            if v >= -74: return "feindselig"
            return "todfeindlich"
        flines = ["", "## FRAKTIONEN & GRUPPEN"]
        for f in factions:
            if (f.get("status") or "active") == "dissolved":
                continue
            ap = int(f.get("attitude_player") or 0)
            head = f"### {f.get('name','?')} — Haltung zum Spieler: {ap:+d} ({_att_label(ap)})"
            flines.append(head)
            if f.get("description"):
                flines.append(f"Beschreibung: {f['description']}")
            if f.get("traits"):
                flines.append(f"Charakteristik: {f['traits']}")
            if f.get("goals"):
                flines.append(f"Ziele: {f['goals']}")
            atts = f.get("attitudes") or {}
            if isinstance(atts, dict) and atts:
                pairs = ", ".join(f"{k}: {int(v):+d}" for k, v in atts.items())
                flines.append(f"Beziehungen zu anderen: {pairs}")
        if len(flines) > 2:
            factions_block = "\n".join(flines) + "\n"

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
    anti_drift_block    = _genre_anti_drift_block(genre)

    return f"""Du bist ein erfahrener, kreativer Game Master für ein interaktives Textadventure auf {output_language}.

{world_state_section}## WELT
{world_block}

## SZENARIO
{config.get("scenario", "")}

## GENRE & STORY-RAHMEN
Genre: {genre}
{config.get("story_frame", "")}

{char_descriptions}
{factions_block}
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
6. Verwende NIEMALS die verbotenen Ausdrücke oder verbotenen Worte
7. Beschreibe Charaktere mit ihren spezifischen körperlichen Merkmalen und Kleidung
8. Berücksichtige Alter, Persönlichkeit, Vorlieben und Abneigungen der Charaktere aktiv
9. ATMOSPHÄRE — PFLICHT: Der aktuelle Ort, die Zeit und das Wetter (siehe SPIELZUSTAND oben)
   MÜSSEN aktiv im Story-Text spürbar sein. Beschreibe Lichtverhältnisse passend zur Tageszeit
   (z.B. dämmernd / mittags grell / nächtlich finster), erwähne Wetter-Effekte (Regen prasselt,
   Nebel dämpft Geräusche, Sturm peitscht), und verankere die Szene im genannten Ort. Verändere
   Zeit natürlich entlang der Handlung (Aktionen verbrauchen Zeit — ein Kampf kostet Minuten,
   eine Reise Stunden). Wetter wechselt langsam, nicht abrupt, außer wenn die Handlung das
   auslöst. Spieler-Aktionen wie „warte bis zur Nacht“ oder „ziehe weiter“ lassen Zeit/Ort wechseln.
10. Du KANNST und SOLLST handlungsrelevante Gegenstände, Codes UND Hindernisse in der Welt erfinden:
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
11. SETTING-ANKER: Der Schauplatz darf NIEMALS abrupt wechseln. Eine Ortsänderung
    erfordert IMMER eine explizite Spieler-Aktion (gehen, fahren, kriechen, fliehen, teleportieren).
    Die neue Szene MUSS am Ende der vorigen Szene physisch anschlussfähig sein.
    Beispiel: Wenn die letzte Szene in einem Vault-Eingang endete, kann die nächste NICHT
    in einem Güterzug spielen — es sei denn der Spieler hat sich explizit dorthin bewegt.
12. ANTI-WIEDERHOLUNG: Wenn der Spieler in zwei aufeinanderfolgenden Szenen ähnliche
    Aktionen versucht (untersuchen, analysieren, beobachten ohne Handlung), MUSS in dieser
    dritten Szene ein konkreter Fortschritt stattfinden — Erfolg, Misserfolg ODER eine neue
    Komplikation, die das Pattern zwingend bricht. Niemals dieselbe Aktion 3× ergebnislos.
13. PERSPEKTIV-KONSISTENZ: Bleibe in der EINMAL etablierten Erzählperspektive (in der Regel
    dritte Person, Protagonist im Fokus — er/sie/Name). Wechsle NIEMALS innerhalb des Spiels
    in 'Du'-/'Ihr'-Form, es sei denn die Geschichte adressiert den Spieler explizit.
14. NPC-DETAILS: Wenn ein NPC zum ersten Mal Kleidung/Ausrüstung zeigt oder etwas trägt,
    erwähne diese Details konkret (Stoff, Farbe, sichtbare Items). Diese Details werden
    in der Welt persistent gespeichert und sollten konsistent fortgeführt werden.
15. SKILL-BASIERTE ERFOLGE (unsichtbare Würfelmechanik): Beachte die Skill-Werte (0-10) der
    handelnden Charaktere im Charakter-Block. Wenn eine Aktion klar zu einem Skill passt
    (Schiessen, Schleichen, Verhandlung, Hacken, Klettern, Lockpicking, etc.), entscheide
    Erfolg/Misserfolg implizit nach diesem Wert:
       - Skill 0-2: Aktion misslingt häufig, schwere Komplikationen sind wahrscheinlich
       - Skill 3-5: gemischte Ergebnisse — Teilerfolg, kleine Patzer, Bedingungen
       - Skill 6-7: Erfolg ist wahrscheinlich, aber nicht perfekt — kleine Reibungen
       - Skill 8-10: souveräner Erfolg, oft mit Stil oder zusätzlichem Vorteil
    NIEMALS Würfelwürfe oder Zahlen erwähnen. Erzähle das Ergebnis natürlich aus der Welt
    heraus. Mische gelegentlich Variationen ein (kritischer Patzer auch bei hohem Skill,
    Glückstreffer bei niedrigem Skill — selten). Wenn der Charakter den Skill nicht hat,
    behandele die Aktion als Anfänger (Skill 1-2).
{anti_drift_block}
## OUTPUT FORMAT
Antworte IMMER und AUSSCHLIESSLICH mit genau diesen zwei Feldern im JSON-Format:
{{
  "story": "Vollständiger Szenentext in mehreren Absätzen...",
  "options": ["Entscheidung 1", "Entscheidung 2", "Entscheidung 3"]
}}
Kein anderes Format. Kein anderes JSON-Schema. Nur "story" und "options"."""


def build_user_prompt(player_action: str, recent_events: list, scene_number: int, detail_len: str = "MINDESTENS 3 Absätze", memory_depth: int = 3, unfound_items: list = None, active_obstacles: list = None, recent_options: list = None, directive: str = "") -> str:
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

    # Anti-repeat block: optionen, die der Spieler in den letzten Szenen NICHT
    # gewählt hat, sollen nicht wieder aufgedrängt werden.
    repeat_block = ""
    if recent_options:
        flat = []
        for opts in recent_options[-3:]:
            if isinstance(opts, list):
                for o in opts:
                    if isinstance(o, str) and o.strip():
                        flat.append(o.strip())
        # Nur Optionen, die mehrfach vorkamen (= ignoriert)
        from collections import Counter
        counts = Counter(o.lower() for o in flat)
        sticky = [o for o in flat if counts[o.lower()] >= 2]
        # Dedup, max 6
        seen_low = set()
        sticky_unique = []
        for o in sticky:
            if o.lower() not in seen_low:
                seen_low.add(o.lower())
                sticky_unique.append(o)
        if sticky_unique:
            listed = "\n".join(f'   • "{o}"' for o in sticky_unique[:6])
            repeat_block = (
                "\n\n## 🔄 ANTI-WIEDERHOLUNG — Diese Optionen wurden mehrfach vorgeschlagen "
                "aber nicht gewählt. Schlage sie NICHT erneut vor:\n" + listed +
                "\nBiete stattdessen NEUE, konkretere Aktionen an, die die Geschichte vorantreiben."
            )

    # Direktive für Choicemaker — eine Option muss aufs Ziel hinarbeiten
    directive_block = ""
    if directive:
        directive_block = (
            "\n\n## 🎯 STORY-ZIEL\n"
            f"Aktuelles Mini-Ziel: {directive}\n"
            "MINDESTENS eine der drei Optionen MUSS einen klaren Schritt in Richtung dieses Ziels ermöglichen."
        )

    if scene_number == 1 and not player_action:
        return f"""## SZENE 1 — SPIELSTART
{memory_block}

Beginne die Geschichte. Stelle die Welt, den/die Hauptprotagonisten und die aktuelle Situation vor.
Schreibe eine packende Eröffnungsszene. WICHTIG: Schreibe {detail_len} — nicht kürzer!
Biete am Ende drei erste Entscheidungsmöglichkeiten an.{investigation_block}{obstacle_block}{repeat_block}{directive_block}"""

    return f"""## SZENE {scene_number}
{memory_block}

## SPIELER-AKTION
Der Spieler entscheidet: "{player_action}"

Schreibe die nächste Szene. WICHTIG: Der "story"-Text MUSS {detail_len} lang sein — nicht kürzer!
Berücksichtige alle bisherigen Ereignisse und die Charaktereigenschaften.{investigation_block}{obstacle_block}{repeat_block}{directive_block}"""


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
    glossary: str = "",
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
        "Schema: {\"location\":\"...\",\"time\":\"...\",\"weather\":\"...\",\"tone\":\"calm|tense|grim|hopeful|mysterious|romantic|epic\","
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
        "established_facts: max 8 kurze Fakten. Keine Erklärungen. "
        "tone: ein einzelnes Wort aus {calm,tense,grim,hopeful,mysterious,romantic,epic} — die emotionale Grundstimmung der Szene. "
        "WICHTIG fortschreitende Zeit: Wenn der Spielertext Aktionen mit Zeit-Wirkung enthält (warten, schlafen, reisen, kämpfen, untersuchen), passe 'time' an (z.B. 'früher Morgen' → 'Mittag' → 'Abend' → 'Nacht')."
    )
    user_prompt = (
        f"{glossary}"
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

        # ── Rescue: Item-at-Root-Bug ─────────────────────────────────────────
        # The LLM sometimes returns an item object at the root level (with id/name/type)
        # instead of a world-state object. Detect and rescue nested world_items/facts.
        _WS_KEYS = {"location", "time", "weather", "characters_present", "established_facts", "tone"}
        has_world_keys = bool(_WS_KEYS & set(parsed.keys()))
        looks_like_item = (
            parsed.get("type") in ("item", "code", "obstacle")
            or (
                "id" in parsed
                and "name" in parsed
                and ("description" in parsed or "descripiont" in parsed)
                and not has_world_keys
            )
        )
        if looks_like_item:
            parsed = {
                "location": old_location,
                "established_facts": parsed.get("established_facts", old_facts),
                "world_items": parsed.get("world_items", old_items),
            }

        # ── Location sanity: reject item-position strings as story location ──
        _ITEM_LOC_HINTS = (
            "in der hand", "im besitz", "bei ", "in der tasche",
            "getragen von", "held by", "in inventory", "auf dem tisch",
        )
        loc_lower = (parsed.get("location") or "").lower()
        if any(hint in loc_lower for hint in _ITEM_LOC_HINTS):
            parsed["location"] = old_location

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


async def _extract_faction_changes(
    story_text: str,
    factions: list,
    model: str,
    num_ctx: int = 4096,
    output_language: str = "Deutsch",
) -> list:
    """Phase 7: Cataloger detects faction-level shifts (rep/attitude/status) per scene.
    Returns list of {name, attitude_player_delta, attitudes_changes, status, new}.
    Skips silently if no factions exist."""
    if not factions:
        return []
    fac_lines = []
    for f in factions:
        ap = int(f.get("attitude_player") or 0)
        fac_lines.append(f"- {f.get('name','?')} (zum Spieler {ap:+d}, Status {f.get('status','active')})")
    fac_summary = "\n".join(fac_lines)
    sys_prompt = (
        "Du bist ein Fraktions-Analysator fuer ein Textadventure. "
        "Antworte NUR mit JSON-Array (kein Markdown). "
        "Schema: [{\"name\":\"Fraktion\",\"attitude_player_delta\":-5..+5,"
        "\"attitudes_changes\":{\"AndereFraktion\":-3..+3},\"status\":\"active|hostile|allied|dissolved\","
        "\"new\":false,\"description\":\"...\"}] "
        "REGELN: Aenderungen nur, wenn der Szenentext klar ein Verhalten ODER Ereignis "
        "zeigt, das die Beziehung beeinflusst. Werte in kleinen Schritten (-5..+5 fuer Spieler, "
        "-3..+3 unter Fraktionen). Wenn nichts geschieht: leeres Array. "
        "Wenn die Szene eine NEUE Fraktion einfuehrt, setze \"new\":true und fuelle description. "
        "Maximal 4 Eintraege pro Szene. Keine Erklaerungen."
    )
    user_prompt = (
        f"Bekannte Fraktionen:\n{fac_summary}\n\n"
        f"=== SZENENTEXT ===\n{story_text[:2000]}\n\n"
        f"Welche Fraktions-Aenderungen sind plausibel? Sprache: {output_language}."
    )
    payload = {
        "model": model,
        "system": sys_prompt,
        "prompt": user_prompt,
        "stream": False,
        "format": "json",
        "options": {"temperature": 0.1, "num_predict": 350, "num_ctx": num_ctx},
    }
    try:
        async with httpx.AsyncClient(timeout=40.0) as client:
            resp = await client.post(OLLAMA_URL, json=payload)
            resp.raise_for_status()
            raw = resp.json().get("response", "")
        s = raw.strip()
        if s.startswith("["):
            try:
                arr = json.loads(s)
                if isinstance(arr, list):
                    return [x for x in arr if isinstance(x, dict) and x.get("name")]
            except Exception:
                pass
        parsed = _extract_json(raw)
        if isinstance(parsed, list):
            return [x for x in parsed if isinstance(x, dict) and x.get("name")]
        if isinstance(parsed, dict):
            for v in parsed.values():
                if isinstance(v, list):
                    return [x for x in v if isinstance(x, dict) and x.get("name")]
        return []
    except Exception:
        return []


async def _extract_character_states(
    story_text: str,
    characters: list,
    model: str,
    num_ctx: int = 4096,
    output_language: str = "Deutsch",
    glossary: str = "",
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
        sk = c.get("skills") or {}
        if isinstance(sk, str):
            try: sk = json.loads(sk)
            except: sk = {}
        sk_text = ", ".join(f"{k}:{int(v)}" for k, v in sk.items() if isinstance(v, (int, float)))[:120] or "—"
        char_lines.append(f"- {c['name']} | Trägt: {clothing} | Inventar: {inv_text} | Skills: {sk_text}")
    char_summary = "\n".join(char_lines)
    sys_prompt = (
        "Du bist ein Zustandsanalysator für Charaktere in einem Textadventure. "
        "Antworte NUR mit einem JSON-Array (kein Markdown). "
        "Schema: [{\"name\":\"...\",\"current_clothing\":\"...\","
        "\"inventory\":[\"Gegenstand1\",\"Gegenstand2\"],"
        "\"new_experiences\":[\"Kurze Beschreibung\"],"
        "\"skill_changes\":{\"SkillName\":+1,\"AndererSkill\":-1}}] "
        "WICHTIG: Erfasse ALLE Charaktere — auch NPCs — bei DEREN ERSTEM AUFTRETEN, "
        "wenn der Szenentext ihre Kleidung, Ausrüstung oder sichtbares Inventar beschreibt. "
        "Wenn current_clothing eines Charakters bisher leer ist und der Text Kleidung erwähnt, "
        "MUSST du diesen Charakter mit der beschriebenen Kleidung zurückgeben. "
        "inventory: vollstaendige aktuelle Liste (max 15 Eintraege). "
        "new_experiences: nur wirklich neue Ereignisse, max 2 pro Szene, kurze Saetze. "
        "skill_changes: NUR wenn der Charakter im Szenentext einen relevanten Skill aktiv "
        "anwendet ODER deutlich versagt. +1 fuer klaren Erfolg/Uebung, -1 fuer schweren "
        "Misserfolg. Verwende nur Skills, die der Charakter bereits hat (siehe oben). "
        "Hoechstens 1 Skill-Aenderung pro Charakter pro Szene. Leer lassen wenn unklar. "
        "Leere Felder als leeren String oder leere Liste. Keine Erlaeuterungen."
    )
    user_prompt = (
        f"{glossary}"
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
        # Items with a known owner are already "found" — skip them
        if item.get("held_by"):
            continue
        if itype == "code" and status == "unknown":
            result.append(item)
        elif itype != "code" and status == "available":
            result.append(item)
    return result


def _get_active_obstacles(world_state: dict | None) -> list:
    """Return obstacles that are currently active (unresolved threats).
    Skips faded (auto-decayed), overcome and avoided obstacles."""
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
    option is present, inject one based on an actual item/obstacle location.
    SKIPPED if a story directive is present (Director-System leads instead)."""
    if world_state and (world_state.get("current_directive") or "").strip():
        return options
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


# ── Multi-Stage Pipeline Helpers ──────────────────────────────────────────────

async def _interpret_action(
    player_action: str,
    world_state: dict | None,
    recent_events: list,
    model: str,
    output_language: str = "Deutsch",
) -> str:
    """Stage 1: Clean up player input — fix typos in character names, sharpen unclear actions.
    Returns a single clean sentence describing what the player intends to do.
    For empty/menu/start actions, returns the input unchanged."""
    pa = (player_action or "").strip()
    if not pa or pa.lower() in ("[spielstart]", "spielstart", "[start]", "start"):
        return pa
    # Skip cleaning for very short menu-style picks (numbers, single words)
    if len(pa) < 4:
        return pa

    char_names = []
    if world_state:
        char_names = [c for c in (world_state.get("characters_present") or []) if isinstance(c, str)]
    char_hint = ", ".join(char_names[:6]) if char_names else "(keine bekannt)"

    last_excerpt = ""
    if recent_events:
        last = recent_events[-1].get("story_text", "") or ""
        last_excerpt = last[-300:]

    sys_prompt = (
        f"Du bist ein Eingabe-Korrektor für ein Textadventure auf {output_language}. "
        "Korrigiere Tippfehler in der Spieleraktion (insbesondere Charakternamen aus der "
        "Liste anwesender Charaktere). Reformuliere unklare Aktionen in EINEN klaren Satz "
        "aus Sicht der Spieler-Eingabe. Behalte den Sinn bei. Erfinde nichts dazu. "
        "WICHTIG: Eigennamen aus der Liste anwesender Charaktere NIEMALS verändern, kürzen oder durch ähnliche Namen ersetzen. "
        "Antworte NUR mit dem korrigierten Satz — KEIN JSON, keine Erklärung, keine Anführungszeichen."
    )
    user_prompt = (
        f"Anwesende Charaktere: {char_hint}\n"
        f"Letzter Szenen-Auszug:\n{last_excerpt}\n\n"
        f"Spieler-Eingabe:\n{pa}\n\n"
        f"Korrigierter Satz:"
    )
    payload = {
        "model": model,
        "system": sys_prompt,
        "prompt": user_prompt,
        "stream": False,
        "options": {"temperature": 0.2, "num_predict": 80, "num_ctx": 1024},
    }
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(OLLAMA_URL, json=payload)
            resp.raise_for_status()
            raw = (resp.json().get("response") or "").strip()
        # Take first non-empty line, strip wrapping quotes
        line = next((l.strip() for l in raw.splitlines() if l.strip()), "")
        line = line.strip('"').strip("'").strip()
        if 3 <= len(line) <= 400:
            return line
    except Exception:
        pass
    return pa


async def _build_recap(
    recent_events: list,
    world_state: dict | None,
    model: str,
    depth: int = 4,
    output_language: str = "Deutsch",
) -> str:
    """Stage 2: Build a 3-4 sentence recap of the recent events.
    Replaces verbose event-dumping in the user prompt for older scenes.
    Returns empty string for very early scenes (recap not useful yet)."""
    if not recent_events or len(recent_events) < 2:
        return ""
    # Take the last `depth` events for compact summarization
    slice_evts = recent_events[-depth:]
    bullets = []
    for ev in slice_evts:
        action = ev.get("interpreted_action") or ev.get("player_action") or ""
        text = (ev.get("story_text") or "")[:400]
        bullets.append(f"S{ev.get('scene_number','?')} [{action[:60]}]: {text}")
    joined = "\n\n".join(bullets)

    loc = (world_state or {}).get("location", "")
    chars = ", ".join((world_state or {}).get("characters_present", [])[:5])
    facts = "; ".join((world_state or {}).get("established_facts", [])[:5])

    sys_prompt = (
        f"Du fasst eine laufende Geschichte für einen Game Master zusammen. Sprache: {output_language}. "
        "Schreibe genau 3-4 prägnante Sätze, die enthalten: aktuellen Ort, anwesende Charaktere, "
        "offene Konflikte/Spannungsbögen, und den letzten kritischen Wendepunkt. "
        "WICHTIG: Eigennamen NIEMALS verändern, kürzen oder ersetzen — nutze die Schreibweise aus dem Kontext exakt. "
        "Keine Floskeln, keine Wiederholungen. Antworte NUR mit der Zusammenfassung — kein JSON."
    )
    user_prompt = (
        f"Aktueller Ort: {loc}\nAnwesend: {chars}\nWichtige Fakten: {facts}\n\n"
        f"=== LETZTE SZENEN ===\n{joined}\n\n"
        f"Zusammenfassung in 3-4 Sätzen:"
    )
    payload = {
        "model": model,
        "system": sys_prompt,
        "prompt": user_prompt,
        "stream": False,
        "options": {"temperature": 0.3, "num_predict": 250, "num_ctx": 3072},
    }
    try:
        async with httpx.AsyncClient(timeout=40.0) as client:
            resp = await client.post(OLLAMA_URL, json=payload)
            resp.raise_for_status()
            raw = (resp.json().get("response") or "").strip()
        # Cleanup: take first paragraph only, max 600 chars
        para = raw.split("\n\n")[0].strip()
        if len(para) > 600:
            para = para[:600].rsplit(".", 1)[0] + "."
        return para
    except Exception:
        return ""


async def _coherence_check(
    scene_text: str,
    world_state: dict | None,
    recap: str,
    char_names: list,
    genre: str,
    model: str,
) -> dict:
    """Stage 4: Check generated scene for consistency violations.
    Returns {'score': 1-10, 'issues': ['...']}. Score >=6 means acceptable."""
    if not scene_text or len(scene_text) < 100:
        return {"score": 10, "issues": []}

    loc = (world_state or {}).get("location", "")
    present = ", ".join((world_state or {}).get("characters_present", [])[:5])
    forbidden = _GENRE_FORBIDDEN.get(
        (genre or "").lower().replace(" ", "").replace("-", "").replace("_", ""),
        []
    )
    forbidden_text = "; ".join(forbidden) if forbidden else "(keine spezifischen Tropen)"

    sys_prompt = (
        "Du bist ein Qualitäts-Prüfer für Game-Master-Szenen. Antworte NUR mit JSON: "
        "{\"score\":1-10,\"issues\":[\"kurzer Bruch1\",\"kurzer Bruch2\"]}. "
        "Score 10=perfekt konsistent, 1=völliger Bruch. "
        "Prüfe diese Aspekte: "
        "(a) Setting-Anker: Wechselt der Ort abrupt ohne Bewegung des Spielers? "
        "(b) Charakter-Konsistenz: Sind Namen korrekt geschrieben? Tauchen unbekannte Personen unmotiviert auf? "
        "(c) Genre-Treue: Verbotene Tropen (siehe Liste) im Text? "
        "(d) Perspektiv-Konsistenz: Bleibt die Erzählperspektive gleich? "
        "(e) Wiederholung: Wird das gleiche Pattern der Vor-Szene erneut ohne Fortschritt durchgespielt? "
        "Sei streng, aber nicht pedantisch. Eine kleine stilistische Variation ist KEIN Bruch."
    )
    user_prompt = (
        f"Bisheriger Ort laut Welt: {loc}\n"
        f"Anwesende Chars laut Welt: {present}\n"
        f"Genre: {genre}\n"
        f"Verbotene Tropen: {forbidden_text}\n"
        f"Recap der bisherigen Story:\n{recap or '(noch keiner)'}\n\n"
        f"=== NEUE SZENE ===\n{scene_text[:2500]}\n\n"
        f"JSON-Bewertung:"
    )
    payload = {
        "model": model,
        "system": sys_prompt,
        "prompt": user_prompt,
        "stream": False,
        "format": "json",
        "options": {"temperature": 0.1, "num_predict": 250, "num_ctx": 3072},
    }
    try:
        async with httpx.AsyncClient(timeout=40.0) as client:
            resp = await client.post(OLLAMA_URL, json=payload)
            resp.raise_for_status()
            raw = (resp.json().get("response") or "").strip()
        parsed = _extract_json(raw)
        if isinstance(parsed, dict):
            score = parsed.get("score", 10)
            try:
                score = int(score)
            except Exception:
                score = 10
            issues = parsed.get("issues") or []
            if not isinstance(issues, list):
                issues = []
            return {"score": max(1, min(10, score)), "issues": [str(i)[:200] for i in issues[:5]]}
    except Exception:
        pass
    return {"score": 10, "issues": []}


# ── Director-System: long-term story directive ───────────────────────────────
_BEAT_CYCLE = ["SETUP", "KONFLIKT", "ESKALATION", "AUFLÖSUNG", "NEUE_FRAGE"]


def _next_beat(current: str) -> str:
    if not current:
        return _BEAT_CYCLE[0]
    try:
        idx = _BEAT_CYCLE.index(current.upper())
        return _BEAT_CYCLE[(idx + 1) % len(_BEAT_CYCLE)]
    except ValueError:
        return _BEAT_CYCLE[0]


def _should_update_director(world_state: dict | None, scene_number: int) -> bool:
    """Director läuft bei Spielstart oder alle 3 Szenen."""
    if scene_number <= 1:
        return True
    if not world_state:
        return True
    if not (world_state.get("current_directive") or "").strip():
        return True
    last_update = int(world_state.get("directive_updated_at_scene", 0) or 0)
    return (scene_number - last_update) >= 3


async def _update_director(
    world_state: dict | None,
    recent_events: list,
    config: dict,
    characters: list,
    model: str,
    scene_number: int,
    output_language: str = "Deutsch",
) -> dict:
    """Generate a new story directive (mini-goal) + beat tag.
    Returns {"directive": "...", "beat": "..."} or {} on failure."""
    glossary = _build_locked_glossary(world_state, characters)
    story_frame = (config.get("story_frame") or "").strip()
    scenario    = (config.get("scenario") or "").strip()
    genre       = config.get("story_genre_custom") or config.get("story_genre", "")

    prev_beat = (world_state or {}).get("current_beat", "")
    suggested_beat = _next_beat(prev_beat) if prev_beat else "SETUP"

    loc = (world_state or {}).get("location", "")
    chars_present = ", ".join((world_state or {}).get("characters_present", [])[:5])
    facts = "; ".join((world_state or {}).get("established_facts", [])[:5])

    # last 3 player actions + outcomes
    history = []
    for ev in (recent_events or [])[-3:]:
        a = (ev.get("interpreted_action") or ev.get("player_action") or "")[:80]
        t = (ev.get("story_text") or "")[:200]
        history.append(f"S{ev.get('scene_number','?')} [{a}]: {t}")
    history_text = "\n\n".join(history) if history else "(noch keine Szenen)"

    sys_prompt = (
        f"Du bist der Story-Director eines {genre}-Textadventures auf {output_language}. "
        "Deine Aufgabe: ein KURZES, KONKRETES Mini-Ziel ('directive') für die nächsten 2-3 Szenen "
        "definieren, das die Geschichte voranbringt — KEIN abstrakter Wunsch, sondern eine "
        "konkrete nächste Hürde, ein nächstes Ziel oder eine zu klärende Frage. "
        "Beats: SETUP=Welt/Konflikt einführen, KONFLIKT=erstes Problem aufbauen, "
        "ESKALATION=Konflikt zuspitzen, AUFLÖSUNG=Konflikt lösen, NEUE_FRAGE=neuer Aufhänger. "
        "Antworte NUR mit JSON: {\"directive\":\"konkretes Ziel in 1 Satz\",\"beat\":\"<BEAT>\"}. "
        "Sprache der directive: " + output_language + ". Keine Anführungszeichen-Verschachtelung."
    )
    user_prompt = (
        f"{glossary}"
        f"GENRE: {genre}\n"
        f"STORY-FRAME: {story_frame or '(kein expliziter Frame)'}\n"
        f"SZENARIO: {scenario or '(generisch)'}\n\n"
        f"AKTUELLER ORT: {loc}\n"
        f"ANWESEND: {chars_present}\n"
        f"WICHTIGE FAKTEN: {facts}\n"
        f"VORIGER BEAT: {prev_beat or '(keiner)'}\n"
        f"VORGESCHLAGENER NÄCHSTER BEAT: {suggested_beat}\n\n"
        f"=== LETZTE SZENEN ===\n{history_text}\n\n"
        f"Aktuelle Direktive (kann verfeinert werden): "
        f"{(world_state or {}).get('current_directive', '(keine)')}\n\n"
        f"Erstelle ein konkretes Mini-Ziel für die nächsten 2-3 Szenen + den passenden Beat."
    )
    payload = {
        "model": model,
        "system": sys_prompt,
        "prompt": user_prompt,
        "stream": False,
        "format": "json",
        "options": {"temperature": 0.4, "num_predict": 200, "num_ctx": 3072},
    }
    try:
        async with httpx.AsyncClient(timeout=40.0) as client:
            resp = await client.post(OLLAMA_URL, json=payload)
            resp.raise_for_status()
            raw = (resp.json().get("response") or "").strip()
        parsed = _extract_json(raw)
        if isinstance(parsed, dict):
            d = (parsed.get("directive") or "").strip()
            b = (parsed.get("beat") or suggested_beat).strip().upper()
            if d:
                if b not in _BEAT_CYCLE:
                    b = suggested_beat
                return {"directive": d[:300], "beat": b}
    except Exception:
        pass
    return {}


async def generate_scene(
    player_action: str,
    scene_number: int,
    story_id: int,
    progress=None,
) -> dict:
    """Multi-stage generation pipeline:
       1. interpret_action  (clean typos, sharpen intent)
       2. build_recap       (compact summary of recent events)
       3. main scene LLM    (the actual GM call)
       4. coherence_check   (score, re-roll once if < 6)
       5. extract_world + extract_character_states (in parallel)

    `progress` is an optional async callable progress(phase: str, label: str)
    that gets called at every stage transition for SSE streaming.
    """
    import asyncio

    async def _emit(phase: str, label: str):
        if progress is None:
            return
        try:
            res = progress(phase, label)
            if asyncio.iscoroutine(res):
                await res
        except Exception:
            pass

    config     = get_story_config(story_id)
    characters = get_characters(story_id)
    factions   = get_factions(story_id)
    llm_cfg    = get_llm_config()

    memory_depth    = int(llm_cfg.get("memory_depth", "3"))
    cfg_num_predict = int(llm_cfg.get("num_predict", "1600"))
    num_ctx         = int(llm_cfg.get("num_ctx", "4096"))
    output_language = llm_cfg.get("output_language", "Deutsch")
    embedding_model = (llm_cfg.get("embedding_model") or "").strip()
    memory_top_k    = int(llm_cfg.get("memory_top_k", "3") or "3")

    world_state = get_world_state(story_id)
    events      = get_recent_events(story_id, limit=max(memory_depth, 8))

    model = llm_cfg.get("ollama_model", "")
    if not model or model == "llama3":
        tags = await check_ollama_connection()
        available = tags.get("models", [])
        model = available[0] if available else "llama3"
    else:
        try:
            tags = await check_ollama_connection()
            available = tags.get("models", [])
        except Exception:
            available = []
    # Role-specific overrides — empty/unavailable → fall back to main `model`
    storyteller_model = _resolve_role_model("storyteller", llm_cfg, available, model)
    director_model    = _resolve_role_model("director",    llm_cfg, available, model)
    cataloger_model   = _resolve_role_model("cataloger",   llm_cfg, available, model)
    choicemaker_model = _resolve_role_model("choicemaker", llm_cfg, available, model)
    interpreter_model = _resolve_role_model("interpreter", llm_cfg, available, model)
    # The main story-call uses the storyteller model
    model = storyteller_model
    temp     = float(llm_cfg.get("temperature", "0.7"))
    top_p    = float(llm_cfg.get("top_p", "0.9"))
    rep_pen  = float(llm_cfg.get("repeat_penalty", "1.1"))
    genre    = config.get("story_genre_custom") or config.get("story_genre", "Fantasy")

    # ── STAGE 1: Interpret action ────────────────────────────────────────────
    await _emit("interpret", "🧠 Aktion verstehen")
    cleaned_action = await _interpret_action(
        player_action, world_state, events, interpreter_model, output_language
    )

    # ── STAGE 1b: Director update (every 3 scenes or when missing) ──────────
    if _should_update_director(world_state, scene_number):
        await _emit("director", "🎯 Story-Ziel aktualisieren")
        director = await _update_director(
            world_state, events, config, characters, director_model, scene_number, output_language
        )
        if director.get("directive"):
            world_state = world_state or {}
            world_state["current_directive"] = director["directive"]
            world_state["current_beat"] = director.get("beat", "")
            world_state["directive_updated_at_scene"] = scene_number
            try:
                save_world_state(story_id, world_state)
            except Exception:
                pass

    directive_text = (world_state or {}).get("current_directive", "") or ""

    # ── STAGE 2: Build recap (only useful from scene 3 onward) ───────────────
    recap_text = ""
    if scene_number >= 3 and len(events) >= 2:
        await _emit("recap", "📜 Kontext zusammenfassen")
        recap_text = await _build_recap(events, world_state, cataloger_model, depth=memory_depth + 1, output_language=output_language)

    # Collect unfound items/codes & active obstacles (mandatory hints)
    unfound_items    = _get_unfound_items(world_state)
    active_obstacles = _get_active_obstacles(world_state)

    # Recent options (last 3 sets) for anti-repeat block
    recent_options = []
    for ev in (events or [])[-3:]:
        opts = ev.get("options_json") or ev.get("options") or []
        if isinstance(opts, str):
            try:
                opts = json.loads(opts)
            except Exception:
                opts = []
        if isinstance(opts, list):
            recent_options.append(opts)

    # Detect repeated generic-explore player actions (stall detection)
    _GENERIC_EXPLORE_TRIGGERS = (
        "erkunde", "untersuche die umgebung", "schaue mich um", "schau mich um",
        "beobachte", "look around", "explore", "sehe mich um", "sieh dich um",
    )
    last_player_actions = [
        (ev.get("interpreted_action") or ev.get("player_action") or "").lower()
        for ev in (events or [])[-3:]
    ]
    generic_explore_count = sum(
        1 for a in last_player_actions
        if any(g in a for g in _GENERIC_EXPLORE_TRIGGERS)
    )
    _force_progress = generic_explore_count >= 2

    system_prompt = build_system_prompt(config, characters, output_language, world_state, factions=factions)
    _detail_map = {
        "niedrig": "1 Absatz",
        "mittel":  "1–2 Absätze",
        "hoch":    "MINDESTENS 3 vollständige Absätze, gerne 4",
    }
    detail_len  = _detail_map.get(config.get("detail_level", "hoch"), "MINDESTENS 3 Absätze")
    user_prompt = build_user_prompt(
        cleaned_action, events, scene_number, detail_len,
        memory_depth, unfound_items, active_obstacles,
        recent_options=recent_options, directive=directive_text,
    )
    # Force-progress injection: wenn 2+ generische Erkundungsaktionen in Folge
    if _force_progress:
        user_prompt += (
            "\n\n## 🚨 ERZWUNGENER FORTSCHRITT\n"
            "In den letzten 2 oder mehr Szenen hat der Spieler nur die Umgebung erkundet "
            "ohne konkreten Fortschritt. Diese Szene MUSS einen Wendepunkt bringen:\n"
            "Entweder eine Entdeckung, ein NPC-Kontakt, ein Zwischenfall oder eine "
            "direkte Konsequenz des Erkundens (z.B. entdeckt werden, einen Hinweis finden, "
            "eine Falle auslösen, eine Tür finden). Beschreibe NIEMALS wieder ergebnislos "
            "die Umgebung. Biete konkrete, handlungsauslösende Optionen an."
        )
    # Always inject locked glossary so storyteller can't drift on names
    glossary = _build_locked_glossary(world_state, characters)
    if glossary:
        user_prompt = glossary + "\n" + user_prompt
    if recap_text:
        user_prompt = (
            f"## RECAP DER BISHERIGEN STORY\n{recap_text}\n\n" + user_prompt
        )

    # ── Memory recall (Phase 6) ────────────────────────────────────────────
    try:
        recall_query = (cleaned_action or "").strip()
        if recap_text:
            recall_query = (recap_text[:400] + "\n\n" + recall_query).strip()
        if recall_query and memory_top_k > 0:
            await _emit("recall", "🧠 Erinnerungen abrufen")
            mems = await recall(story_id, recall_query, top_k=memory_top_k,
                                model=embedding_model, exclude_recent=1)
            if mems:
                lines = ["## RELEVANTE ERINNERUNGEN (aus früheren Szenen)"]
                for m in mems:
                    sn = m.get("scene_number") or "?"
                    lines.append(f"- [Szene {sn}] {m.get('text','').strip()}")
                user_prompt = "\n".join(lines) + "\n\n" + user_prompt
    except Exception:
        pass

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
            "interpreted_player_action": parsed.get("interpreted_player_action", cleaned_action),
        }

    # ── STAGE 3: Main scene generation ───────────────────────────────────────
    await _emit("generate", "✍️ Szene schreiben")
    raw = await _call_ollama(system_prompt, user_prompt)
    result = _parse_result(raw)

    # ── STAGE 4: Coherence check + optional re-roll ──────────────────────────
    coherence: dict = {"score": 10, "issues": []}
    if result and scene_number >= 3 and len(result.get("story", "")) >= 200:
        await _emit("check", "🔍 Konsistenz prüfen")
        char_names = [c.get("name", "") for c in characters]
        coherence = await _coherence_check(
            result["story"], world_state, recap_text, char_names, genre, cataloger_model
        )
        if coherence.get("score", 10) < 6 and coherence.get("issues"):
            # Re-roll once with concrete violation hints baked into the prompt
            await _emit("reroll", "🔁 Szene überarbeiten")
            issues_text = "\n".join(f"   - {i}" for i in coherence["issues"][:5])
            harden = (
                "\n\n## ⚠️ DIE LETZTE SZENE WURDE WEGEN INKONSISTENZEN ABGELEHNT\n"
                "Schreibe sie neu und vermeide diese Brüche:\n"
                f"{issues_text}\n"
                "Bleibe strikt im etablierten Ort, Genre und Erzähl-Perspektive."
            )
            raw_retry = await _call_ollama(system_prompt + harden, user_prompt)
            result_retry = _parse_result(raw_retry)
            if result_retry and len(result_retry.get("story", "")) >= 200:
                # Verify retry actually improved
                retry_check = await _coherence_check(
                    result_retry["story"], world_state, recap_text, char_names, genre, cataloger_model
                )
                if retry_check.get("score", 10) > coherence.get("score", 10):
                    result = result_retry
                    coherence = retry_check

    # ── STAGE 5: Extract world + character states (parallel) ─────────────────
    if result and len(result.get("story", "")) >= 150:
        await _emit("extract", "📦 Welt aktualisieren")
        new_ws_task   = _extract_world_state(result["story"], world_state, config, cataloger_model, num_ctx, output_language, glossary=glossary)
        char_upd_task = _extract_character_states(result["story"], characters, cataloger_model, num_ctx, output_language, glossary=glossary)
        fac_upd_task  = _extract_faction_changes(result["story"], factions, cataloger_model, num_ctx, output_language)
        new_ws, char_updates, fac_updates = await asyncio.gather(new_ws_task, char_upd_task, fac_upd_task, return_exceptions=False)
        if new_ws:
            new_ws["scene"] = scene_number
            # Preserve directive across extraction (extractor doesn't know about it)
            if (world_state or {}).get("current_directive"):
                new_ws["current_directive"] = world_state.get("current_directive", "")
                new_ws["current_beat"] = world_state.get("current_beat", "")
                new_ws["directive_updated_at_scene"] = world_state.get("directive_updated_at_scene", scene_number)
            # Auto-decay obstacles that were not mentioned/touched
            new_ws = _decay_obstacles(new_ws, world_state, result.get("story", ""), cleaned_action)
            save_world_state(story_id, new_ws)
        for upd in (char_updates or []):
            update_character_state(
                story_id, upd["name"],
                current_clothing=upd.get("current_clothing"),
                inventory=upd.get("inventory"),
                new_experiences=upd.get("new_experiences"),
                skill_changes=upd.get("skill_changes"),
            )
        # Apply faction changes (Phase 7)
        for fu in (fac_updates or []):
            try:
                update_faction_state(
                    story_id, fu["name"],
                    attitude_player_delta=fu.get("attitude_player_delta"),
                    attitudes_changes=fu.get("attitudes_changes"),
                    status=fu.get("status") if fu.get("status") in ("active","hostile","allied","dissolved") else None,
                )
            except Exception:
                pass
        _ws = new_ws or world_state or {}
        result["world_items"]         = _ws.get("world_items", [])
        result["location"]            = _ws.get("location", "") or ""
        result["time"]                = _ws.get("time", "") or ""
        result["weather"]             = _ws.get("weather", "") or ""
        result["tone"]                = _ws.get("tone", "") or ""
        result["established_facts"]   = _ws.get("established_facts", []) or []
        result["characters_present"]  = _ws.get("characters_present", []) or []
        result["current_directive"]   = _ws.get("current_directive", "") or ""
        result["current_beat"]        = _ws.get("current_beat", "") or ""
        result["options"]             = _ensure_investigation_options(result["options"], _ws, output_language)
        result["coherence_score"]     = coherence.get("score", 10)
        result["coherence_issues"]    = coherence.get("issues", [])
        result["recap"]               = recap_text
        result["interpreted_player_action"] = cleaned_action
        # Persist a short memory of this scene for future recall
        try:
            mem_text_parts = []
            if cleaned_action:
                mem_text_parts.append(f"Aktion: {cleaned_action}")
            if recap_text:
                mem_text_parts.append(f"Recap: {recap_text}")
            else:
                # Use first ~280 chars of story as scene memory
                story_snip = (result.get("story") or "")[:280].strip()
                if story_snip:
                    mem_text_parts.append(f"Szene: {story_snip}")
            mem_text = "\n".join(mem_text_parts).strip()
            if mem_text:
                await remember(story_id, mem_text, scene_number=scene_number,
                               kind="scene", model=embedding_model)
        except Exception:
            pass
        await _emit("done", "✅ Fertig")
        return result

    # ── Fallback: minimal prompt for tiny / overloaded models ────────────────
    await _emit("fallback", "🔧 Fallback-Modus")
    protagonists = [c for c in characters if c.get("is_protagonist")]
    prot_name = protagonists[0]["name"] if protagonists else "der Held"
    world = config.get("world_name", "eine unbekannte Welt")
    fallback_system = (
        f"Du bist ein Game Master für ein {genre}-Textadventure in {world}. "
        f"Hauptprotagonist: {prot_name}. "
        f"Schreibe ausschließlich auf {output_language}. "
        f"Antworte nur mit JSON: {{\"story\": \"...\", \"options\": [\"a\", \"b\", \"c\"]}}"
    )
    action_desc = cleaned_action or "Beginne die Geschichte."
    fallback_user = (
        f"Szene {scene_number}: {action_desc}\n"
        f"Schreibe 2–3 Absätze immersiven Storytime-Text auf {output_language}."
    )
    raw2 = await _call_ollama(fallback_system, fallback_user, num_predict=max(cfg_num_predict // 2, 400))
    result2 = _parse_result(raw2)
    if result2:
        await _emit("extract", "📦 Welt aktualisieren")
        new_ws = await _extract_world_state(result2["story"], world_state, config, cataloger_model, num_ctx, output_language, glossary=glossary)
        if new_ws:
            new_ws["scene"] = scene_number
            if (world_state or {}).get("current_directive"):
                new_ws["current_directive"] = world_state.get("current_directive", "")
                new_ws["current_beat"] = world_state.get("current_beat", "")
                new_ws["directive_updated_at_scene"] = world_state.get("directive_updated_at_scene", scene_number)
            new_ws = _decay_obstacles(new_ws, world_state, result2.get("story", ""), cleaned_action)
            save_world_state(story_id, new_ws)
        char_updates = await _extract_character_states(result2["story"], characters, cataloger_model, num_ctx, output_language, glossary=glossary)
        for upd in char_updates:
            update_character_state(
                story_id, upd["name"],
                current_clothing=upd.get("current_clothing"),
                inventory=upd.get("inventory"),
                new_experiences=upd.get("new_experiences"),
                skill_changes=upd.get("skill_changes"),
            )
        _ws = new_ws or world_state or {}
        result2["world_items"]         = _ws.get("world_items", [])
        result2["location"]            = _ws.get("location", "") or ""
        result2["time"]                = _ws.get("time", "") or ""
        result2["weather"]             = _ws.get("weather", "") or ""
        result2["tone"]                = _ws.get("tone", "") or ""
        result2["established_facts"]   = _ws.get("established_facts", []) or []
        result2["characters_present"]  = _ws.get("characters_present", []) or []
        result2["current_directive"]   = _ws.get("current_directive", "") or ""
        result2["current_beat"]        = _ws.get("current_beat", "") or ""
        result2["options"]             = _ensure_investigation_options(result2["options"], _ws, output_language)
        result2["coherence_score"]     = 0
        result2["coherence_issues"]    = ["Fallback-Modus aktiviert"]
        result2["recap"]               = recap_text
        result2["interpreted_player_action"] = cleaned_action
        try:
            snip = (result2.get("story") or "")[:280].strip()
            if snip:
                mt = (f"Aktion: {cleaned_action}\nSzene: {snip}").strip()
                await remember(story_id, mt, scene_number=scene_number,
                               kind="scene", model=embedding_model)
        except Exception:
            pass
        await _emit("done", "✅ Fertig")
        return result2

    # Last resort
    if result:
        await _emit("done", "✅ Fertig")
        return result
    clean = _strip_json_artifacts(raw.strip())
    await _emit("done", "❌ Fehler")
    return {
        "story":                     clean or "Der Game Master konnte keine Antwort generieren.",
        "options":                   ["Weiter →", "Erkunde die Umgebung", "Warte und beobachte"],
        "events":                    "",
        "world_changes":             "",
        "character_updates":         "",
        "interpreted_player_action": cleaned_action,
        "coherence_score":           0,
        "coherence_issues":          ["Generierung fehlgeschlagen"],
        "recap":                     recap_text,
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
