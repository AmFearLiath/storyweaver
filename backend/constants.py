"""Shared constants for character stats and other domain-wide enums."""

# Character stats: 0-100 scale, semantic vital/social meters.
# These are tracked separately from skills (which are 0-10 ability scores).
# The Cataloger LLM updates them based on scene events; UI shows them as bars.
STAT_KEYS: tuple = (
    "Gesundheit",   # physical health, wounds
    "Stress",       # mental load, anxiety pressure
    "Ansehen",      # public reputation, social standing
    "Vertrauen",    # how much allies/factions currently trust the character
    "Angst",        # acute fear, intimidation
    "Loyalität",    # commitment to allies/cause
    "Energie",      # physical/mental stamina, exhaustion
    "Moral",        # ethical confidence, willingness to keep going
)

# Default starting values for newly created characters.
STAT_DEFAULTS: dict = {
    "Gesundheit":  90,
    "Stress":      20,
    "Ansehen":     50,
    "Vertrauen":   50,
    "Angst":       15,
    "Loyalität":   60,
    "Energie":     80,
    "Moral":       70,
}

# Stats where HIGH values are bad (so the UI shows them red when high)
STAT_INVERTED: tuple = ("Stress", "Angst")


def stat_label(stat: str, value: int) -> str:
    """Human-readable label for a stat value."""
    v = max(0, min(100, int(value or 0)))
    inverted = stat in STAT_INVERTED
    if inverted:
        if v >= 80: return "kritisch"
        if v >= 60: return "hoch"
        if v >= 40: return "spürbar"
        if v >= 20: return "leicht"
        return "ruhig"
    if v >= 80: return "ausgezeichnet"
    if v >= 60: return "gut"
    if v >= 40: return "mittel"
    if v >= 20: return "schwach"
    return "kritisch"
