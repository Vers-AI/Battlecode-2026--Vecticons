"""Observation manager (Stage 2; renamed from knowledge manager 2026-10-21).

Per-turn assembly of facts the bot needs. Pure read-only; no decisions,
no SDK writes. Cached map-derived data (camp layout, altar positions,
per-zone camp lists) lives on the Bot instance and is filled on the
first play turn (2x fuel).

Update frequency:
  - Self state (HP, gold, gems, position): every turn.
  - Vision-derived facts: every turn (depends on vision()).
  - Map cache (camp groups, altars, spawn): once at first play turn.

Read path: bot.play_turn() calls observation.snapshot(self) once per turn,
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
    visible_enemies: tuple

    # Per-group cached metadata (is_monolith, static_hp). Filled from the bot's map cache.
    group_meta: dict

    # Static union of excluded groups' spawn tiles (Monolith / over-cap
    # static HP): permanent doors for the reachability gate. Cached once.
    excluded_tiles: Any

    # Enemy fog memory (intelligence manager): engine handle -> last-known
    # record. Reference to bot.enemy_memory; updated in place each turn.
    enemy_memory: dict


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
        visible_enemies=(),
        group_meta={},
        enemy_memory={},
        excluded_tiles=None,
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
        my_id = draft_id_for(int(me.champion_name()))
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


def draft_id_for(champion_type_int: int) -> int:
    """ChampionType value -> matching ChampionDraft value.

    The two enums diverge from Cassi & Issac onward: CASSI_AND_ISSAC is
    one draft pick (3) but two playable types (CASSI=3, ISSAC=4), shifting
    every later value by one. Comparing a ChampionType against draft picks
    directly therefore only works for RYTH/VESPER_VALE — the bug that made
    three of five champions silent on the S2.1 channel (and, latently,
    misread their draft-memory zone records since Stage 2).
    """
    short = champion_short_name(int(champion_type_int))
    if short == "ISSAC":
        short = "CASSI_AND_ISSAC"
    attr = getattr(cambc.ChampionDraft, short, None)
    return int(attr) if attr is not None else -1


def _populate_map_cache(bot, snap) -> None:
    """First-turn (2x fuel) cache: altars, spawns, camp groups, draft zone.

    Also pre-computes per-group metadata (is_monolith, is_respawning, is_visible)
    so per-turn killable filters don't have to call ``group.camps()`` / ``state()``
    again. Without this cache, the Stage 3 utility layer was exceeding its
    per-turn fuel budget on large maps (group-by-group camp iteration every turn).
    """
    if bot.map_cache is not None:
        altars, spawn, groups, zone, group_meta, excluded = bot.map_cache
        snap.altars = altars
        snap.spawn_tile = spawn
        snap.camp_groups = groups
        snap.camp_zone_for_champion = zone
        snap.group_meta = group_meta
        snap.excluded_tiles = excluded
        return

    altars = tuple(game_map.altars().positions())
    spawn_positions = game_map.spawn_region(cambc.Team.ALLY).positions()
    spawn = spawn_positions[0] if spawn_positions else None
    groups = tuple(game_map.camp_groups())
    zone = _zone_id_for_me()

    # Pre-compute per-group metadata. This is the single expensive call that
    # we never want to repeat on every play turn.
    group_meta = _build_group_meta(groups)

    # Static door overlay for the reachability gate: spawn tiles of every
    # group failing the killable filter (Monolith or over-cap static HP).
    # Position-independent by construction - a vision-derived overlay
    # re-created the target-flip oscillator on 00ambush (reach flickered
    # with which excluded camps were visible). Staleness limitation: if
    # the enemy clears an excluded group, its tiles free up but the
    # overlay stays conservative until respawn (never oscillates, may
    # over-block a route briefly).
    excluded = cambc.Bitboard()
    for group in groups:
        m = group_meta.get(group_key(group), {})
        if m.get("is_monolith", False) or int(m.get("static_hp", 0)) > config.KILLABLE_HP_CAP:
            try:
                excluded |= group.spawn_tiles()
            except Exception:
                pass

    bot.map_cache = (altars, spawn, groups, zone, group_meta, excluded)
    snap.altars = altars
    snap.spawn_tile = spawn
    snap.camp_groups = groups
    snap.camp_zone_for_champion = zone
    snap.group_meta = group_meta
    snap.excluded_tiles = excluded


def group_key(group) -> int:
    """Stable key for a camp group: the engine handle.

    int(group) is stable across wrapper objects (fresh camp.camp_group()
    views resolve to the same key); id(group) would only match the
    cached objects held by the map-cache tuple.
    """
    try:
        return int(group.__index__())
    except Exception:
        return id(group)


def _build_group_meta(groups) -> dict:
    """Per-group static metadata, keyed by engine handle (group_key).

    is_monolith: any camp of the group is a Monolith. static_hp: sum of
    the camps' base health (GameRules camp table is static HP; the
    precompute is probe-verified). Powers the candidate gate's
    position-independent HP check (architecture.md Stage 2 killable
    definition, amended 2026-10-21) and the opportunistic-attack filter.
    """
    from cambc import CampType
    meta: dict = {}
    for group in groups:
        is_monolith = False
        static_hp = 0
        try:
            for camp in group.camps():
                try:
                    if int(camp.kind()) == int(CampType.MONOLITH):
                        is_monolith = True
                except Exception:
                    continue
                try:
                    static_hp += float(camp.base_stats().base_health)
                except Exception:
                    continue
        except Exception:
            is_monolith = False
        # Skip state() — it can raise if not in vision, and "respawning" is a
        # temporary state that changes every match turn; safer to evaluate it
        # lazily on demand in execution.zone_killable_camps.
        meta[group_key(group)] = {
            "is_monolith": is_monolith,
            "static_hp": int(static_hp),
        }
    return meta


def snapshot(bot) -> Snapshot:
    """Build the per-turn observation snapshot; caches map data on bot."""
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
    # Enemy vision facts: observation is the single source of what this bot
    # sees (camps AND champions). Perf note: one SDK call + <= 5 enemy
    # views per turn.
    snap.visible_enemies = tuple(cambc.vision.champions(cambc.Team.ENEMY))
    snap.enemy_memory = getattr(bot, "enemy_memory", None) or {}
    return snap
