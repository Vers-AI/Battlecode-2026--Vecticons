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
BANK_UTILITY_PER_GEM: float = 100.0 # PLACEHOLDER. Tuned so bank dominates farm once gems>0,
                               # matching the Stage 2 baseline's "bank immediately" semantics.
                               # (With 1 gem and BANK_UTILITY_PER_GEM=100, bank score = 100; any
                               # farm candidate ≤ 100 at typical d+clear ranges.)
RECALL_UTILITY: float = 200.0     # PLACEHOLDER. Tuned so recall at hp_frac<RECALL_HP_FRAC dominates
                              # bank/farm: a low-HP bot should never bank or farm.
KILL_REWARD_GOLD: int = 50        # GameRules "When a champion dies".

# Stage 3 utility-layer tuning (architecture.md §Stage 3).
# Safety floor under which low-HP capping overrides the utility argmax
# (bot retreats and recalls before the pack wakes up on us).
SAFETY_HP_FRAC: float = 0.25       # PLACEHOLDER. Below this HP fraction the recall candidate always wins.
# Per-turn candidate-pool caps. The utility layer's per-turn cost is bounded by
# (VISIBLE_CAP + ZONE_CAP) farm candidates plus the constant-cost capture/bank/
# recall/fight candidates. Without these caps, v15 burned fuel in proportion to
# the full map's group count (one ``group.camps()`` call per group per turn).
FARM_VISIBLE_CAP: int = 3          # PLACEHOLDER. Top-N nearest killable visible camps considered.
FARM_ZONE_CAP: int = 3             # PLACEHOLDER. Top-N nearest unseen zone camps considered.
# Conservative flat gem estimates for the score (spec: gems × TIME_DISCOUNT^turns).
# Visible killable groups: monsters are pre-filtered ≤ KILLABLE_HP_CAP; one cycle
# usually clears one wolf/elk pack plus a gem cluster (~50-90 gems). Use 60.
# Unseen zone groups: large uncertainty discount.
FARM_VISIBLE_GEM_ESTIMATE: int = 60   # PLACEHOLDER. Per farm candidate.
FARM_ZONE_GEM_ESTIMATE: int = 30      # PLACEHOLDER. Per farm candidate (unseen).

# Enemy fog memory (intelligence manager). Rounds a last-known sighting stays
# "fresh" in recent_sightings(); 60 = one respawn cycle (GameRules death rules).
ENEMY_TRACK_TTL: int = 60             # PLACEHOLDER. Rounds before a sighting goes stale.

# S2.1 shared-memory signals. An enemy within this Chebyshev radius of any
# altar sets the altar-contested flag in the owner's byte. Source: GameRules
# altar locks are placeable within 2 tiles of an altar — that is the contest
# reach worth signaling.
SIGNAL_ALTAR_CONTEST_RADIUS: int = 2  # PLACEHOLDER.

# S2.2 intel consumers (facts folded by intelligence.fold_ally_intel).
# Bank-score multiplier for a contested altar (beacon altar flag / own
# sighting within SIGNAL_ALTAR_CONTEST_RADIUS). Score-down, not skip: when
# every altar is contested, banking must still happen.
BANK_CONTEST_PENALTY: float = 0.5   # PLACEHOLDER. ~7 tiles of TIME_DISCOUNT distance.
# Per-sighting multiplier on bank/farm scores in a region (architecture.md
# "crude constant per region ... estimated later" — S2.2 is the estimate).
# Tuning history (S2.2 A/B, stage ratchet vs S2.0's 16-24/13,392):
#   0.7 -> 14-26/12,977: avoidance too strong — farm redirect steered the
#   bot off enemy-trafficked GEM economies into safe gold clusters
#   (01gold_rush: +110 camps, 33 gems, ~11.7k gold held); bank delivery
#   intact everywhere (no hoard: collected==submitted).
REGION_RISK_FACTOR: float = 0.85   # PLACEHOLDER. ^count; ~2 discount tiles per sighting.

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
