from fastapi import FastAPI, HTTPException, Header, Depends, UploadFile, File
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel
from typing import Optional, List
import json
import logging
import traceback
import shutil
import uuid
from pathlib import Path
from datetime import datetime

from backend.database import (
    init_db, PRESET_RULES, DEFAULT_STORY_CONFIG,
    # auth
    create_user, verify_user, create_session, get_user_by_token, delete_session, hash_password,
    # stories
    get_stories, create_story, get_story, update_story_meta,
    save_story_config, delete_story, get_story_config,
    # characters
    get_characters, upsert_character, delete_character,
    # events/game
    get_recent_events, save_event, increment_scene, reset_game, undo_last_event,
    # world state
    get_world_state,
    save_world_state,
    update_character_state,
    # config
    get_llm_config, set_llm_config,
    # db connection (used in import)
    get_connection,
)
from backend.llm import generate_scene, check_ollama_connection, call_ollama_json, _flatten_field

app = FastAPI(title="Adventure Game Master", version="2.0.0")

FRONTEND_DIR = Path(__file__).parent.parent / "frontend"
AVATARS_DIR  = FRONTEND_DIR / "assets" / "avatars"
AVATARS_DIR.mkdir(parents=True, exist_ok=True)
LOG_DIR = Path(__file__).parent.parent / "logs"
LOG_DIR.mkdir(exist_ok=True)

# ── LLM error logger ──────────────────────────────────────────────────────────
_llm_logger = logging.getLogger("llm_errors")
_llm_logger.setLevel(logging.ERROR)
_llm_handler = logging.FileHandler(LOG_DIR / "llm_errors.log", encoding="utf-8")
_llm_handler.setFormatter(logging.Formatter("%(asctime)s  %(message)s", datefmt="%Y-%m-%d %H:%M:%S"))
_llm_logger.addHandler(_llm_handler)
_llm_logger.propagate = False

def log_llm_error(context: str, exc: Exception, extra: str = ""):
    tb = traceback.format_exc()
    _llm_logger.error(
        f"[{context}] {type(exc).__name__}: {exc}"
        + (f"\n  extra: {extra}" if extra else "")
        + f"\n{tb}"
    )

app.mount("/static", StaticFiles(directory=str(FRONTEND_DIR)), name="static")


@app.on_event("startup")
async def startup():
    init_db()
    # Ensure a default admin user exists for first-time access
    try:
        conn = get_connection()
        row = conn.execute("SELECT id FROM users WHERE username=?", ("admin",)).fetchone()
        conn.close()
        if not row:
            # create_user handles hashing and uniqueness
            created = create_user("admin", "admin123")
            if created:
                print("[startup] created default admin/admin123")
    except Exception:
        # don't block startup on admin creation error
        pass


# ── Pages ──────────────────────────────────────────────────────────────────────

@app.get("/")
async def root():
    return FileResponse(str(FRONTEND_DIR / "landing.html"))


@app.get("/play")
async def play():
    return FileResponse(str(FRONTEND_DIR / "index.html"))


# ── Auth dependency ────────────────────────────────────────────────────────────

async def require_user(authorization: Optional[str] = Header(None)) -> dict:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Nicht angemeldet")
    token = authorization[7:]
    user = get_user_by_token(token)
    if not user:
        raise HTTPException(status_code=401, detail="Session abgelaufen")
    return user


def require_story(story_id: int, user: dict) -> dict:
    story = get_story(story_id, user["id"])
    if not story:
        raise HTTPException(status_code=404, detail="Geschichte nicht gefunden")
    return story


# ── Auth Routes ────────────────────────────────────────────────────────────────

class AuthRequest(BaseModel):
    username: str
    password: str


@app.post("/api/auth/register")
async def register(req: AuthRequest):
    if len(req.username.strip()) < 2:
        raise HTTPException(status_code=400, detail="Benutzername zu kurz (min. 2 Zeichen)")
    if len(req.password) < 4:
        raise HTTPException(status_code=400, detail="Passwort zu kurz (min. 4 Zeichen)")
    user = create_user(req.username, req.password)
    if not user:
        raise HTTPException(status_code=409, detail="Benutzername bereits vergeben")
    token = create_session(user["id"])
    return {"token": token, "user": user}


@app.post("/api/auth/login")
async def login(req: AuthRequest):
    user = verify_user(req.username, req.password)
    if not user:
        raise HTTPException(status_code=401, detail="Benutzername oder Passwort falsch")
    token = create_session(user["id"])
    return {"token": token, "user": user}


@app.post("/api/auth/logout")
async def logout(authorization: Optional[str] = Header(None)):
    if authorization and authorization.startswith("Bearer "):
        delete_session(authorization[7:])
    return {"success": True}


@app.get("/api/auth/me")
async def me(user: dict = Depends(require_user)):
    return user


# ── Story Routes ───────────────────────────────────────────────────────────────

@app.get("/api/stories")
async def list_stories(user: dict = Depends(require_user)):
    return get_stories(user["id"])


class StoryCreate(BaseModel):
    name: str
    description: str = ""
    genre: str = "Fantasy"


@app.post("/api/stories")
async def new_story(req: StoryCreate, user: dict = Depends(require_user)):
    if not req.name.strip():
        raise HTTPException(status_code=400, detail="Name darf nicht leer sein")
    return create_story(user["id"], req.name.strip(), req.description, req.genre)


class StoryMeta(BaseModel):
    name: str
    description: str = ""


@app.put("/api/stories/{story_id}")
async def update_story(story_id: int, req: StoryMeta, user: dict = Depends(require_user)):
    require_story(story_id, user)
    update_story_meta(story_id, user["id"], req.name, req.description)
    return {"success": True}


@app.delete("/api/stories/{story_id}")
async def remove_story(story_id: int, user: dict = Depends(require_user)):
    require_story(story_id, user)
    delete_story(story_id, user["id"])
    return {"success": True}


@app.get("/api/stories/{story_id}/config")
async def get_config(story_id: int, user: dict = Depends(require_user)):
    story = require_story(story_id, user)
    return {"name": story["name"], "description": story["description"], "config": story["story_config"]}


@app.post("/api/stories/{story_id}/config")
async def save_config(story_id: int, data: dict, user: dict = Depends(require_user)):
    require_story(story_id, user)
    config = data.get("config", {})
    save_story_config(story_id, user["id"], config)
    return {"success": True}


# ── Preset Rules ───────────────────────────────────────────────────────────────

@app.get("/api/presets/rules")
async def get_preset_rules():
    return PRESET_RULES


# ── Characters ─────────────────────────────────────────────────────────────────

@app.get("/api/characters/{story_id}")
async def list_characters(story_id: int, user: dict = Depends(require_user)):
    require_story(story_id, user)
    return get_characters(story_id)


class RelationshipModel(BaseModel):
    target: str
    type: str = ""
    description: str = ""


class CharacterModel(BaseModel):
    id: Optional[int] = None
    story_id: int
    name: str
    role: str
    is_protagonist: int = 0
    description: str = ""
    status: str = "alive"
    age: str = ""
    physical_traits: str = ""
    default_clothing: str = ""
    superpowers: str = ""
    likes: str = ""
    dislikes: str = ""
    favorite_weapon: str = ""
    relationships: List[RelationshipModel] = []
    current_clothing: str = ""
    inventory: List[str] = []
    experiences: List[str] = []
    avatar_path: str = ""


@app.post("/api/characters")
async def save_character(char: CharacterModel, user: dict = Depends(require_user)):
    require_story(char.story_id, user)
    upsert_character(
        story_id=char.story_id,
        name=char.name,
        role=char.role,
        description=char.description,
        status=char.status,
        is_protagonist=char.is_protagonist,
        age=char.age,
        physical_traits=char.physical_traits,
        default_clothing=char.default_clothing,
        superpowers=char.superpowers,
        likes=char.likes,
        dislikes=char.dislikes,
        favorite_weapon=char.favorite_weapon,
        relationships=[r.dict() for r in char.relationships],
        current_clothing=char.current_clothing,
        inventory=list(char.inventory),
        experiences=list(char.experiences),
        avatar_path=char.avatar_path,
        char_id=char.id,
    )
    return {"success": True}


@app.post("/api/characters/{story_id}/{char_id}/avatar")
async def upload_avatar(
    story_id: int, char_id: int,
    file: UploadFile = File(...),
    user: dict = Depends(require_user),
):
    """Upload an avatar image for a character. Returns the public URL path."""
    require_story(story_id, user)
    # Validate mime type
    allowed = {"image/jpeg", "image/png", "image/webp", "image/gif"}
    if file.content_type not in allowed:
        raise HTTPException(status_code=400, detail="Nur JPEG, PNG, WebP oder GIF erlaubt.")
    # Limit size: 5 MB
    content = await file.read()
    if len(content) > 5 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="Bild darf maximal 5 MB groß sein.")
    # Save with unique filename
    ext = Path(file.filename).suffix.lower() or ".jpg"
    filename = f"{story_id}_{char_id}_{uuid.uuid4().hex[:8]}{ext}"
    dest = AVATARS_DIR / filename
    dest.write_bytes(content)
    # Update DB
    avatar_url = f"/static/assets/avatars/{filename}"
    conn = get_connection()
    conn.execute(
        "UPDATE characters SET avatar_path=?, updated_at=CURRENT_TIMESTAMP WHERE id=? AND story_id=?",
        (avatar_url, char_id, story_id),
    )
    conn.commit()
    conn.close()
    return {"avatar_path": avatar_url}


@app.get("/api/characters/{story_id}/{char_id}/export-json")
async def export_character_json(story_id: int, char_id: int, user: dict = Depends(require_user)):
    """Export a character as a GPT-friendly JSON for avatar generation."""
    require_story(story_id, user)
    conn = get_connection()
    row = conn.execute(
        "SELECT * FROM characters WHERE id=? AND story_id=?", (char_id, story_id)
    ).fetchone()
    conn.close()
    if not row:
        raise HTTPException(status_code=404, detail="Charakter nicht gefunden.")
    c = dict(row)
    story_cfg = get_story_config(story_id)
    export = {
        "storyweaver_character_export": True,
        "story": {
            "genre":       story_cfg.get("story_genre_custom") or story_cfg.get("story_genre", ""),
            "world":       story_cfg.get("world_name", ""),
            "atmosphere":  story_cfg.get("world_atmosphere", ""),
        },
        "character": {
            "name":             c.get("name", ""),
            "role":             c.get("role", ""),
            "is_protagonist":   bool(c.get("is_protagonist")),
            "age":              c.get("age", ""),
            "physical_traits":  c.get("physical_traits", ""),
            "default_clothing": c.get("default_clothing", ""),
            "current_clothing": c.get("current_clothing", ""),
            "description":      c.get("description", ""),
            "superpowers":      c.get("superpowers", ""),
            "likes":            c.get("likes", ""),
            "dislikes":         c.get("dislikes", ""),
            "favorite_weapon":  c.get("favorite_weapon", ""),
        },
        "avatar_instructions": (
            "Erstelle ein realistisches Portrait-Bild des Charakters basierend auf den obigen Feldern. "
            "Verwende physical_traits und default_clothing als Basis. "
            "Stil: passend zum Genre und der Atmosphäre der Geschichte."
        ),
    }
    from fastapi.responses import JSONResponse
    return JSONResponse(
        content=export,
        headers={"Content-Disposition": f"attachment; filename=\"{c['name'].replace(' ', '_')}_avatar.json\""},
    )


@app.delete("/api/characters/{story_id}/{char_id}")
async def remove_character(story_id: int, char_id: int, user: dict = Depends(require_user)):
    require_story(story_id, user)
    delete_character(char_id, story_id)
    return {"success": True}


# ── Export / Import ───────────────────────────────────────────────────────────

@app.get("/api/stories/{story_id}/export")
async def export_story(story_id: int, user: dict = Depends(require_user)):
    story = require_story(story_id, user)
    config = get_story_config(story_id)
    characters = get_characters(story_id)

    # Strip internal DB fields from characters
    char_export = []
    for c in characters:
        char_export.append({
            "name":             c.get("name", ""),
            "role":             c.get("role", ""),
            "description":      c.get("description", ""),
            "status":           c.get("status", "alive"),
            "is_protagonist":   c.get("is_protagonist", 0),
            "age":              c.get("age", ""),
            "physical_traits":  c.get("physical_traits", ""),
            "default_clothing": c.get("default_clothing", ""),
            "superpowers":      c.get("superpowers", ""),
            "likes":            c.get("likes", ""),
            "dislikes":         c.get("dislikes", ""),
            "favorite_weapon":  c.get("favorite_weapon", ""),
            "relationships":    c.get("relationships", []),
        })

    return {
        "export_version": 1,
        "story_title":    story["name"],
        "config":         config,
        "characters":     char_export,
    }


class ImportRequest(BaseModel):
    story_id: int
    data: dict


@app.post("/api/stories/import")
async def import_story(req: ImportRequest, user: dict = Depends(require_user)):
    story = require_story(req.story_id, user)
    data = req.data

    if data.get("export_version") != 1:
        raise HTTPException(status_code=400, detail="Unbekanntes Export-Format (export_version != 1)")

    errors = []

    # Import config (merge with existing defaults)
    if "config" in data and isinstance(data["config"], dict):
        new_cfg = dict(DEFAULT_STORY_CONFIG)
        new_cfg.update(data["config"])
        save_story_config(req.story_id, user["id"], new_cfg)

    # Import characters (replace all)
    if "characters" in data and isinstance(data["characters"], list):
        # Delete existing characters first
        conn = get_connection()
        conn.execute("DELETE FROM characters WHERE story_id=?", (req.story_id,))
        conn.commit()
        conn.close()

        for c in data["characters"]:
            if not isinstance(c, dict) or not c.get("name"):
                continue
            try:
                upsert_character(
                    story_id=req.story_id,
                    name=c.get("name", ""),
                    role=c.get("role", ""),
                    description=c.get("description", ""),
                    status=c.get("status", "alive"),
                    is_protagonist=int(c.get("is_protagonist", 0)),
                    age=c.get("age", ""),
                    physical_traits=c.get("physical_traits", ""),
                    default_clothing=c.get("default_clothing", ""),
                    superpowers=c.get("superpowers", ""),
                    likes=c.get("likes", ""),
                    dislikes=c.get("dislikes", ""),
                    favorite_weapon=c.get("favorite_weapon", ""),
                    relationships=c.get("relationships", []),
                )
            except Exception as e:
                errors.append(f"Charakter '{c.get('name')}': {e}")

    return {
        "success": True,
        "story_title": story["name"],
        "errors": errors,
    }


# ── AI Helpers ─────────────────────────────────────────────────────────────────

class GenerateCharRequest(BaseModel):
    story_id: int
    name: str
    description: str = ""


@app.post("/api/ai/generate-character")
async def ai_generate_character(req: GenerateCharRequest, user: dict = Depends(require_user)):
    require_story(req.story_id, user)
    cfg = get_story_config(req.story_id)
    recent = get_recent_events(req.story_id, limit=5)
    context = ", ".join(
        e.get('events_summary', '') for e in recent if e.get('events_summary')
    ) or "keine"

    world_info = (
        f"Welt: {cfg.get('world_name','Unbekannt')}, "
        f"Genre: {cfg.get('story_genre','Fantasy')}, "
        f"Atmosph\u00e4re: {cfg.get('world_atmosphere','dunkel')}"
    )
    desc_hint = f" ({req.description})" if req.description else ""

    system = (
        "Du bist eine JSON-API für ein Textadventure-Spiel. "
        "Antworte AUSSCHLIESSLICH mit einem einzigen gültigen JSON-Objekt – kein Text davor oder danach. "
        "Alle Feldwerte auf Deutsch. Erfinde passende Details, falls nötig."
    )
    prompt = (
        f"Erstelle ein detailliertes Charakterprofil für einen NPC namens '{req.name}'{desc_hint}.\n"
        f"Spielwelt: {world_info}\n"
        f"Bisherige Ereignisse in der Geschichte: {context}\n\n"
        "Fülle ALLE Felder dieses JSON-Objekts mit passenden deutschen Texten:\n"
        "{\n"
        '  "role": "Rolle oder Beruf des Charakters",\n'
        '  "age": "Alter als Text, z.B. Mitte 30",\n'
        '  "physical_traits": "Körpermerkmale, Aussehen",\n'
        '  "default_clothing": "typische Kleidung oder Ausrüstung",\n'
        '  "description": "kurze Charakterbeschreibung mit Persönlichkeit",\n'
        '  "superpowers": "besondere Fähigkeiten oder leer lassen",\n'
        '  "likes": "Vorlieben und Interessen",\n'
        '  "dislikes": "Abneigungen",\n'
        '  "favorite_weapon": "bevorzugte Waffe oder leer lassen"\n'
        "}"
    )
    try:
        result = await call_ollama_json(prompt, system=system)
        expected_keys = ("role","age","physical_traits","default_clothing","description",
                         "superpowers","likes","dislikes","favorite_weapon")
        # If model wrapped data in a sub-object, flatten it
        if not result.get("role") and len(result) == 1:
            inner = next(iter(result.values()))
            if isinstance(inner, dict):
                result = inner
        # Ensure all expected keys exist (fill missing with empty string)
        for key in expected_keys:
            result.setdefault(key, "")
        # Coerce every field to a plain string — LLMs sometimes return lists/dicts
        for key in expected_keys:
            result[key] = _flatten_field(result[key])
        result["name"] = req.name
        return result
    except Exception as e:
        log_llm_error("generate-character", e, f"story={req.story_id} name={req.name}")
        return {
            "name": req.name,
            "role": "", "age": "", "physical_traits": "",
            "default_clothing": "", "description": "",
            "superpowers": "", "likes": "", "dislikes": "", "favorite_weapon": "",
            "_error": f"KI-Fehler: {str(e) or type(e).__name__}",
        }


class GenerateWorldRequest(BaseModel):
    story_id: int
    description: str


@app.post("/api/ai/generate-world")
async def ai_generate_world(req: GenerateWorldRequest, user: dict = Depends(require_user)):
    require_story(req.story_id, user)
    if not req.description.strip():
        raise HTTPException(status_code=400, detail="Beschreibung darf nicht leer sein")

    system = (
        "Du bist eine JSON-API für ein Textadventure-Spiel. "
        "Antworte AUSSCHLIESSLICH mit einem einzigen gültigen JSON-Objekt – kein Text davor oder danach. "
        "Alle Feldwerte auf Deutsch. Schreibe ausführliche, atmosphärische Beschreibungen."
    )
    prompt = (
        f"Erstelle eine vollständige Spielwelt-Beschreibung für dieses Setting: '{req.description}'.\n\n"
        "Fülle ALLE Felder dieses JSON-Objekts mit passenden deutschen Texten:\n"
        "{\n"
        '  "world_name": "Name der Welt oder des Settings",\n'
        '  "world_era": "Zeitalter oder Epoche, z.B. postapokalyptisch / Mittelalter / Science-Fiction",\n'
        '  "world_atmosphere": "Stimmung und Atmosphäre in wenigen Worten",\n'
        '  "world_description": "ausführliche Beschreibung der Welt (3-5 Sätze)",\n'
        '  "scenario": "das Hauptszenario oder die zentrale Herausforderung",\n'
        '  "story_genre": "Genre, z.B. Postapokalypse / Fantasy / Sci-Fi",\n'
        '  "story_frame": "die Rahmenhandlung: Warum spielen wir, was ist das Ziel?"\n'
        "}"
    )
    try:
        result = await call_ollama_json(prompt, system=system)
        # Normalize: ensure all expected keys exist, flatten nested objects
        expected = ["world_name","world_era","world_atmosphere","world_description",
                    "scenario","story_genre","story_frame"]
        flat = {key: result.get(key, "") for key in expected}
        # Best-effort recovery if model used different key names
        if not flat.get("world_name"):
            for k, v in result.items():
                clean_k = k.replace(".", "_").replace(" ", "_").lower()
                for exp in expected:
                    if exp in clean_k or clean_k in exp:
                        flat.setdefault(exp, v)
                        break
        return flat if any(flat.values()) else result
    except Exception as e:
        log_llm_error("generate-world", e, f"story={req.story_id} prompt={req.description[:80]}")
        # Return partial/empty result instead of 500 so the user can fill in manually
        return {
            "world_name": "",
            "world_era": "",
            "world_atmosphere": "",
            "world_description": "",
            "scenario": "",
            "story_genre": "",
            "story_frame": "",
            "_error": f"KI-Fehler: {str(e) or type(e).__name__}",
        }


# ── Game ───────────────────────────────────────────────────────────────────────

@app.get("/api/game/state/{story_id}")
async def game_state(story_id: int, user: dict = Depends(require_user)):
    story = require_story(story_id, user)
    events = get_recent_events(story_id, limit=30)
    characters = get_characters(story_id)
    world_state = get_world_state(story_id)
    return {
        "scene_number": story["scene_counter"],
        "events": events,
        "characters": characters,
        "story": story,
        "world_items": world_state.get("world_items", []),
        "location":            world_state.get("location", "") or "",
        "time":                world_state.get("time", "") or "",
        "weather":             world_state.get("weather", "") or "",
        "tone":                world_state.get("tone", "") or "",
        "established_facts":   world_state.get("established_facts", []) or [],
        "characters_present":  world_state.get("characters_present", []) or [],
        "current_directive":   world_state.get("current_directive", "") or "",
        "current_beat":        world_state.get("current_beat", "") or "",
    }


class StartRequest(BaseModel):
    story_id: int


@app.post("/api/game/start")
async def start_game(req: StartRequest, user: dict = Depends(require_user)):
    story = require_story(req.story_id, user)
    scene_number = increment_scene(req.story_id)
    try:
        result = await generate_scene("", scene_number, req.story_id)
    except Exception as e:
        log_llm_error("game/start", e, f"story={req.story_id} scene={scene_number}")
        raise HTTPException(status_code=500, detail=f"LLM Fehler: {str(e)}")

    save_event(
        story_id=req.story_id,
        scene_number=scene_number,
        story_text=result.get("story", ""),
        player_action="[Spielstart]",
        interpreted_action="Spielstart",
        events_summary=result.get("events", ""),
        world_changes=result.get("world_changes", "keine"),
        character_updates=result.get("character_updates", "keine"),
        options=result.get("options", []),
    )
    return {
        "scene_number":              scene_number,
        "story_text":                result.get("story", ""),
        "events_summary":            result.get("events", ""),
        "world_changes":             result.get("world_changes", ""),
        "character_updates":         result.get("character_updates", ""),
        "options":                   result.get("options", []),
        "interpreted_player_action": result.get("interpreted_player_action", ""),
        "world_items":               result.get("world_items", []),
        "location":                  result.get("location", "") or "",
        "time":                      result.get("time", "") or "",
        "weather":                   result.get("weather", "") or "",
        "tone":                      result.get("tone", "") or "",
        "established_facts":         result.get("established_facts", []) or [],
        "characters_present":        result.get("characters_present", []) or [],
        "current_directive":         result.get("current_directive", "") or "",
        "current_beat":              result.get("current_beat", "") or "",
    }


class ActionRequest(BaseModel):
    story_id: int
    action: str
    is_custom: bool = False


@app.post("/api/game/action")
async def process_action(req: ActionRequest, user: dict = Depends(require_user)):
    require_story(req.story_id, user)
    scene_number = increment_scene(req.story_id)
    try:
        result = await generate_scene(req.action, scene_number, req.story_id)
    except Exception as e:
        log_llm_error("game/action", e, f"story={req.story_id} scene={scene_number} action={req.action[:80]}")
        raise HTTPException(status_code=500, detail=f"LLM Fehler: {str(e)}")

    save_event(
        story_id=req.story_id,
        scene_number=scene_number,
        story_text=result.get("story", ""),
        player_action=req.action,
        interpreted_action=result.get("interpreted_player_action", ""),
        events_summary=result.get("events", ""),
        world_changes=result.get("world_changes", "keine"),
        character_updates=result.get("character_updates", "keine"),
        options=result.get("options", []),
    )
    return {
        "scene_number":              scene_number,
        "story_text":                result.get("story", ""),
        "events_summary":            result.get("events", ""),
        "world_changes":             result.get("world_changes", ""),
        "character_updates":         result.get("character_updates", ""),
        "options":                   result.get("options", []),
        "interpreted_player_action": result.get("interpreted_player_action", ""),
        "world_items":               result.get("world_items", []),
        "location":                  result.get("location", "") or "",
        "time":                      result.get("time", "") or "",
        "weather":                   result.get("weather", "") or "",
        "tone":                      result.get("tone", "") or "",
        "established_facts":         result.get("established_facts", []) or [],
        "characters_present":        result.get("characters_present", []) or [],
        "current_directive":         result.get("current_directive", "") or "",
        "current_beat":              result.get("current_beat", "") or "",
    }


class ResetRequest(BaseModel):
    story_id: int


@app.post("/api/game/action-stream")
async def process_action_stream(req: ActionRequest, user: dict = Depends(require_user)):
    """SSE-Endpoint: streamt Pipeline-Phasen als 'phase'-Events,
    sendet das finale Resultat als 'result'-Event, dann 'done'.
    Frontend nutzt fetch + ReadableStream (NDJSON line-delimited)."""
    import asyncio
    require_story(req.story_id, user)
    scene_number = increment_scene(req.story_id)

    queue: asyncio.Queue = asyncio.Queue()
    SENTINEL = object()

    async def progress(phase: str, label: str):
        await queue.put({"type": "phase", "phase": phase, "label": label})

    async def runner():
        try:
            result = await generate_scene(req.action, scene_number, req.story_id, progress=progress)
            save_event(
                story_id=req.story_id,
                scene_number=scene_number,
                story_text=result.get("story", ""),
                player_action=req.action,
                interpreted_action=result.get("interpreted_player_action", ""),
                events_summary=result.get("events", ""),
                world_changes=result.get("world_changes", "keine"),
                character_updates=result.get("character_updates", "keine"),
                options=result.get("options", []),
            )
            await queue.put({
                "type": "result",
                "data": {
                    "scene_number":              scene_number,
                    "story_text":                result.get("story", ""),
                    "events_summary":            result.get("events", ""),
                    "world_changes":             result.get("world_changes", ""),
                    "character_updates":         result.get("character_updates", ""),
                    "options":                   result.get("options", []),
                    "interpreted_player_action": result.get("interpreted_player_action", ""),
                    "world_items":               result.get("world_items", []),
                    "location":                  result.get("location", "") or "",
                    "time":                      result.get("time", "") or "",
                    "weather":                   result.get("weather", "") or "",
                    "tone":                      result.get("tone", "") or "",
                    "established_facts":         result.get("established_facts", []) or [],
                    "characters_present":        result.get("characters_present", []) or [],
                    "current_directive":         result.get("current_directive", "") or "",
                    "current_beat":              result.get("current_beat", "") or "",
                    "coherence_score":           result.get("coherence_score", 10),
                    "coherence_issues":          result.get("coherence_issues", []),
                    "recap":                     result.get("recap", ""),
                },
            })
        except Exception as e:
            log_llm_error("game/action-stream", e,
                          f"story={req.story_id} scene={scene_number} action={req.action[:80]}")
            await queue.put({"type": "error", "message": f"LLM Fehler: {str(e)}"})
        finally:
            await queue.put(SENTINEL)

    async def stream():
        task = asyncio.create_task(runner())
        try:
            while True:
                item = await queue.get()
                if item is SENTINEL:
                    break
                yield json.dumps(item, ensure_ascii=False) + "\n"
        finally:
            if not task.done():
                task.cancel()

    return StreamingResponse(stream(), media_type="application/x-ndjson")


@app.post("/api/game/reset")
async def game_reset(req: ResetRequest, user: dict = Depends(require_user)):
    require_story(req.story_id, user)
    reset_game(req.story_id)
    return {"success": True}


class UndoRequest(BaseModel):
    story_id: int


@app.post("/api/game/undo")
async def game_undo(req: UndoRequest, user: dict = Depends(require_user)):
    require_story(req.story_id, user)
    ok = undo_last_event(req.story_id)
    if not ok:
        return {"success": False, "reason": "empty"}
    # Return the new tail event (if any) so the client can re-display the previous options
    events = get_recent_events(req.story_id, limit=1)
    last = events[-1] if events else None
    world_state = get_world_state(req.story_id)
    return {
        "success": True,
        "scene_number": (last["scene_number"] if last else 0),
        "last_event": last,
        "world_items":         world_state.get("world_items", []),
        "location":            world_state.get("location", "") or "",
        "time":                world_state.get("time", "") or "",
        "weather":             world_state.get("weather", "") or "",
        "tone":                world_state.get("tone", "") or "",
        "established_facts":   world_state.get("established_facts", []) or [],
        "characters_present":  world_state.get("characters_present", []) or [],
    }


# ── Inventar-Mutationen (kein LLM-Call) ────────────────────────────────────────

def _char_inv(characters: list, name: str) -> list:
    """Parse current inventory list of a character (case-insensitive name match)."""
    n = (name or "").strip().lower()
    for c in characters:
        if (c.get("name") or "").strip().lower() == n:
            inv = c.get("inventory")
            if isinstance(inv, str):
                try:
                    inv = json.loads(inv)
                except Exception:
                    inv = []
            return list(inv) if isinstance(inv, list) else []
    return []


def _strip_item(inv: list, item_name: str) -> tuple[list, bool]:
    """Removes first case-insensitive match of item_name from inv. Returns (new_list, removed)."""
    target = (item_name or "").strip().lower()
    out = []
    removed = False
    for it in inv:
        if not removed and isinstance(it, str) and it.strip().lower() == target:
            removed = True
            continue
        out.append(it)
    return out, removed


class InventoryTransferRequest(BaseModel):
    story_id: int
    from_char: str
    to_char: str
    item_name: str


@app.post("/api/game/inventory/transfer")
async def inventory_transfer(req: InventoryTransferRequest, user: dict = Depends(require_user)):
    require_story(req.story_id, user)
    if req.from_char.strip().lower() == req.to_char.strip().lower():
        raise HTTPException(status_code=400, detail="Geber und Empfänger müssen unterschiedlich sein.")
    chars = get_characters(req.story_id)
    src = _char_inv(chars, req.from_char)
    dst = _char_inv(chars, req.to_char)
    new_src, removed = _strip_item(src, req.item_name)
    if not removed:
        raise HTTPException(status_code=404, detail=f"{req.from_char} besitzt '{req.item_name}' nicht.")
    new_dst = list(dst) + [req.item_name]
    update_character_state(req.story_id, req.from_char, inventory=new_src)
    update_character_state(req.story_id, req.to_char,   inventory=new_dst)
    # Sync world_items.held_by if matching item exists
    ws = get_world_state(req.story_id) or {}
    items = ws.get("world_items") or []
    target = req.item_name.strip().lower()
    changed = False
    for it in items:
        if isinstance(it, dict) and (it.get("name") or "").strip().lower() == target:
            it["held_by"] = req.to_char
            it["status"]  = it.get("status") or "acquired"
            changed = True
            break
    if changed:
        ws["world_items"] = items
        save_world_state(req.story_id, ws)
    return {"success": True, "from_inventory": new_src, "to_inventory": new_dst}


class InventoryDropRequest(BaseModel):
    story_id: int
    char: str
    item_name: str


@app.post("/api/game/inventory/drop")
async def inventory_drop(req: InventoryDropRequest, user: dict = Depends(require_user)):
    require_story(req.story_id, user)
    chars = get_characters(req.story_id)
    src = _char_inv(chars, req.char)
    new_src, removed = _strip_item(src, req.item_name)
    if not removed:
        raise HTTPException(status_code=404, detail=f"{req.char} besitzt '{req.item_name}' nicht.")
    update_character_state(req.story_id, req.char, inventory=new_src)
    # Add/update in world_items at current location
    ws = get_world_state(req.story_id) or {}
    items = list(ws.get("world_items") or [])
    target = req.item_name.strip().lower()
    found = False
    for it in items:
        if isinstance(it, dict) and (it.get("name") or "").strip().lower() == target:
            it["held_by"] = ""
            it["location"] = ws.get("location", "") or it.get("location", "")
            it["status"]  = "acquired"
            found = True
            break
    if not found:
        items.append({
            "name": req.item_name,
            "status": "acquired",
            "held_by": "",
            "location": ws.get("location", "") or "",
        })
    ws["world_items"] = items
    save_world_state(req.story_id, ws)
    return {"success": True, "inventory": new_src, "world_items": items}


class InventoryTakeRequest(BaseModel):
    story_id: int
    char: str
    item_name: str


@app.post("/api/game/inventory/take")
async def inventory_take(req: InventoryTakeRequest, user: dict = Depends(require_user)):
    require_story(req.story_id, user)
    ws = get_world_state(req.story_id) or {}
    items = list(ws.get("world_items") or [])
    target = req.item_name.strip().lower()
    matched = None
    for it in items:
        if isinstance(it, dict) and (it.get("name") or "").strip().lower() == target:
            matched = it
            break
    if not matched:
        raise HTTPException(status_code=404, detail=f"'{req.item_name}' ist nicht in der Welt vorhanden.")
    holder = (matched.get("held_by") or "").strip().lower()
    if holder and holder != req.char.strip().lower():
        raise HTTPException(status_code=400, detail=f"'{req.item_name}' wird bereits von {matched.get('held_by')} getragen.")
    matched["held_by"] = req.char
    matched["status"]  = "acquired"
    ws["world_items"] = items
    save_world_state(req.story_id, ws)
    chars = get_characters(req.story_id)
    inv = _char_inv(chars, req.char)
    if not any(isinstance(i, str) and i.strip().lower() == target for i in inv):
        inv.append(req.item_name)
        update_character_state(req.story_id, req.char, inventory=inv)
    return {"success": True, "inventory": inv, "world_items": items}


# ── History ────────────────────────────────────────────────────────────────────

@app.post("/api/game/repair/{story_id}")
async def repair_story(story_id: int, user: dict = Depends(require_user)):
    """Repariert einen verzogenen Spielzustand:
       - Re-extrahiert World-State aus den letzten 3 Szenen
       - Re-extrahiert NPC-Kleidung/Inventar aus den letzten 3 Szenen
       - Entfernt abstrakte Konzepte (Energienetz, Resonanz etc.) aus world_items als obstacle
    """
    require_story(story_id, user)
    from backend.llm import _extract_world_state, _extract_character_states, check_ollama_connection
    from backend.database import get_story_config, get_characters as _get_chars, save_world_state, update_character_state
    cfg = get_story_config(story_id)
    chars = _get_chars(story_id)
    llm_cfg = get_llm_config()
    model = llm_cfg.get("ollama_model", "")
    if not model or model == "llama3":
        tags = await check_ollama_connection()
        avail = tags.get("models", [])
        model = avail[0] if avail else "llama3"
    output_language = llm_cfg.get("output_language", "Deutsch")
    num_ctx = int(llm_cfg.get("num_ctx", "4096"))

    events = get_recent_events(story_id, limit=3)
    if not events:
        return {"success": False, "reason": "no_events"}
    # Stitch last 3 scenes into one big text for re-extraction
    big_text = "\n\n".join((ev.get("story_text") or "") for ev in events)
    ws = get_world_state(story_id)
    new_ws = await _extract_world_state(big_text, ws, cfg, model, num_ctx, output_language)
    cleaned = []
    abstract_blocklist = (
        "energienetz", "resonanz", "aura", "chakra", "mana", "ki-energie",
        "dreieckiges delta", "delta-energie", "fluss der zeit"
    )
    if new_ws and new_ws.get("world_items"):
        for it in new_ws["world_items"]:
            name_lc = (it.get("name") or "").lower()
            if it.get("type") == "obstacle" and any(b in name_lc for b in abstract_blocklist):
                continue  # drop abstract-concept obstacles
            cleaned.append(it)
        new_ws["world_items"] = cleaned
        new_ws["scene"] = events[-1].get("scene_number", 0)
        save_world_state(story_id, new_ws)
    char_updates = await _extract_character_states(big_text, chars, model, num_ctx, output_language)
    updated_names = []
    for upd in (char_updates or []):
        update_character_state(
            story_id, upd["name"],
            current_clothing=upd.get("current_clothing"),
            inventory=upd.get("inventory"),
            new_experiences=upd.get("new_experiences"),
        )
        updated_names.append(upd["name"])
    return {
        "success": True,
        "world_state_refreshed": bool(new_ws),
        "characters_updated":   updated_names,
        "world_items_after":    (new_ws or {}).get("world_items", []) if new_ws else [],
    }


@app.get("/api/history/{story_id}")
async def get_history(story_id: int, user: dict = Depends(require_user)):
    require_story(story_id, user)
    return get_recent_events(story_id, limit=100)


# ── Ollama ─────────────────────────────────────────────────────────────────────

@app.get("/api/ollama/status")
async def ollama_status():
    result = await check_ollama_connection()
    connected = result.get("status") == "ok"
    models = result.get("models", [])
    # Get currently configured model
    try:
        cfg = get_llm_config()
        current_model = cfg.get("ollama_model", models[0] if models else "")
    except Exception:
        current_model = models[0] if models else ""
    return {"connected": connected, "models": models, "model": current_model}


# ── LLM Config ─────────────────────────────────────────────────────────────────

@app.get("/api/llm/config")
async def get_llm(user: dict = Depends(require_user)):
    return get_llm_config()


class LLMConfigRequest(BaseModel):
    config: dict


@app.post("/api/llm/config")
async def save_llm(req: LLMConfigRequest, user: dict = Depends(require_user)):
    allowed = {
        "temperature", "top_p", "repeat_penalty", "ollama_model",
        "num_ctx", "num_predict", "output_language", "memory_depth",
        "model_storyteller", "model_director", "model_cataloger",
        "model_choicemaker", "model_interpreter",
    }
    for key, value in req.config.items():
        if key in allowed:
            set_llm_config(key, str(value) if value is not None else "")
    return {"success": True}


# ══════════════════════════════════════════════════════════════════════════════
# ADMIN API
# ══════════════════════════════════════════════════════════════════════════════

def _rows_to_list(rows) -> list:
    return [dict(r) for r in rows]


@app.get("/api/admin/overview")
async def admin_overview(user: dict = Depends(require_user)):
    conn = get_connection()
    try:
        users   = _rows_to_list(conn.execute("SELECT id, username, created_at FROM users ORDER BY id").fetchall())
        stories = _rows_to_list(conn.execute(
            "SELECT id, user_id, name, description, scene_counter, created_at, updated_at FROM stories ORDER BY id"
        ).fetchall())
        char_count  = conn.execute("SELECT COUNT(*) FROM characters").fetchone()[0]
        event_count = conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
        cfg_rows    = _rows_to_list(conn.execute("SELECT key, value FROM global_config ORDER BY key").fetchall())
        return {
            "users": users,
            "stories": stories,
            "char_count": char_count,
            "event_count": event_count,
            "global_config": cfg_rows,
        }
    finally:
        conn.close()


@app.get("/api/admin/users")
async def admin_users(user: dict = Depends(require_user)):
    conn = get_connection()
    try:
        return _rows_to_list(conn.execute("SELECT id, username, created_at FROM users ORDER BY id").fetchall())
    finally:
        conn.close()


class AdminUserUpdate(BaseModel):
    username: str
    password: str = ""


@app.put("/api/admin/users/{user_id}")
async def admin_update_user(user_id: int, req: AdminUserUpdate, user: dict = Depends(require_user)):
    conn = get_connection()
    try:
        if req.password:
            pw_hash = hash_password(req.password)
            conn.execute("UPDATE users SET username=?, password_hash=? WHERE id=?",
                         (req.username, pw_hash, user_id))
        else:
            conn.execute("UPDATE users SET username=? WHERE id=?", (req.username, user_id))
        conn.commit()
        return {"success": True}
    finally:
        conn.close()


@app.delete("/api/admin/users/{user_id}")
async def admin_delete_user(user_id: int, user: dict = Depends(require_user)):
    conn = get_connection()
    try:
        conn.execute("DELETE FROM users WHERE id=?", (user_id,))
        conn.commit()
        return {"success": True}
    finally:
        conn.close()


@app.get("/api/admin/stories")
async def admin_stories(user: dict = Depends(require_user)):
    conn = get_connection()
    try:
        return _rows_to_list(conn.execute(
            "SELECT id, user_id, name, description, scene_counter, story_config, created_at, updated_at FROM stories ORDER BY id"
        ).fetchall())
    finally:
        conn.close()


class AdminStoryUpdate(BaseModel):
    user_id: Optional[int] = None
    name: Optional[str] = None
    description: Optional[str] = None
    scene_counter: Optional[int] = None
    story_config: Optional[str] = None


@app.put("/api/admin/stories/{story_id}")
async def admin_update_story(story_id: int, req: AdminStoryUpdate, user: dict = Depends(require_user)):
    conn = get_connection()
    try:
        fields, vals = [], []
        if req.user_id is not None:       fields.append("user_id=?");       vals.append(req.user_id)
        if req.name is not None:          fields.append("name=?");           vals.append(req.name)
        if req.description is not None:   fields.append("description=?");    vals.append(req.description)
        if req.scene_counter is not None: fields.append("scene_counter=?");  vals.append(req.scene_counter)
        if req.story_config is not None:  fields.append("story_config=?");   vals.append(req.story_config)
        if not fields:
            return {"success": True, "note": "nothing changed"}
        vals.append(story_id)
        conn.execute(f"UPDATE stories SET {', '.join(fields)}, updated_at=CURRENT_TIMESTAMP WHERE id=?", vals)
        conn.commit()
        return {"success": True}
    finally:
        conn.close()


@app.delete("/api/admin/stories/{story_id}")
async def admin_delete_story(story_id: int, user: dict = Depends(require_user)):
    conn = get_connection()
    try:
        conn.execute("DELETE FROM stories WHERE id=?", (story_id,))
        conn.commit()
        return {"success": True}
    finally:
        conn.close()


@app.get("/api/admin/characters/{story_id}")
async def admin_characters(story_id: int, user: dict = Depends(require_user)):
    conn = get_connection()
    try:
        return _rows_to_list(conn.execute(
            "SELECT * FROM characters WHERE story_id=? ORDER BY id", (story_id,)
        ).fetchall())
    finally:
        conn.close()


@app.delete("/api/admin/characters/{char_id}")
async def admin_delete_character(char_id: int, user: dict = Depends(require_user)):
    conn = get_connection()
    try:
        conn.execute("DELETE FROM characters WHERE id=?", (char_id,))
        conn.commit()
        return {"success": True}
    finally:
        conn.close()


@app.get("/api/admin/events/{story_id}")
async def admin_events(story_id: int, user: dict = Depends(require_user)):
    conn = get_connection()
    try:
        return _rows_to_list(conn.execute(
            "SELECT id, scene_number, player_action, substr(story_text,1,200) as story_preview, "
            "events_summary, world_changes, created_at FROM events WHERE story_id=? ORDER BY id",
            (story_id,)
        ).fetchall())
    finally:
        conn.close()


@app.delete("/api/admin/events/{event_id}")
async def admin_delete_event(event_id: int, user: dict = Depends(require_user)):
    conn = get_connection()
    try:
        conn.execute("DELETE FROM events WHERE id=?", (event_id,))
        conn.commit()
        return {"success": True}
    finally:
        conn.close()


@app.get("/api/admin/config")
async def admin_get_config(user: dict = Depends(require_user)):
    conn = get_connection()
    try:
        return _rows_to_list(conn.execute("SELECT key, value FROM global_config ORDER BY key").fetchall())
    finally:
        conn.close()


class AdminConfigUpdate(BaseModel):
    key: str
    value: str


@app.put("/api/admin/config")
async def admin_set_config(req: AdminConfigUpdate, user: dict = Depends(require_user)):
    conn = get_connection()
    try:
        conn.execute("INSERT INTO global_config(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                     (req.key, req.value))
        conn.commit()
        return {"success": True}
    finally:
        conn.close()


@app.get("/api/admin/logs")
async def admin_logs(user: dict = Depends(require_user), lines: int = 100):
    log_file = LOG_DIR / "llm_errors.log"
    if not log_file.exists():
        return {"lines": []}
    text = log_file.read_text(encoding="utf-8", errors="replace")
    all_lines = text.splitlines()
    return {"lines": all_lines[-lines:]}


@app.get("/admin")
async def admin_page():
    return FileResponse(str(FRONTEND_DIR / "admin.html"))
