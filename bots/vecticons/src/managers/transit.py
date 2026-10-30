"""F3b - the transit & regroup policy.

Two mechanisms, both priced in the argmax, both map-agnostic (no map
names, no champion names - only kit types and facts):

  * THE DYNAMIC AWAKE-CAMP LAYER. A camp is not terrain. A SLEEPING camp
    is harmless (GameRules :190 "The monsters are friendly until they are
    hit!") and an ENGAGED one zones the ground around it (:208 "Damage
    wakes the group; two rounds without an in-range champion let it
    sleep"). The threat model used to read the awake camps out of the
    CURRENT vision only, and only over the tiles next to the goal, so the
    corridor behind a camp we had woken priced as empty - and the
    respawn -> walk-back-alone -> die loop had free rein (the evidence
    match: 18 corridor deaths, the same champion, the same corridor,
    every ~70 rounds). This module keeps the memory: a camp we SAW awake
    - or a camp WE struck (striking a monster wakes it) - stays a priced
    danger zone, and the zone is priced over the WHOLE walk, so the route
    AROUND an aggro'd zone wins the argmax instead of the route through
    it.

  * THE REGROUP. A candidate that crosses a lethal corridor while we are
    ALONE is deferred: the bot waits for the company instead of dribbling
    through the corridor one champion at a time. The wait is priced (it
    outscores the action it defers) and it expires (after
    REGROUP_MAX_ROUNDS the bot goes anyway - no deadlock).

The module holds facts and prices; it never decides. The caller (the
utility layer) folds the transit damage into the candidate score and the
argmax picks the winner.

State lives on the Bot instance (plain WASM RAM, survives across turns -
the same discipline as intelligence.enemy_memory): two bounded, TTL'd
dicts and a cached zone list. The bot instance also survives the death
window (a dead champion keeps its shopping turns, GameRules :373), so
the death-corridor fact outlives the death that recorded it - which is
the whole point: the champion that died is the one that walks back.

FUEL: the per-turn cost is the point of the design. The zone list is
CACHED on the bot and rebuilt only when a fact changes, the zone disks
(the monster's own attack range around the spawn tile) are built with the
zone list, and the memory sweep (the TTL expiry and the "the camp we can
see is not awake any more" check) runs every TRANSIT_UPDATE_EVERY
rounds. The expensive part - the bitboard intersection of the walk with
the zone disks - is cached on the PATH ENTRY (managers.fielding), so it
is paid once per (walk, zone set) change, not once per turn.
"""
from __future__ import annotations

from typing import Any

import cambc

import config


# ---------------------------------------------------------------------------
# Counters (the campaign's stdout evidence that the mechanism ran).
# ---------------------------------------------------------------------------

_COUNTERS = {
    "zones": 0,       # live danger zones at the last read (a gauge)
    "awake": 0,       # camps recorded as awake (seen awake or struck)
    "deaths": 0,      # death corridors recorded
    "holds": 0,       # regroup holds (the bot waited for company)
    "priced": 0,      # candidates discounted by the transit term
}

# The FUEL PROBE (the evidence that the transit layer is cheap).
#
# The engine charges the bot WASM fuel per turn, and a turn that runs out
# of fuel is recorded as a "fuelExhausted" event - so the fuel counter is
# the campaign's compute-cost meter, and the F3 feature has to be priced
# against it (the f1-retest gate: 25,687 exhaustions over the 70-map
# pool).
#
# Two probes, both a single `cambc.fuel_remaining()` read around the
# work in question:
#
#   * "update" - the cost of refresh() (the danger-memory sweep).
#   * "price"  - the cost of the transit PRICING (the zone list and the
#     bitboard intersections).
#
# The numbers are the fuel the F3b layer SPENT, per champion process.
# The campaign sums them over the team and prints them next to the
# counters, so the claim "the transit layer costs ~N fuel a match" is a
# measurement and not an assertion.
_FUEL = {"update": 0, "price": 0}


def counters() -> dict:
    """The F3b counters (the campaign's "the mechanism ran" evidence)."""
    out = dict(_COUNTERS)
    out["casts"] = 0
    out["fuel_update"] = int(_FUEL.get("update", 0))
    out["fuel_price"] = int(_FUEL.get("price", 0))
    return out


def _count(key: str, amount: int = 1) -> None:
    try:
        _COUNTERS[key] = int(_COUNTERS.get(key, 0)) + int(amount)
    except Exception:
        pass


def count_priced(amount: int = 1) -> None:
    """Count a candidate that the transit term discounted."""
    _count("priced", amount)


def count_hold(amount: int = 1) -> None:
    """Count a regroup hold (the bot waited for the company)."""
    _count("holds", amount)


# ---------------------------------------------------------------------------
# The danger memory.
# ---------------------------------------------------------------------------
#
# Two dicts on the Bot instance:
#
#   transit_awake - camp handle -> (x, y, strength, reach, round)
#                   A camp we saw AWAKE, or one we struck. The monsters
#                   do not chase (GameRules :207), so the zone around the
#                   spawn tile is where the danger is.
#   transit_death - (x, y) -> (x, y, strength, reach, round)
#                   Where WE died. The corridor that killed us is priced
#                   as a hazard, so the route around it wins the argmax.
#
# Both are bounded (config.TRANSIT_AWAKE_MAX / TRANSIT_DEATH_MAX) and
# TTL'd: a fact that has not been re-confirmed stops being priced.
#
# The records are TUPLES, not dicts: the sweep touches them every few
# rounds and a tuple read is cheaper than a string-keyed lookup in this
# wasm build.


def _memory(bot, name: str) -> dict:
    mem = getattr(bot, name, None)
    if not isinstance(mem, dict):
        mem = {}
        try:
            setattr(bot, name, mem)
        except Exception:
            pass
    return mem


def _dirty(bot) -> None:
    """Mark the cached zone list stale (it is rebuilt on the next read)."""
    try:
        bot.transit_zone_dirty = True
    except Exception:
        pass


def update(snap) -> None:
    """Refresh the danger memory from this turn's facts.

    Called once per turn from bot.py, right after the snapshot. It costs
    no engine read: the visible awake camps are already read by the
    observation layer.

    FUEL: the cheap part runs every turn (the awake records are almost
    always empty), the sweep - the TTL expiry and the "the camp we can
    see is not awake any more" check - runs every TRANSIT_UPDATE_EVERY
    rounds.
    """
    bot = getattr(snap, "bot", None)
    if bot is None:
        return
    now = int(getattr(snap, "round_number", 0) or 0)
    awake = _memory(bot, "transit_awake")
    # 1. The camps that are awake RIGHT NOW. An already-known zone only
    # gets a fresh stamp (the monsters do not move, GameRules :207).
    for record in getattr(snap, "awake_zones", None) or ():
        try:
            handle = int(record[0])
            old = awake.get(handle)
            if old is not None:
                awake[handle] = (old[0], old[1], old[2], old[3], now, old[5])
                continue
            awake[handle] = (int(record[1]), int(record[2]), float(record[3]),
                             int(record[4]), now)
            _count("awake")
            _dirty(bot)
        except Exception:
            continue
    # 2. The sweep (the TTL and the visible-but-not-awake check).
    every = int(config.TRANSIT_UPDATE_EVERY)
    if every > 0 and now % every != 0:
        return
    _sweep(bot, snap, now)


def _sweep(bot, snap, now: int) -> None:
    """Expire the stale facts and drop the camps that are no danger."""
    awake = _memory(bot, "transit_awake")
    death = _memory(bot, "transit_death")
    ttl_awake = int(config.TRANSIT_AWAKE_TTL)
    ttl_death = int(config.TRANSIT_DEATH_TTL)
    # A camp we can SEE that is not awake is no danger: its monster is
    # dead, or it went back to sleep (GameRules :208 - two rounds without
    # an in-range champion). The memory must not keep pricing a corpse.
    visible = None
    live = None
    try:
        live = {int(r[0]) for r in (getattr(snap, "awake_zones", None) or ())}
        visible = getattr(snap, "visible_camp_handles", None)
        if visible is None:
            visible = set()
            for camp in getattr(snap, "visible_camps", None) or ():
                try:
                    visible.add(int(camp.__index__()))
                except Exception:
                    continue
    except Exception:
        visible = None
    changed = False
    for handle in list(awake.keys()):
        record = awake.get(handle)
        if record is None:
            changed = True
            continue
        if now - int(record[4]) > ttl_awake:
            try:
                del awake[handle]
            except Exception:
                pass
            changed = True
            continue
        if visible is not None and handle in visible and handle not in live:
            try:
                del awake[handle]
            except Exception:
                pass
            changed = True
    for key in list(death.keys()):
        record = death.get(key)
        if record is None:
            changed = True
            continue
        if now - int(record[4]) > ttl_death:
            try:
                del death[key]
            except Exception:
                pass
            changed = True
    if changed:
        _dirty(bot)
    _trim(awake, int(config.TRANSIT_AWAKE_MAX))
    _trim(death, int(config.TRANSIT_DEATH_MAX))


def _trim(memory: dict, cap: int) -> None:
    """Keep the memory bounded: drop the oldest facts first."""
    try:
        if len(memory) <= int(cap):
            return
        ordered = sorted(memory.items(), key=lambda item: int(item[1][4]))
        for key, _record in ordered[: len(memory) - int(cap)]:
            del memory[key]
    except Exception:
        pass


def note_awake(snap, camp) -> None:
    """Record a camp we just struck: striking a monster wakes it.

    GameRules :190 - "When a champion strikes a monster, it will awaken
    attacking the nearest champion every turn". Waking a monster is a
    commitment: the ground around it becomes a danger zone even after we
    walk out of its sight. A strength-0 camp (a gem or gold cluster) has
    no monster, so nothing wakes up.
    """
    bot = getattr(snap, "bot", None)
    if bot is None:
        return
    try:
        handle = int(camp.__index__())
    except Exception:
        return
    info = (getattr(snap, "camp_info", None) or {}).get(handle)
    if info is None:
        return
    try:
        kind = int(info[1])
        reach = int(info[3])
        x = int(info[5])
        y = int(info[6])
    except Exception:
        return
    strength = _camp_strength(snap, kind)
    if strength <= 0.0:
        return
    now = int(getattr(snap, "round_number", 0) or 0)
    mem = _memory(bot, "transit_awake")
    old = mem.get(handle)
    if old is not None:
        mem[handle] = (old[0], old[1], old[2], old[3], now, old[5])
        return
    mem[handle] = (x, y, strength, reach, now, _disk(x, y, reach))
    _count("awake")
    _dirty(bot)


def _camp_strength(snap, kind: int) -> float:
    """The monster's damage per turn (the camp design table).

    The engine's own design table is authoritative (the observation layer
    prices the visible awake camps from it); the config table is the
    fallback for a kind the design table has not seen yet.
    """
    try:
        from managers import observation
        design = observation.camp_design(kind)
        if design is not None:
            return float(design[1])
    except Exception:
        pass
    try:
        return float(config.MONSTER_STRENGTH.get(int(kind), 0.0))
    except Exception:
        return 0.0


def note_death(snap, x: int, y: int, damage: float = 0.0) -> None:
    """Record the place that killed us: the death corridor.

    The corridor is priced as a hazard, so the route AROUND it wins the
    argmax and the respawn -> walk-back-alone -> die loop loses its
    favourite road.

    `damage` is the hit that killed us (the health we had at the end of
    our last alive turn). It is the honest price of the tile: a death
    to a 60-damage monster prices the corridor at 60 a turn, not at a
    flat guess. It is capped by TRANSIT_DEATH_DAMAGE - a death to a
    champion is worth more than a death to a monster, but a corridor
    priced at the whole health pool would veto every route through it.
    """
    bot = getattr(snap, "bot", None)
    if bot is None:
        return
    try:
        x = int(x)
        y = int(y)
    except Exception:
        return
    cap = float(config.TRANSIT_DEATH_DAMAGE)
    try:
        strength = float(damage)
    except Exception:
        strength = 0.0
    if strength <= 0.0:
        strength = cap
    if strength > cap:
        strength = cap
    now = int(getattr(snap, "round_number", 0) or 0)
    mem = _memory(bot, "transit_death")
    key = (x, y)
    old = mem.get(key)
    if old is not None:
        mem[key] = (old[0], old[1], old[2], old[3], now, old[5])
        return
    radius = int(config.TRANSIT_DEATH_RADIUS)
    mem[key] = (x, y, strength, radius, now, _disk(x, y, radius))
    _count("deaths")
    _dirty(bot)


# ---------------------------------------------------------------------------
# The zone list (the priced danger zones).
# ---------------------------------------------------------------------------


def _disk(x: int, y: int, reach: int):
    """The tiles a zone covers: the monster's own reach around its tile.

    The monsters do not chase (GameRules :207), so the danger of an
    engaged camp is the ground within its attack range of the spawn tile.
    """
    try:
        board = cambc.Bitboard()
        board.draw_chebyshev(cambc.Vec2(int(x), int(y)), int(reach))
        return board
    except Exception:
        return None


def zones(snap) -> list:
    """The MEMORY danger zones as (x, y, strength, reach, disk) tuples.

    A zone is a piece of ground that is dangerous RIGHT NOW even though
    the current vision cannot see why. Two facts qualify:

      * a camp we WOKE and then LOST SIGHT OF. The monsters do not chase
        (GameRules :207), so the ground around its spawn tile stays
        dangerous for as long as the monster is awake - but the F3a
        threat model reads the awake camps out of the CURRENT vision
        only, so the moment we walked out of sight of the camp we had
        just woken, the ground behind it priced as EMPTY. That is the
        hole the respawn -> walk-back-alone -> die loop walked through.
      * a DEATH CORRIDOR - the place that killed us. Nothing in the
        current vision says "this tile is bad"; we know it because we
        died there.

    THE VISIBLE AWAKE CAMPS ARE NOT IN THIS LIST. A camp we can see
    and that is awake is already priced by the destination exposure
    term (managers.fielding.threats), and pricing it here as well would
    count the same damage twice - the same walk would be discounted by
    the near-goal exposure AND by the corridor. So the corridor prices
    exactly the facts the exposure term CANNOT see: the camps that are
    out of sight and the death corridors.

    The death corridors and the out-of-sight camps are priced the same
    way - the expected damage of the path tiles inside the zone.

    The list is CACHED on the bot: it is rebuilt only when a fact
    changes, so the common turn costs one attribute read. Every rebuild
    bumps the zone version, which invalidates the cached damage on the
    path entries (managers.fielding).
    """
    bot = getattr(snap, "bot", None)
    if bot is None:
        return []
    cached = getattr(bot, "transit_zone_cache", None)
    if cached is not None and not getattr(bot, "transit_zone_dirty", True):
        return cached
    visible = getattr(snap, "visible_camp_handles", None)
    if visible is None:
        visible = frozenset()
    out = []
    for handle, record in _memory(bot, "transit_awake").items():
        try:
            if int(handle) in visible:
                # A camp we can see: the exposure term prices it.
                continue
            x = int(record[0])
            y = int(record[1])
            strength = float(record[2])
            reach = int(record[3])
        except Exception:
            continue
        if strength <= 0.0:
            continue
        disk = record[5] if len(record) > 5 else None
        if disk is None:
            disk = _disk(x, y, reach)
        if disk is None:
            continue
        out.append((x, y, strength, reach, disk))
    for record in _memory(bot, "transit_death").values():
        try:
            x = int(record[0])
            y = int(record[1])
            strength = float(record[2])
            reach = int(record[3])
        except Exception:
            continue
        if strength <= 0.0:
            continue
        disk = record[5] if len(record) > 5 else None
        if disk is None:
            disk = _disk(x, y, reach)
        if disk is None:
            continue
        out.append((x, y, strength, reach, disk))
    try:
        bot.transit_zone_cache = out
        bot.transit_zone_dirty = False
        bot.transit_zone_version = int(
            getattr(bot, "transit_zone_version", 0) or 0) + 1
    except Exception:
        pass
    try:
        _COUNTERS["zones"] = len(out)
    except Exception:
        pass
    return out


def zone_version(snap) -> int:
    """The version of the cached zone list (a cache key)."""
    bot = getattr(snap, "bot", None)
    if bot is None:
        return 0
    try:
        return int(getattr(bot, "transit_zone_version", 0) or 0)
    except Exception:
        return 0


# ---------------------------------------------------------------------------
# The prices.
# ---------------------------------------------------------------------------


def path_damage(path: Any, zone_list: list, px: int = -999, py: int = -999,
                 gx: int = -999, gy: int = -999):
    """The damage along a walked path: (total, corridor).

    `path` is a bitboard of the tiles the walk crosses. One zone tile is
    one turn of exposure, and the zone covers every tile within its
    reach (the monster's own attack range), so the damage of a zone is
    strength * (the number of path tiles inside the zone).

    `total` is every zone on the path - it is the DISCOUNT the candidate
    takes.

    `corridor` is the part of the damage that is a CORRIDOR and not the
    destination: a zone that holds our own tile is a danger we are
    already inside (a fight, not a transit) and a zone that holds the
    GOAL is the destination's own monster (the destination exposure term
    prices that one). Only the corridor can make a candidate worth
    deferring - the regroup hold waits for the company before it crosses
    a corridor, not before it fights a monster it walked to.
    """
    if path is None or not zone_list:
        return (0.0, 0.0)
    total = 0.0
    corridor = 0.0
    for zone in zone_list:
        try:
            x = int(zone[0])
            y = int(zone[1])
            strength = float(zone[2])
            reach = int(zone[3])
            disk = zone[4]
        except Exception:
            continue
        if strength <= 0.0:
            continue
        try:
            turns = int((path & disk).count())
        except Exception:
            continue
        if turns <= 0:
            continue
        damage = float(strength) * float(turns)
        total += damage
        at_feet = (abs(int(px) - x) <= reach and abs(int(py) - y) <= reach)
        at_goal = (abs(int(gx) - x) <= reach and abs(int(gy) - y) <= reach)
        if not at_feet and not at_goal:
            corridor += damage
    return (total, corridor)


def ally_close(snap, radius: int) -> bool:
    """Is an ALLY (not us) within `radius` tiles of us? (the company test)

    The regroup waits for company: without a nearby ally the champion
    must not cross a lethal corridor alone. The check is a vision read
    (one bitboard) plus a disk test - and it only runs on the rare turn
    where a corridor is actually in the way.
    """
    try:
        mask = cambc.vision.champion_tiles(cambc.Team.ALLY)
        if mask.is_empty():
            return False
        disk = cambc.Bitboard()
        disk.draw_chebyshev(snap.position, int(radius))
        overlap = disk & mask
        if overlap.is_empty():
            return False
        # The occupancy includes US: a single tile in the disk is us
        # standing in our own company.
        if int(overlap.count()) > 1:
            return True
        return not overlap.is_set(snap.position)
    except Exception:
        return False
