"""Intelligence manager: persistent per-champion enemy fog memory.

Purpose: track every enemy champion this bot has personally seen and its
last known location, and speak the Sprint 2.1 shared-memory signal
protocol. The dict lives on the Bot instance (plain WASM RAM, survives
across turns) — it does NOT touch the 1,024-byte draft memory; only the
beacon write touches the 8-byte shared channel.

Key decisions:
  - Keyed by the engine handle (int(champion)), the only stable identity:
    Champion view objects are recreated every turn, so id() would never
    match across turns.
  - Records are facts only (round, x, y, champion type). No decisions.
  - Staleness is handled at read time (recent_sightings + ENEMY_TRACK_TTL),
    never by deleting records — old intel can still matter later.
  - S2.1 beacon: region-quantized (owner's design choice 2026-10-21) —
    each owned byte carries region + freshness + altar flag; champion
    kind is dropped by quantization (accepted limitation).

Limitations: fold_ally_intel folds only what the 8-byte beacon carries
(region-quantized facts, no positions); a dead writer's shared byte
freezes until it respawns (60 rounds, GameRules) — frozen flags/density
over-suppress conservatively; personal sightings can be arbitrarily
stale (callers go through recent_sightings()).

Update frequency: once per play turn from bot.play_turn().
Read path: snap.enemy_memory -> recent_sightings(bot); shared channel
via read_signals().
"""
from __future__ import annotations

import cambc
from cambc import game_map

import config
from managers import observation


def _handle(champion) -> int:
    """Stable per-enemy key (mirrors execution._camp_handle's pattern).

    Fallback degrades to the champion type, which can merge two
    mirror-picked enemies of the same type — acceptable last resort.
    """
    try:
        return int(champion.__index__())
    except Exception:
        try:
            return int(champion.champion_name())
        except Exception:
            return -1


def update_enemy_memory(bot, snap) -> list:
    """Record last-known locations for every currently visible enemy.

    Returns the records of enemies seen for the first time this match
    (diagnostics only; no consumer depends on the return value).

    S2.3 fuel: the enemy's coordinates are read ONCE here and cached on the
    snapshot as plain ints (`snap.enemy_positions`); the altar-contest test
    reuses them instead of reading every enemy's position again (~3.7k
    fuel per coordinate read).
    """
    memory = bot.enemy_memory
    if not snap.is_alive:
        return []
    first_sights: list = []
    round_now = cambc.round_number()
    positions: list = []
    for enemy in snap.visible_enemies:
        try:
            if not enemy.is_visible():
                continue
            pos = enemy.position()
            ex = int(pos.x)
            ey = int(pos.y)
            positions.append((enemy, ex, ey))
            record = {
                "round": int(round_now),
                "x": ex,
                "y": ey,
                "kind": int(enemy.champion_name()),
                # S2.3: the threat model needs the enemy's damage and reach
                # to price a remembered position as a risk (the fog memory
                # is the only threat input the bot has outside its own
                # vision).
                "strength": float(enemy.strength()),
                "range": int(enemy.attack_range()),
            }
        except Exception:
            continue
        key = _handle(enemy)
        if key not in memory:
            first_sights.append(record)
        memory[key] = record
    try:
        snap.enemy_positions = tuple(positions)
    except Exception:
        pass
    return first_sights


def recent_sightings(bot, max_rounds: int | None = None) -> dict:
    """Sightings no older than max_rounds (default ENEMY_TRACK_TTL).

    The single read path for consumers: a record within TTL is "probably
    still roughly there". Older entries are excluded from the view but
    kept in bot.enemy_memory.
    """
    if max_rounds is None:
        max_rounds = int(config.ENEMY_TRACK_TTL)
    round_now = cambc.round_number()
    return {
        key: record
        for key, record in bot.enemy_memory.items()
        if round_now - record["round"] <= max_rounds
    }


# ---------------------------------------------------------------------------
# S2.1 — Shared-memory signal protocol (8-byte channel).
#
# Byte layout (byte i owned by champion i, i = our index in the ally
# draft order; ownership bitmap = draft memory byte 2, Stage 0 format):
#   bits 0-3  region id   4x4 grid over the map: rx*4+ry where
#                        rx = x*4 // width, ry = y*4 // height
#   bit  4    altar-contested flag: this champion currently sees an enemy
#                        within SIGNAL_ALTAR_CONTEST_RADIUS of any altar
#   bits 5-7  freshness  0 = no fresh sighting (zero byte = nothing);
#                        1-7 = min(6, age_rounds // 8) + 1, i.e. a
#                        ~55-round horizon aligned with ENEMY_TRACK_TTL=60
#
# Written every play turn by the owner only (write-only discipline);
# readers parse all bytes (read-all). Freshness is a relative age bucket
# computed at write time, so a byte stays valid while its writer lives.
# Perf note: the beacon path adds one 1-byte write, one 8-byte read and a
# bounded <= 5-enemy x altars scan per turn — no map-wide loops.
#
# Format constants below are protocol-fixed (writer and reader must
# agree); they are encoding, not sweep tunables — do not move to config.
# ---------------------------------------------------------------------------

_REGION_GRID: int = 4          # 4x4 = 16 regions.
_FRESH_BUCKET_ROUNDS: int = 8  # rounds per freshness bucket.
_FRESH_NONE: int = 0           # freshness sentinel: no fresh sighting.
_ALTAR_FLAG_BIT: int = 4       # bit position of the altar-contested flag.
_FRESH_SHIFT: int = 5          # freshness occupies the high 3 bits.


def region_of(x: int, y: int) -> int:
    """Map position -> 4x4 grid region id (0-15). Protocol-fixed.

    S2.3 fuel: `game_map.width()` / `height()` are engine calls, so the
    map size is cached on the first read (it never changes in a match).
    """
    width, height = _map_size()
    rx = min(_REGION_GRID - 1, (int(x) * _REGION_GRID) // width)
    ry = min(_REGION_GRID - 1, (int(y) * _REGION_GRID) // height)
    return rx * _REGION_GRID + ry


_MAP_SIZE: tuple = (0, 0)


def _map_size() -> tuple:
    """The map size, read from the engine once per process."""
    global _MAP_SIZE
    if _MAP_SIZE == (0, 0):
        try:
            _MAP_SIZE = (max(1, int(game_map.width())), max(1, int(game_map.height())))
        except Exception:
            _MAP_SIZE = (1, 1)
    return _MAP_SIZE


def _channel_index(bot) -> int:
    """Our owned shared byte: index in the ally draft order (cached).

    Uses observation.draft_id_for: current_draft picks are ChampionDraft
    values while me.champion_name() is a ChampionType — they diverge from
    Cassi & Issac onward and cannot be compared directly.
    """
    idx = getattr(bot, "channel_index", None)
    if idx is None:
        try:
            picks = [int(p) for p in cambc.draft.current_draft(cambc.Team.ALLY)]
            me_id = observation.draft_id_for(int(cambc.me().champion_name()))
            idx = picks.index(me_id)
        except Exception:
            idx = -1
        bot.channel_index = idx
    return int(idx)


def freshest_sighting(bot):
    """Our most recent in-TTL sighting record, or None."""
    best = None
    best_round = -1
    for record in recent_sightings(bot).values():
        if record["round"] > best_round:
            best_round = record["round"]
            best = record
    return best


def local_contested_altars(snap) -> set:
    """Altar tiles with a visible enemy within the contest radius (exact).

    Own-vision ground truth: the beacon's region quantization can miss an
    enemy within the radius when the altar sits near a region boundary —
    this check covers our own sightings precisely. Write side
    (altar_contested) and the S2.2 fold both build on it.

    S2.3 fuel: the enemy coordinates come from `snap.enemy_positions` (the
    ints the intel update already read), so no Vec2 is read twice.
    """
    radius = int(config.SIGNAL_ALTAR_CONTEST_RADIUS)
    contested: set = set()
    cached = getattr(snap, "enemy_positions", None)
    if cached:
        enemies = [(ex, ey) for _champion, ex, ey in cached]
    else:
        enemies = []
        for enemy in snap.visible_enemies:
            try:
                if not enemy.is_visible():
                    continue
                pos = enemy.position()
                enemies.append((int(pos.x), int(pos.y)))
            except Exception:
                continue
    if not enemies:
        return contested
    for altar in snap.altars:
        try:
            ax = int(altar.x)
            ay = int(altar.y)
        except Exception:
            continue
        for ex, ey in enemies:
            if max(abs(ex - ax), abs(ey - ay)) <= radius:
                contested.add((ax, ay))
                break
    return contested


def altar_contested(snap) -> bool:
    """True if a currently visible enemy sits in any altar's contest zone."""
    return bool(local_contested_altars(snap))


def encode_beacon(record, altar_flag: bool, round_now: int) -> int:
    """Pack one owned byte: freshest sighting region + altar flag."""
    value = 0
    if record is not None:
        age = max(0, int(round_now) - int(record["round"]))
        fresh = min(7, _FRESH_NONE + 1 + age // _FRESH_BUCKET_ROUNDS)
        value |= region_of(record["x"], record["y"]) & 15
        value |= fresh << _FRESH_SHIFT
    if altar_flag:
        value |= 1 << _ALTAR_FLAG_BIT
    return value


def decode_beacon(value: int) -> dict:
    """Parse one channel byte into facts (region, altar, freshness, age)."""
    fresh = (value >> _FRESH_SHIFT) & 7
    return {
        "region": int(value) & 15,
        "altar": bool((value >> _ALTAR_FLAG_BIT) & 1),
        "fresh": fresh,
        "age_rounds": None if fresh == _FRESH_NONE
        else (fresh - 1) * _FRESH_BUCKET_ROUNDS,
    }


def write_beacon(bot, snap) -> int | None:
    """Write our owned byte: freshest personal sighting + altar flag.

    Write-only discipline: byte channel_index is ours alone; no other
    champion ever writes it. Returns the byte value for diagnostics, or
    None when dead or without a valid channel index.
    """
    if not snap.is_alive:
        return None
    idx = _channel_index(bot)
    if not 0 <= idx <= 7:
        return None
    record = freshest_sighting(bot)
    try:
        flag = altar_contested(snap)
    except Exception:
        flag = False
    value = encode_beacon(record, flag, cambc.round_number())
    try:
        cambc.shared_memory.write(idx, bytes([value]))
    except Exception:
        return None
    return value


def read_signals() -> list:
    """Parse every channel byte (read-all). S2.1's read path; S2.2 folds
    these into the observation snapshot."""
    try:
        raw = bytearray(cambc.shared_memory.size())
        cambc.shared_memory.read(0, raw)
    except Exception:
        return []
    out = []
    for idx in range(len(raw)):
        parsed = decode_beacon(raw[idx])
        parsed["idx"] = idx
        out.append(parsed)
    return out


# ---------------------------------------------------------------------------
# S2.2 — Ally-broadcast fold: beacons -> observation snapshot (facts only).
# Consumers live in utility (bank/farm scoring); this module only computes
# facts. bot.py wires fold_ally_intel right after read_signals().
# ---------------------------------------------------------------------------


def fold_ally_intel(snap, ally_signals) -> None:
    """Fold broadcast beacons into the snapshot (facts; no decisions).

    Sets:
      snap.ally_signals     every parsed beacon (S2.1 protocol)
      snap.region_sightings region id -> count of fresh bytes (fresh > 0);
                            broadcast sighting density per region, all
                            champions incl. ours (the channel is the source)
      snap.contested_altars (x, y) altar tiles considered contested:
                            - exact: a visible enemy within
                              SIGNAL_ALTAR_CONTEST_RADIUS (own vision)
                            - broadcast: altars inside the region of an
                              altar-flagged beacon. Region attribution
                              requires fresh == 1: an honest flag write
                              always lands in bucket 1 (the altar-adjacent
                              enemy IS the writer's freshest sighting, age
                              0), so higher buckets mean the region bits
                              point somewhere else — not attributable.

    Known limitation (S2.1, accepted): a dead writer's byte freezes until
    respawn, so a frozen flag can suppress banking at a since-cleared
    altar for up to 60 rounds — conservative direction, never oscillating.
    """
    snap.ally_signals = tuple(ally_signals)
    density: dict = {}
    for sig in ally_signals:
        if int(sig.get("fresh", 0)) > 0:
            region = int(sig.get("region", 0)) & 15
            density[region] = density.get(region, 0) + 1
    snap.region_sightings = density
    contested = local_contested_altars(snap)
    flagged_regions = {
        int(sig.get("region", 0)) & 15
        for sig in ally_signals
        if sig.get("altar") and int(sig.get("fresh", 0)) == 1
    }
    # S2.3 fuel: the altar coordinates are cached as plain ints.
    coords = getattr(snap, "altar_coords", None)
    if not coords:
        coords = []
        for altar in snap.altars:
            try:
                coords.append((int(altar.x), int(altar.y)))
            except Exception:
                continue
    for ax, ay in coords:
        try:
            if region_of(ax, ay) in flagged_regions:
                contested.add((ax, ay))
        except Exception:
            continue
    snap.contested_altars = frozenset(contested)
