"""Knowledge manager (Stage 2).

Per-turn assembly of facts the bot needs. Pure read-only; no decisions,
no SDK writes. Cached map-derived data (camp layout, altar positions,
per-zone camp lists) lives on the Bot instance and is filled on the
first play turn (2x fuel).

Update frequency:
  - Self state (HP, gold, gems, position): every turn.
  - Vision-derived facts: every turn (depends on vision()).
  - Map cache (camp groups, altars, spawn): once at first play turn.

Read path: bot.play_turn() calls knowledge.snapshot(self) once per turn,
then passes the snapshot through to execution. Knowledge never mutates
engine state.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import cambc
from cambc import game_map

import config


@dataclass
class Snapshot:
    """Per-turn read of facts the bot needs. Cheap; safe to call every turn."""

    me: Any
    is_alive: bool
    is_recalling: bool
    is_submitting_gems: bool
    is_teleporting: bool
    can_move: bool
    can_attack: bool

    # Self resources.
    hp: float
    max_hp: float
    gold: int
    gems: int
    position: Any

    # Map cache (filled on first call).
    altars: tuple
    spawn_tile: Any | None
    camp_groups: tuple
    camp_zone_for_champion: int

    # Vision-derived.
    visible_camp_groups: tuple


def _me_facts(me) -> Snapshot:
    return Snapshot(
        me=me,
        is_alive=bool(me.is_alive()),
        is_recalling=bool(me.is_recalling()),
        is_submitting_gems=bool(me.is_submitting_gems()),
        is_teleporting=bool(me.is_teleporting()),
        can_move=bool(me.can_move()),
        can_attack=bool(me.can_attack()),
        hp=float(me.health()),
        max_hp=float(me.max_health()),
        gold=int(me.gold_count()),
        gems=int(me.gem_count()),
        position=me.position(),
        altars=(),
        spawn_tile=None,
        camp_groups=(),
        camp_zone_for_champion=0,
        visible_camp_groups=(),
    )


def _zone_id_for_me() -> int:
    """Parse the Stage 0 draft-memory buffer for this champion's zone id.

    Header layout (architecture.md Stage 0):
      byte 0       format version (must be 1)
      byte 1       pick count
      byte 2       ownership bit map
      byte 3       reserved
    Then 7-byte per-pick records starting at offset 4. record[0]=champion id,
    record[2]=camp zone id. The record for this champion is at
    offset 4 + 7*pick_index, where pick_index is our index in current_draft(ALLY).
    """
    try:
        raw = bytes(cambc.DraftMemoryBuffer())
    except Exception:
        return 0
    if len(raw) < 4 or raw[0] != 1:
        return 0
    try:
        picks_ally = cambc.draft.current_draft(cambc.Team.ALLY)
    except Exception:
        return 0
    if not picks_ally:
        return 0
    try:
        me = cambc.me()
        my_id = int(me.champion_name())
    except Exception:
        return 0
    try:
        idx = list(picks_ally).index(my_id)
    except ValueError:
        return 0
    zone_offset = 4 + 7 * idx + 2
    if zone_offset >= len(raw):
        return 0
    return int(raw[zone_offset])


def champion_short_name(champion_name_int: int) -> str:
    """Return the short champion name from an int ChampionType.

    `cambc.ChampionType.to_string(value)` yields 'ChampionType.RYTH'. We want
    'RYTH' for config.CHAMPION_STAT_PRIORITY lookups.
    """
    raw = cambc.ChampionType.to_string(int(champion_name_int))
    if "." in raw:
        return raw.split(".", 1)[1]
    return raw


def _populate_map_cache(bot, snap) -> None:
    """First-turn (2x fuel) cache: altars, spawns, camp groups, draft zone."""
    if bot.map_cache is not None:
        altars, spawn, groups, zone = bot.map_cache
        snap.altars = altars
        snap.spawn_tile = spawn
        snap.camp_groups = groups
        snap.camp_zone_for_champion = zone
        return

    altars = tuple(game_map.altars().positions())
    spawn_positions = game_map.spawn_region(cambc.Team.ALLY).positions()
    spawn = spawn_positions[0] if spawn_positions else None
    groups = tuple(game_map.camp_groups())
    zone = _zone_id_for_me()

    bot.map_cache = (altars, spawn, groups, zone)
    snap.altars = altars
    snap.spawn_tile = spawn
    snap.camp_groups = groups
    snap.camp_zone_for_champion = zone


def snapshot(bot) -> Snapshot:
    """Build the per-turn knowledge snapshot; caches map data on bot."""
    me = cambc.me()
    snap = _me_facts(me)
    _populate_map_cache(bot, snap)
    visible = cambc.vision.visible_tiles()
    visible_groups = []
    for group in snap.camp_groups:
        tiles = group.spawn_tiles()
        if not (tiles & visible).is_empty():
            visible_groups.append(group)
    snap.visible_camp_groups = tuple(visible_groups)
    return snap
