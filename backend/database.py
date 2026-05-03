import sqlite3
import json
import hashlib
import secrets
from pathlib import Path
from typing import Optional

DB_PATH = Path(__file__).parent / "adventure.db"

PRESET_RULES = [
    "Der Spieler trifft alle wichtigen Entscheidungen",
    "Konsequenzen sind permanent — nichts kann rückgängig gemacht werden",
    "Tod ist möglich und endgültig",
    "Magie existiert und hat eigene, unveränderliche Regeln",
    "NPCs haben eigene Motivationen, Geheimnisse und Loyalitäten",
    "Die Welt verändert sich durch Spielerentscheidungen nachhaltig",
    "Kämpfe werden narrativ beschrieben — kein Würfelsystem",
    "Ressourcen, Ausrüstung und Zeit sind begrenzt",
    "Information ist unvollständig — Fog of War gilt",
    "Verbündete können sterben, fliehen oder den Spieler verraten",
    "Feinde sind intelligent und lernen aus Niederlagen",
    "Moralische Entscheidungen haben keine klare richtige Antwort",
]

DEFAULT_STORY_CONFIG = {
    "world_name": "Arvandor",
    "world_era": "Mittelalter-Fantasy",
    "world_atmosphere": "düster, bedrohlich, mystisch",
    "world_description": (
        "Die Welt von Arvandor: einst blühend, nun von Schatten zerrissen. "
        "Drei Königreiche kämpfen um die letzte Lichtquelle."
    ),
    "scenario": "Ein düsteres Mittelalter-Fantasy-Reich am Rand des Abgrunds.",
    "story_genre": "Fantasy",
    "story_genre_custom": "",
    "story_frame": (
        "Eine epische Heldenreise in einer sterbenden Welt. "
        "Die Spieler entscheiden, ob Licht oder Dunkelheit triumphiert."
    ),
    "preset_rules": [
        "Der Spieler trifft alle wichtigen Entscheidungen",
        "Konsequenzen sind permanent — nichts kann rückgängig gemacht werden",
        "Tod ist möglich und endgültig",
    ],
    "custom_rules": "",
    "language_style": "literarisch",
    "detail_level": "hoch",
    "style_examples": [
        {
            "good": "Klaus bleibt im Türrahmen stehen, seine Finger umklammern den Türpfosten. Der Geruch von altem Holz und Angst liegt in der Luft.",
            "bad": "Klaus geht in den Raum."
        }
    ],
    "forbidden_phrases": [
        "atmet tief durch", "nickt nachdenklich", "seufzt schwer",
        "lächelt wissend", "runzelt die Stirn",
    ],
    "forbidden_words_alts": [
        {"word": "plötzlich", "alts": "unvermittelt, auf einmal, urplötzlich"},
        {"word": "sehr", "alts": "außerordentlich, äußerst, ungemein"},
        {"word": "schön", "alts": "von atemberaubender Schönheit, makellos, bezaubernd"},
    ],
}


# ── Helpers ───────────────────────────────────────────────────────────────────

def hash_password(password: str) -> str:
    return hashlib.sha256(password.encode("utf-8")).hexdigest()


def generate_token() -> str:
    return secrets.token_hex(32)


def get_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


# ── Table Creation ────────────────────────────────────────────────────────────

def _create_tables(conn):
    c = conn.cursor()

    c.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS sessions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            token TEXT UNIQUE NOT NULL,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS stories (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            description TEXT DEFAULT '',
            scene_counter INTEGER DEFAULT 0,
            story_config TEXT DEFAULT '{}',
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS characters (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            story_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            role TEXT NOT NULL,
            is_protagonist INTEGER DEFAULT 0,
            description TEXT DEFAULT '',
            status TEXT DEFAULT 'alive',
            age TEXT DEFAULT '',
            physical_traits TEXT DEFAULT '',
            default_clothing TEXT DEFAULT '',
            superpowers TEXT DEFAULT '',
            likes TEXT DEFAULT '',
            dislikes TEXT DEFAULT '',
            favorite_weapon TEXT DEFAULT '',
            relationships TEXT DEFAULT '[]',
            current_clothing TEXT DEFAULT '',
            inventory TEXT DEFAULT '[]',
            experiences TEXT DEFAULT '[]',
            avatar_path TEXT DEFAULT '',
            skills TEXT DEFAULT '{}',
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (story_id) REFERENCES stories(id) ON DELETE CASCADE
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            story_id INTEGER NOT NULL,
            scene_number INTEGER,
            story_text TEXT,
            player_action TEXT,
            interpreted_action TEXT,
            events_summary TEXT,
            world_changes TEXT,
            character_updates TEXT,
            options_json TEXT DEFAULT '[]',
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (story_id) REFERENCES stories(id) ON DELETE CASCADE
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS global_config (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            key TEXT UNIQUE NOT NULL,
            value TEXT NOT NULL
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS game_world_state (
            story_id INTEGER PRIMARY KEY,
            state_json TEXT NOT NULL DEFAULT '{}',
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (story_id) REFERENCES stories(id) ON DELETE CASCADE
        )
    """)

    # ── Memory Vector DB (Phase 6) ──────────────────────────────────────────
    c.execute("""
        CREATE TABLE IF NOT EXISTS memories (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            story_id INTEGER NOT NULL,
            scene_number INTEGER DEFAULT 0,
            kind TEXT DEFAULT 'recap',
            text TEXT NOT NULL,
            embedding BLOB,
            embed_model TEXT DEFAULT '',
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (story_id) REFERENCES stories(id) ON DELETE CASCADE
        )
    """)
    c.execute("CREATE INDEX IF NOT EXISTS idx_memories_story ON memories(story_id)")

    # ── Faction System (Phase 7) ────────────────────────────────────────────
    c.execute("""
        CREATE TABLE IF NOT EXISTS factions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            story_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            description TEXT DEFAULT '',
            status TEXT DEFAULT 'active',
            attitude_player INTEGER DEFAULT 0,
            attitudes_json TEXT DEFAULT '{}',
            traits TEXT DEFAULT '',
            goals TEXT DEFAULT '',
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (story_id) REFERENCES stories(id) ON DELETE CASCADE
        )
    """)
    c.execute("CREATE INDEX IF NOT EXISTS idx_factions_story ON factions(story_id)")

    # ── Save-Slots / Branching (Phase 8) ────────────────────────────────────
    c.execute("""
        CREATE TABLE IF NOT EXISTS save_slots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            story_id INTEGER NOT NULL,
            parent_slot_id INTEGER,
            name TEXT NOT NULL,
            scene_number INTEGER DEFAULT 0,
            snapshot_json TEXT NOT NULL,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (story_id) REFERENCES stories(id) ON DELETE CASCADE
        )
    """)
    c.execute("CREATE INDEX IF NOT EXISTS idx_saves_story ON save_slots(story_id)")

    conn.commit()


def _ensure_global_config(conn):
    c = conn.cursor()
    defaults = [
        ("temperature", "0.7"),
        ("top_p", "0.9"),
        ("repeat_penalty", "1.1"),
        ("ollama_model", "llama3"),
        # Role-specific model overrides (empty = use main ollama_model)
        ("model_storyteller", ""),
        ("model_director",    ""),
        ("model_cataloger",   ""),
        ("model_choicemaker", ""),
        ("model_interpreter", ""),
        # Memory Vector DB (Phase 6)
        ("embedding_model", ""),
        ("memory_top_k", "3"),
    ]
    for key, value in defaults:
        c.execute("INSERT OR IGNORE INTO global_config (key, value) VALUES (?, ?)", (key, value))
    conn.commit()


def _migrate_character_state_columns(conn):
    """Add runtime-state columns to characters table if they don’t exist yet (for existing DBs)."""
    new_cols = [
        ("current_clothing", "TEXT DEFAULT ''"),
        ("inventory",        "TEXT DEFAULT '[]'"),
        ("experiences",      "TEXT DEFAULT '[]'"),        ("avatar_path",      "TEXT DEFAULT ''"),
        ("skills",           "TEXT DEFAULT '{}'"),
    ]
    for col, typedef in new_cols:
        try:
            conn.execute(f"ALTER TABLE characters ADD COLUMN {col} {typedef}")
            conn.commit()
        except Exception:
            pass  # column already exists


def _migrate_old_schema(conn):
    """Migrate from pre-v2 schema (game_config) to new users/stories schema."""
    c = conn.cursor()

    # Nothing to migrate if new schema already present
    if c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='users'").fetchone():
        return

    # Old schema detected (game_config exists)
    old_config = {}
    try:
        for row in c.execute("SELECT key, value FROM game_config"):
            old_config[row[0]] = row[1]
    except Exception:
        pass

    old_chars = []
    try:
        old_chars = [dict(r) for r in c.execute("SELECT * FROM characters")]
    except Exception:
        pass

    old_events = []
    try:
        old_events = [dict(r) for r in c.execute("SELECT * FROM events")]
    except Exception:
        pass

    # Drop old tables
    for t in ["decisions", "custom_actions", "world_state", "game_config", "characters", "events"]:
        c.execute(f"DROP TABLE IF EXISTS {t}")
    conn.commit()

    # Create new tables
    _create_tables(conn)

    # Default user from old setup
    c.execute(
        "INSERT INTO users (username, password_hash) VALUES (?, ?)",
        ("spieler", hash_password("spieler123")),
    )
    user_id = c.lastrowid

    # Build story config from old keys
    cfg = dict(DEFAULT_STORY_CONFIG)
    key_map = {
        "world_name": "world_name",
        "world_era": "world_era",
        "world_atmosphere": "world_atmosphere",
        "world_description": "world_description",
        "scenario": "scenario",
        "story_genre": "story_genre",
        "story_frame": "story_frame",
        "game_rules": "custom_rules",
        "language_style": "language_style",
        "detail_level": "detail_level",
    }
    for ok, nk in key_map.items():
        if ok in old_config:
            cfg[nk] = old_config[ok]

    try:
        cfg["forbidden_phrases"] = json.loads(old_config.get("forbidden_phrases", "[]"))
    except Exception:
        pass

    try:
        fwa = json.loads(old_config.get("forbidden_words_alts", "[]"))
        cfg["forbidden_words_alts"] = [
            {"word": e.get("word", ""), "alts": e.get("alt", e.get("alts", ""))}
            for e in fwa
        ]
    except Exception:
        pass

    good = old_config.get("good_example", "")
    bad = old_config.get("bad_example", "")
    if good or bad:
        cfg["style_examples"] = [{"good": good, "bad": bad}]

    scene_counter = int(old_config.get("scene_counter", "0"))

    c.execute(
        "INSERT INTO stories (user_id, name, description, scene_counter, story_config) VALUES (?, ?, ?, ?, ?)",
        (user_id, "Meine erste Geschichte", cfg.get("world_description", ""), scene_counter, json.dumps(cfg)),
    )
    story_id = c.lastrowid

    for ch in old_chars:
        c.execute(
            """INSERT INTO characters
               (story_id, name, role, is_protagonist, description, status,
                age, physical_traits, default_clothing, superpowers, likes, dislikes, favorite_weapon)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                story_id,
                ch.get("name", ""), ch.get("role", ""),
                1 if str(ch.get("role", "")).lower() in ["protagonist", "hauptcharakter", "spieler"] else 0,
                ch.get("description", ""), ch.get("status", "alive"),
                ch.get("age", ""), ch.get("physical_traits", ""),
                ch.get("default_clothing", ""), ch.get("superpowers", ""),
                ch.get("likes", ""), ch.get("dislikes", ""), ch.get("favorite_weapon", ""),
            ),
        )

    for ev in old_events:
        c.execute(
            """INSERT INTO events
               (story_id, scene_number, story_text, player_action,
                interpreted_action, events_summary, world_changes, character_updates)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                story_id, ev.get("scene_number", 0),
                ev.get("story_text", ""), ev.get("player_action", ""),
                ev.get("interpreted_action", ""), ev.get("events_summary", ""),
                ev.get("world_changes", "keine"), ev.get("character_updates", "keine"),
            ),
        )

    conn.commit()


# ── Init ──────────────────────────────────────────────────────────────────────

def init_db():
    conn = get_connection()
    try:
        _migrate_old_schema(conn)
        _create_tables(conn)
        _ensure_global_config(conn)
        _migrate_character_state_columns(conn)
    finally:
        conn.close()


# ── Auth ──────────────────────────────────────────────────────────────────────

def create_user(username: str, password: str) -> Optional[dict]:
    conn = get_connection()
    try:
        c = conn.cursor()
        c.execute(
            "INSERT INTO users (username, password_hash) VALUES (?, ?)",
            (username.strip(), hash_password(password)),
        )
        user_id = c.lastrowid
        conn.commit()
        return {"id": user_id, "username": username.strip()}
    except sqlite3.IntegrityError:
        return None
    finally:
        conn.close()


def verify_user(username: str, password: str) -> Optional[dict]:
    conn = get_connection()
    row = conn.execute(
        "SELECT id, username FROM users WHERE username = ? AND password_hash = ?",
        (username.strip(), hash_password(password)),
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def create_session(user_id: int) -> str:
    token = generate_token()
    conn = get_connection()
    conn.execute("INSERT INTO sessions (user_id, token) VALUES (?, ?)", (user_id, token))
    conn.commit()
    conn.close()
    return token


def get_user_by_token(token: str) -> Optional[dict]:
    conn = get_connection()
    row = conn.execute(
        """SELECT u.id, u.username FROM users u
           JOIN sessions s ON s.user_id = u.id
           WHERE s.token = ?""",
        (token,),
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def delete_session(token: str):
    conn = get_connection()
    conn.execute("DELETE FROM sessions WHERE token = ?", (token,))
    conn.commit()
    conn.close()


# ── Stories ───────────────────────────────────────────────────────────────────

def get_stories(user_id: int) -> list:
    conn = get_connection()
    rows = conn.execute(
        "SELECT id, name, description, scene_counter, created_at, updated_at "
        "FROM stories WHERE user_id = ? ORDER BY updated_at DESC",
        (user_id,),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def create_story(user_id: int, name: str, description: str = "", genre: str = "Fantasy") -> dict:
    cfg = dict(DEFAULT_STORY_CONFIG)
    cfg["story_genre"] = genre
    conn = get_connection()
    c = conn.cursor()
    c.execute(
        "INSERT INTO stories (user_id, name, description, story_config) VALUES (?, ?, ?, ?)",
        (user_id, name, description, json.dumps(cfg)),
    )
    story_id = c.lastrowid
    conn.commit()
    conn.close()
    return {"id": story_id, "name": name, "description": description, "scene_counter": 0}


def get_story(story_id: int, user_id: int) -> Optional[dict]:
    conn = get_connection()
    row = conn.execute(
        "SELECT * FROM stories WHERE id = ? AND user_id = ?", (story_id, user_id)
    ).fetchone()
    conn.close()
    if not row:
        return None
    d = dict(row)
    try:
        d["story_config"] = json.loads(d.get("story_config") or "{}")
    except Exception:
        d["story_config"] = {}
    return d


def update_story_meta(story_id: int, user_id: int, name: str, description: str):
    conn = get_connection()
    conn.execute(
        "UPDATE stories SET name=?, description=?, updated_at=CURRENT_TIMESTAMP WHERE id=? AND user_id=?",
        (name, description, story_id, user_id),
    )
    conn.commit()
    conn.close()


def save_story_config(story_id: int, user_id: int, config: dict):
    conn = get_connection()
    conn.execute(
        "UPDATE stories SET story_config=?, updated_at=CURRENT_TIMESTAMP WHERE id=? AND user_id=?",
        (json.dumps(config), story_id, user_id),
    )
    conn.commit()
    conn.close()


def delete_story(story_id: int, user_id: int):
    conn = get_connection()
    conn.execute("DELETE FROM stories WHERE id=? AND user_id=?", (story_id, user_id))
    conn.commit()
    conn.close()


def get_story_config(story_id: int) -> dict:
    conn = get_connection()
    row = conn.execute("SELECT story_config FROM stories WHERE id=?", (story_id,)).fetchone()
    conn.close()
    if not row:
        return dict(DEFAULT_STORY_CONFIG)
    try:
        cfg = json.loads(row["story_config"] or "{}")
        # Ensure all defaults exist
        for k, v in DEFAULT_STORY_CONFIG.items():
            if k not in cfg:
                cfg[k] = v
        return cfg
    except Exception:
        return dict(DEFAULT_STORY_CONFIG)


# ── Characters ────────────────────────────────────────────────────────────────

def get_characters(story_id: int) -> list:
    conn = get_connection()
    rows = conn.execute(
        "SELECT * FROM characters WHERE story_id=? ORDER BY is_protagonist DESC, name",
        (story_id,),
    ).fetchall()
    conn.close()
    result = []
    for r in rows:
        d = dict(r)
        for json_col in ("relationships", "inventory", "experiences"):
            try:
                d[json_col] = json.loads(d.get(json_col) or "[]")
            except Exception:
                d[json_col] = []
        # skills is a dict, not a list
        try:
            d["skills"] = json.loads(d.get("skills") or "{}")
            if not isinstance(d["skills"], dict):
                d["skills"] = {}
        except Exception:
            d["skills"] = {}
        result.append(d)
    return result


def upsert_character(
    story_id: int, name: str, role: str, description: str = "",
    status: str = "alive", is_protagonist: int = 0,
    age: str = "", physical_traits: str = "", default_clothing: str = "",
    superpowers: str = "", likes: str = "", dislikes: str = "",
    favorite_weapon: str = "", relationships: list = None,
    current_clothing: str = "", inventory: list = None, experiences: list = None,
    avatar_path: str = "",
    skills: dict | None = None,
    char_id: int = None,
):
    rel_json = json.dumps(relationships or [])
    inv_json = json.dumps(inventory or [])
    exp_json = json.dumps(experiences or [])
    skl_json = json.dumps(skills or {})
    conn = get_connection()
    if char_id:
        conn.execute(
            """UPDATE characters SET
               name=?, role=?, is_protagonist=?, description=?, status=?,
               age=?, physical_traits=?, default_clothing=?, superpowers=?,
               likes=?, dislikes=?, favorite_weapon=?, relationships=?,
               current_clothing=?, inventory=?, experiences=?, avatar_path=?,
               skills=?,
               updated_at=CURRENT_TIMESTAMP
               WHERE id=? AND story_id=?""",
            (name, role, is_protagonist, description, status,
             age, physical_traits, default_clothing, superpowers,
             likes, dislikes, favorite_weapon, rel_json,
             current_clothing, inv_json, exp_json, avatar_path,
             skl_json, char_id, story_id),
        )
    else:
        conn.execute(
            """INSERT INTO characters
               (story_id, name, role, is_protagonist, description, status,
                age, physical_traits, default_clothing, superpowers, likes,
                dislikes, favorite_weapon, relationships,
                current_clothing, inventory, experiences, avatar_path, skills)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (story_id, name, role, is_protagonist, description, status,
             age, physical_traits, default_clothing, superpowers,
             likes, dislikes, favorite_weapon, rel_json,
             current_clothing, inv_json, exp_json, avatar_path, skl_json),
        )
    conn.commit()
    conn.close()


def update_character_state(
    story_id: int, char_name: str,
    current_clothing: str | None = None,
    inventory: list | None = None,
    new_experiences: list | None = None,
    skill_changes: dict | None = None,
    max_experiences: int = 20,
):
    """Lightweight update of runtime character state (clothing, inventory, experiences, skills)."""
    conn = get_connection()
    row = conn.execute(
        "SELECT id, experiences, skills FROM characters WHERE story_id=? AND LOWER(name)=LOWER(?)",
        (story_id, char_name),
    ).fetchone()
    if not row:
        conn.close()
        return
    updates = []
    values  = []
    if current_clothing is not None:
        updates.append("current_clothing = ?")
        values.append(current_clothing)
    if inventory is not None:
        updates.append("inventory = ?")
        values.append(json.dumps(inventory))
    if new_experiences:
        try:
            existing = json.loads(row["experiences"] or "[]")
        except Exception:
            existing = []
        merged = existing + [e for e in new_experiences if e and e not in existing]
        if len(merged) > max_experiences:
            merged = merged[-max_experiences:]
        updates.append("experiences = ?")
        values.append(json.dumps(merged))
    if skill_changes:
        try:
            cur_skills = json.loads(row["skills"] or "{}")
            if not isinstance(cur_skills, dict):
                cur_skills = {}
        except Exception:
            cur_skills = {}
        for skill, delta in skill_changes.items():
            if not skill:
                continue
            try:
                d = int(delta)
            except Exception:
                continue
            cur_val = int(cur_skills.get(skill, 0))
            new_val = max(0, min(10, cur_val + d))
            cur_skills[skill] = new_val
        updates.append("skills = ?")
        values.append(json.dumps(cur_skills))
    if updates:
        values.extend([story_id, row["id"]])
        conn.execute(
            f"UPDATE characters SET {', '.join(updates)}, updated_at=CURRENT_TIMESTAMP "
            f"WHERE story_id=? AND id=?",
            values,
        )
        conn.commit()
    conn.close()


def delete_character(char_id: int, story_id: int):
    conn = get_connection()
    conn.execute("DELETE FROM characters WHERE id=? AND story_id=?", (char_id, story_id))
    conn.commit()
    conn.close()


# ── Events ────────────────────────────────────────────────────────────────────

def get_recent_events(story_id: int, limit: int = 20) -> list:
    conn = get_connection()
    rows = conn.execute(
        "SELECT * FROM events WHERE story_id=? ORDER BY id DESC LIMIT ?",
        (story_id, limit),
    ).fetchall()
    conn.close()
    result = []
    for r in reversed(rows):
        d = dict(r)
        try:
            d["options"] = json.loads(d.get("options_json") or "[]")
        except Exception:
            d["options"] = []
        result.append(d)
    return result


def save_event(
    story_id: int, scene_number: int, story_text: str, player_action: str,
    interpreted_action: str, events_summary: str, world_changes: str,
    character_updates: str, options: list,
) -> int:
    conn = get_connection()
    c = conn.cursor()
    c.execute(
        """INSERT INTO events
           (story_id, scene_number, story_text, player_action, interpreted_action,
            events_summary, world_changes, character_updates, options_json)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (story_id, scene_number, story_text, player_action, interpreted_action,
         events_summary, world_changes, character_updates, json.dumps(options)),
    )
    event_id = c.lastrowid
    conn.commit()
    conn.close()
    return event_id


# ── Memory Vector DB (Phase 6) ────────────────────────────────────────────────

def add_memory(story_id: int, text: str, scene_number: int = 0,
               kind: str = "recap", embedding: bytes | None = None,
               embed_model: str = "") -> int:
    conn = get_connection()
    c = conn.cursor()
    c.execute(
        """INSERT INTO memories (story_id, scene_number, kind, text, embedding, embed_model)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (story_id, scene_number, kind, text, embedding, embed_model),
    )
    mid = c.lastrowid
    conn.commit()
    conn.close()
    return mid


def get_memories(story_id: int, limit: int = 500) -> list:
    conn = get_connection()
    rows = conn.execute(
        "SELECT id, scene_number, kind, text, embedding, embed_model, created_at "
        "FROM memories WHERE story_id=? ORDER BY id DESC LIMIT ?",
        (story_id, limit),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def delete_memories(story_id: int) -> int:
    conn = get_connection()
    c = conn.execute("DELETE FROM memories WHERE story_id=?", (story_id,))
    conn.commit()
    n = c.rowcount
    conn.close()
    return n


def delete_memory(memory_id: int, story_id: int) -> bool:
    conn = get_connection()
    c = conn.execute("DELETE FROM memories WHERE id=? AND story_id=?", (memory_id, story_id))
    conn.commit()
    ok = c.rowcount > 0
    conn.close()
    return ok


# ── Factions (Phase 7) ────────────────────────────────────────────────────────

def get_factions(story_id: int) -> list:
    conn = get_connection()
    rows = conn.execute(
        "SELECT * FROM factions WHERE story_id=? ORDER BY id ASC", (story_id,)
    ).fetchall()
    conn.close()
    out = []
    for r in rows:
        d = dict(r)
        try:
            d["attitudes"] = json.loads(d.get("attitudes_json") or "{}")
        except Exception:
            d["attitudes"] = {}
        out.append(d)
    return out


def upsert_faction(
    story_id: int, name: str, description: str = "", status: str = "active",
    attitude_player: int = 0, attitudes: dict | None = None,
    traits: str = "", goals: str = "", faction_id: int | None = None,
) -> int:
    att_json = json.dumps(attitudes or {})
    ap = max(-100, min(100, int(attitude_player)))
    conn = get_connection()
    c = conn.cursor()
    if faction_id:
        c.execute(
            """UPDATE factions SET name=?, description=?, status=?, attitude_player=?,
               attitudes_json=?, traits=?, goals=?, updated_at=CURRENT_TIMESTAMP
               WHERE id=? AND story_id=?""",
            (name, description, status, ap, att_json, traits, goals, faction_id, story_id),
        )
        fid = faction_id
    else:
        c.execute(
            """INSERT INTO factions (story_id, name, description, status,
               attitude_player, attitudes_json, traits, goals)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (story_id, name, description, status, ap, att_json, traits, goals),
        )
        fid = c.lastrowid
    conn.commit()
    conn.close()
    return fid


def delete_faction(faction_id: int, story_id: int) -> bool:
    conn = get_connection()
    c = conn.execute("DELETE FROM factions WHERE id=? AND story_id=?", (faction_id, story_id))
    conn.commit()
    ok = c.rowcount > 0
    conn.close()
    return ok


def update_faction_state(
    story_id: int, name: str,
    attitude_player_delta: int | None = None,
    attitudes_changes: dict | None = None,
    status: str | None = None,
):
    """Apply incremental updates from the cataloger. Creates faction if missing."""
    conn = get_connection()
    row = conn.execute(
        "SELECT * FROM factions WHERE story_id=? AND LOWER(name)=LOWER(?)",
        (story_id, name),
    ).fetchone()
    if not row:
        conn.close()
        # Auto-create with neutral defaults
        return upsert_faction(story_id, name, attitude_player=int(attitude_player_delta or 0),
                              attitudes=attitudes_changes or {})
    updates, values = [], []
    cur_ap = int(row["attitude_player"] or 0)
    if attitude_player_delta is not None:
        new_ap = max(-100, min(100, cur_ap + int(attitude_player_delta)))
        updates.append("attitude_player = ?"); values.append(new_ap)
    if attitudes_changes:
        try:
            cur_att = json.loads(row["attitudes_json"] or "{}")
        except Exception:
            cur_att = {}
        for tgt, delta in (attitudes_changes or {}).items():
            try:
                d = int(delta)
            except Exception:
                continue
            cur_att[tgt] = max(-100, min(100, int(cur_att.get(tgt, 0)) + d))
        updates.append("attitudes_json = ?"); values.append(json.dumps(cur_att))
    if status:
        updates.append("status = ?"); values.append(status)
    if updates:
        updates.append("updated_at = CURRENT_TIMESTAMP")
        values.extend([row["id"], story_id])
        conn.execute(
            f"UPDATE factions SET {', '.join(updates)} WHERE id=? AND story_id=?",
            values,
        )
        conn.commit()
    conn.close()


# ── Save-Slots / Branching (Phase 8) ─────────────────────────────────────────

def get_save_slots(story_id: int) -> list:
    conn = get_connection()
    rows = conn.execute(
        "SELECT id, story_id, parent_slot_id, name, scene_number, created_at "
        "FROM save_slots WHERE story_id=? ORDER BY id DESC", (story_id,),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_save_slot(slot_id: int) -> dict | None:
    conn = get_connection()
    row = conn.execute("SELECT * FROM save_slots WHERE id=?", (slot_id,)).fetchone()
    conn.close()
    return dict(row) if row else None


def create_save_slot(story_id: int, name: str, scene_number: int,
                     snapshot_json: str, parent_slot_id: int | None = None) -> int:
    conn = get_connection()
    c = conn.cursor()
    c.execute(
        """INSERT INTO save_slots (story_id, parent_slot_id, name, scene_number, snapshot_json)
           VALUES (?, ?, ?, ?, ?)""",
        (story_id, parent_slot_id, name, scene_number, snapshot_json),
    )
    sid = c.lastrowid
    conn.commit()
    conn.close()
    return sid


def delete_save_slot(slot_id: int, story_id: int) -> bool:
    conn = get_connection()
    c = conn.execute("DELETE FROM save_slots WHERE id=? AND story_id=?", (slot_id, story_id))
    conn.commit()
    ok = c.rowcount > 0
    conn.close()
    return ok


def replace_story_state(story_id: int, snapshot: dict):
    """Atomic restore: replace world_state, characters, events, factions, memories
    of the story with snapshot data. Used by save/restore + branch."""
    conn = get_connection()
    c = conn.cursor()
    try:
        # World state
        ws = snapshot.get("world_state") or {}
        c.execute(
            "INSERT INTO game_world_state (story_id, state_json, updated_at) "
            "VALUES (?, ?, CURRENT_TIMESTAMP) "
            "ON CONFLICT(story_id) DO UPDATE SET state_json=excluded.state_json, "
            "updated_at=CURRENT_TIMESTAMP",
            (story_id, json.dumps(ws)),
        )
        # Scene counter
        sc = int(snapshot.get("scene_counter") or 0)
        c.execute("UPDATE stories SET scene_counter=? WHERE id=?", (sc, story_id))

        # Wipe old data for the story
        c.execute("DELETE FROM events WHERE story_id=?", (story_id,))
        c.execute("DELETE FROM characters WHERE story_id=?", (story_id,))
        c.execute("DELETE FROM factions WHERE story_id=?", (story_id,))
        c.execute("DELETE FROM memories WHERE story_id=?", (story_id,))

        # Re-insert characters
        for ch in snapshot.get("characters") or []:
            c.execute(
                """INSERT INTO characters
                   (story_id, name, role, is_protagonist, description, status, age,
                    physical_traits, default_clothing, superpowers, likes, dislikes,
                    favorite_weapon, relationships, current_clothing, inventory,
                    experiences, avatar_path, skills)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    story_id, ch.get("name", ""), ch.get("role", ""),
                    int(ch.get("is_protagonist") or 0), ch.get("description", ""),
                    ch.get("status", "alive"), ch.get("age", ""),
                    ch.get("physical_traits", ""), ch.get("default_clothing", ""),
                    ch.get("superpowers", ""), ch.get("likes", ""),
                    ch.get("dislikes", ""), ch.get("favorite_weapon", ""),
                    json.dumps(ch.get("relationships") or []),
                    ch.get("current_clothing", ""),
                    json.dumps(ch.get("inventory") or []),
                    json.dumps(ch.get("experiences") or []),
                    ch.get("avatar_path", ""),
                    json.dumps(ch.get("skills") or {}),
                ),
            )

        # Re-insert events
        for ev in snapshot.get("events") or []:
            c.execute(
                """INSERT INTO events
                   (story_id, scene_number, story_text, player_action, interpreted_action,
                    events_summary, world_changes, character_updates, options_json)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    story_id, int(ev.get("scene_number") or 0),
                    ev.get("story_text", ""), ev.get("player_action", ""),
                    ev.get("interpreted_action", ""), ev.get("events_summary", ""),
                    ev.get("world_changes", ""), ev.get("character_updates", ""),
                    json.dumps(ev.get("options") or []),
                ),
            )

        # Re-insert factions
        for f in snapshot.get("factions") or []:
            c.execute(
                """INSERT INTO factions
                   (story_id, name, description, status, attitude_player,
                    attitudes_json, traits, goals)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    story_id, f.get("name", ""), f.get("description", ""),
                    f.get("status", "active"),
                    int(f.get("attitude_player") or 0),
                    json.dumps(f.get("attitudes") or {}),
                    f.get("traits", ""), f.get("goals", ""),
                ),
            )

        # Re-insert memories (without embeddings — they will be re-computed on demand)
        for m in snapshot.get("memories") or []:
            c.execute(
                """INSERT INTO memories (story_id, scene_number, kind, text, embed_model)
                   VALUES (?, ?, ?, ?, ?)""",
                (
                    story_id, int(m.get("scene_number") or 0),
                    m.get("kind", "recap"), m.get("text", ""),
                    m.get("embed_model", ""),
                ),
            )

        conn.commit()
    finally:
        conn.close()


def clone_story(source_story_id: int, new_name: str, user_id: int) -> int:
    """Create a fresh story row that copies story_config from source. Caller fills
    state via replace_story_state()."""
    conn = get_connection()
    src = conn.execute("SELECT * FROM stories WHERE id=?", (source_story_id,)).fetchone()
    if not src:
        conn.close()
        raise ValueError("source story not found")
    c = conn.cursor()
    c.execute(
        "INSERT INTO stories (user_id, name, description, scene_counter, story_config) "
        "VALUES (?, ?, ?, ?, ?)",
        (user_id, new_name, src["description"], 0, src["story_config"]),
    )
    new_id = c.lastrowid
    conn.commit()
    conn.close()
    return new_id


def snapshot_story(story_id: int) -> dict:
    """Collect the full mutable game state for save-slot creation."""
    conn = get_connection()
    sc_row = conn.execute("SELECT scene_counter FROM stories WHERE id=?", (story_id,)).fetchone()
    ws_row = conn.execute("SELECT state_json FROM game_world_state WHERE story_id=?", (story_id,)).fetchone()
    chars = [dict(r) for r in conn.execute("SELECT * FROM characters WHERE story_id=?", (story_id,)).fetchall()]
    events = [dict(r) for r in conn.execute("SELECT * FROM events WHERE story_id=? ORDER BY id ASC", (story_id,)).fetchall()]
    facs = [dict(r) for r in conn.execute("SELECT * FROM factions WHERE story_id=?", (story_id,)).fetchall()]
    mems = [dict(r) for r in conn.execute(
        "SELECT scene_number, kind, text, embed_model FROM memories WHERE story_id=? ORDER BY id ASC",
        (story_id,),
    ).fetchall()]
    conn.close()

    # Parse JSON fields back to native
    for ch in chars:
        for col, default in (("relationships", []), ("inventory", []),
                             ("experiences", []), ("skills", {})):
            v = ch.get(col)
            if isinstance(v, str):
                try: ch[col] = json.loads(v or json.dumps(default))
                except Exception: ch[col] = default
    for ev in events:
        try: ev["options"] = json.loads(ev.get("options_json") or "[]")
        except Exception: ev["options"] = []
        ev.pop("options_json", None)
    for f in facs:
        try: f["attitudes"] = json.loads(f.get("attitudes_json") or "{}")
        except Exception: f["attitudes"] = {}
        f.pop("attitudes_json", None)

    try:
        ws = json.loads(ws_row["state_json"]) if ws_row and ws_row["state_json"] else {}
    except Exception:
        ws = {}

    return {
        "scene_counter": int(sc_row["scene_counter"]) if sc_row else 0,
        "world_state": ws,
        "characters": chars,
        "events": events,
        "factions": facs,
        "memories": mems,
    }



def increment_scene(story_id: int) -> int:
    conn = get_connection()
    row = conn.execute("SELECT scene_counter FROM stories WHERE id=?", (story_id,)).fetchone()
    current = row["scene_counter"] if row else 0
    new_val = current + 1
    conn.execute(
        "UPDATE stories SET scene_counter=?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
        (new_val, story_id),
    )
    conn.commit()
    conn.close()
    return new_val


def reset_game(story_id: int):
    conn = get_connection()
    conn.execute("DELETE FROM events WHERE story_id=?", (story_id,))
    conn.execute("UPDATE stories SET scene_counter=0 WHERE id=?", (story_id,))
    conn.execute("DELETE FROM game_world_state WHERE story_id=?", (story_id,))
    conn.execute(
        "UPDATE characters SET current_clothing='', inventory='[]', experiences='[]' WHERE story_id=?",
        (story_id,),
    )
    conn.commit()
    conn.close()


def undo_last_event(story_id: int) -> bool:
    """Delete the last event of the story and decrement the scene counter.
    Returns True if an event was removed, False if there was nothing to undo."""
    conn = get_connection()
    row = conn.execute(
        "SELECT id FROM events WHERE story_id=? ORDER BY id DESC LIMIT 1",
        (story_id,),
    ).fetchone()
    if not row:
        conn.close()
        return False
    conn.execute("DELETE FROM events WHERE id=?", (row["id"],))
    cnt = conn.execute(
        "SELECT scene_counter FROM stories WHERE id=?", (story_id,)
    ).fetchone()
    new_val = max(0, (cnt["scene_counter"] if cnt else 0) - 1)
    conn.execute(
        "UPDATE stories SET scene_counter=?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
        (new_val, story_id),
    )
    conn.commit()
    conn.close()
    return True


# ── Global LLM Config ─────────────────────────────────────────────────────────

def get_llm_config() -> dict:
    conn = get_connection()
    rows = conn.execute("SELECT key, value FROM global_config").fetchall()
    conn.close()
    return {r["key"]: r["value"] for r in rows}


def set_llm_config(key: str, value: str):
    conn = get_connection()
    conn.execute(
        "INSERT INTO global_config (key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, value),
    )
    conn.commit()
    conn.close()


# ── World State (per story) ───────────────────────────────────────────────────────

def get_world_state(story_id: int) -> dict:
    """Load the persistent world state for a story (location, facts, etc.)."""
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT state_json FROM game_world_state WHERE story_id=?", (story_id,)
        ).fetchone()
    except Exception:
        row = None
    finally:
        conn.close()
    if not row:
        return {}
    try:
        return json.loads(row["state_json"] or "{}")
    except Exception:
        return {}


def save_world_state(story_id: int, state: dict):
    """Persist the updated world state for a story."""
    conn = get_connection()
    conn.execute(
        "INSERT INTO game_world_state (story_id, state_json, updated_at) VALUES (?, ?, CURRENT_TIMESTAMP) "
        "ON CONFLICT(story_id) DO UPDATE SET state_json=excluded.state_json, updated_at=CURRENT_TIMESTAMP",
        (story_id, json.dumps(state, ensure_ascii=False)),
    )
    conn.commit()
    conn.close()


