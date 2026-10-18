"""All Sprint 1 tunables (architecture.md config.py contract).

No magic numbers elsewhere. Every value carries either a source citation
(GameRules / champion stat tables / mechanics) or a PLACEHOLDER tag for first-pass
tuning values pending A/B data.

NOTE on cambc.constants resolution: do NOT read them at module import
time - the wasm preload phase has no engine context. Use `resolve()`
the first time a value is needed in a turn function.
"""
from __future__ import annotations


# Sources: GameRules camp table, champion stats, mechanics sections.
# PLACEHOLDER = first-pass value; not final.

BANK_THRESHOLD: int = 1           # PLACEHOLDER. gems; tribute channel = ceil(gems/10) turns.
TIME_DISCOUNT: float = 0.9        # PLACEHOLDER. per-turn multiplier on utility.
RECALL_HP_FRAC: float = 0.35      # from starter bot heuristic.
KILLABLE_HP_CAP: int = 5_000      # PLACEHOLDER. > elk pack total HP, < Monolith 20k. Tuned so
                                   # wolf/elk groups clear while Monolith stays excluded.
DEATH_RISK_BASE: float = 0.05     # PLACEHOLDER. unused Sprint 1.
BANK_UTILITY_PER_GEM: float = 1.0 # PLACEHOLDER.
RECALL_UTILITY: float = 50.0      # PLACEHOLDER.
KILL_REWARD_GOLD: int = 50        # GameRules "When a champion dies".

MONOLITH_HP: int = 20_000         # GameRules camp table (Monolith total HP).

# Per-champion stat priority used when shopping on spawn tiles.
CHAMPION_STAT_PRIORITY: dict[str, str] = {
    # Keys are short names as returned by ChampionType.to_string() (uppercase, underscored).
    "RYTH": "HEALTH",
    "STELLAR": "FOCUS",
    "VESPER_VALE": "STRENGTH",
    "VARG": "STRENGTH",
    "WILLOW": "FOCUS",
    "VENDETTA": "STRENGTH",
    "PRISM": "FOCUS",
    "MARCELLA": "STRENGTH",
    "REN": "FOCUS",
    "TAVI": "FOCUS",
    "CASSI": "STRENGTH",
    "ISSAC": "STRENGTH",
}

_POWER_SHARD_BY_STAT: dict[str, str] = {
    "STRENGTH": "POWER_SHARD_OF_STRENGTH",
    "FOCUS": "POWER_SHARD_OF_FOCUS",
    "HEALTH": "POWER_SHARD_OF_HEALTH",
}

_STAT_INDEX: dict[str, int] = {"STRENGTH": 0, "FOCUS": 1, "HEALTH": 2}


# Cached cambc.constants lookups, lazy to avoid the wasm preload.
_resolved = False
TRIBUTE_RANGE: int = 0
CAPTURE_ROUNDS: int = 0
RESPAWN_ROUNDS: int = 0
KILL_GOLD_ON_DEATH: int = 0


def resolve() -> None:
    """Resolve cached engine constants exactly once. Safe to call repeatedly."""
    global _resolved, TRIBUTE_RANGE, CAPTURE_ROUNDS, RESPAWN_ROUNDS, KILL_GOLD_ON_DEATH
    if _resolved:
        return
    import cambc.constants as _constants
    TRIBUTE_RANGE = int(_constants.SUBMIT_GEMS_RANGE)
    CAPTURE_ROUNDS = int(_constants.CAPTURE_TIME)
    RESPAWN_ROUNDS = int(_constants.RESPAWN_TIME)
    KILL_GOLD_ON_DEATH = int(_constants.KILL_GOLD)
    _resolved = True


def stat_index(champion_name: str) -> int:
    """Index of this champion's priority stat (0=strength, 1=focus, 2=health)."""
    return _STAT_INDEX.get(CHAMPION_STAT_PRIORITY.get(champion_name, "STRENGTH"), 0)


def power_shard_for(champion_name: str):
    """Return the Power ItemType shard matching this champion's stat priority.

    Accepts the short name ('RYTH') or the SDK's prefixed form
    ('ChampionType.RYTH'); strips the prefix.
    """
    import cambc
    if isinstance(champion_name, str) and "." in champion_name:
        champion_name = champion_name.split(".", 1)[1]
    stat = CHAMPION_STAT_PRIORITY.get(champion_name, "STRENGTH")
    attr = _POWER_SHARD_BY_STAT.get(stat, "POWER_SHARD_OF_STRENGTH")
    return getattr(cambc.ItemType, attr)
