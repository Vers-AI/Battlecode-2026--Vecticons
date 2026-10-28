"""Static topology analysis (S2.3 - boss graduation).

The boss fight candidate is scored as

    (drops + unlock_value) x discount^estimated_turns x P(survive)

where `unlock_value` is the REACHABILITY DELTA of the group: what opens up
if its tiles open, valued by the altars / gem groups behind the door. It is
static map topology, so it is PRECOMPUTED (never a per-turn double BFS -
the documented fuel-wall history) and cached into the group metadata.

Both wall sets are the owner's 2026-10-23 ruling:
  - the reference reachability (R0) uses terrain + the static overlay of the
    excluded groups' tiles - i.e. what we can reach while every door stays
    shut;
  - the per-group flood opens exactly ONE door (that group's tiles) and
    keeps the others shut, so the delta is attributable to that group.

Rooted at our SPAWN tile, not at the champion's live position: the value is
a static property of the map, so it must not depend on where a champion
happens to stand (position-independence is what keeps the field oscillator-
safe).
"""
from __future__ import annotations

from typing import Any

import cambc
from cambc import game_map

import config
from managers import fielding


def excluded_handles(group_meta: dict) -> set:
    """Handles of the groups that fail the killable filter (the bosses)."""
    out = set()
    for key, meta in (group_meta or {}).items():
        try:
            if meta.get("is_monolith", False):
                out.add(key)
            elif int(meta.get("static_hp", 0)) > int(config.KILLABLE_HP_CAP):
                out.add(key)
        except Exception:
            continue
    return out


def _altars() -> list:
    try:
        return game_map.altars().positions()
    except Exception:
        return []


def _ally_spawn():
    try:
        positions = game_map.spawn_region(cambc.Team.ALLY).positions()
        return positions[0] if positions else None
    except Exception:
        return None


def compute_unlock_values(
    groups,
    group_meta,
    max_groups: int | None = None,
    radius: int | None = None,
) -> dict:
    """Reachability delta per excluded group: {group handle -> gem value}.

    Value of a newly reachable banking point: config.ALTAR_UNLOCK_VALUE.
    Value of a newly reachable camp group: its real gem/gold economy in
    gem-equivalent units.

    The closed and open floods share the same spawn root and radius. Each
    open flood removes exactly one excluded group's tiles from the static
    door overlay, so the set difference is attributable to that one door.

    ``max_groups`` and ``radius`` deliberately default to the configured
    values. The play-phase fallback passes smaller bounds because it runs
    on a champion's first (2x-fuel) turn, while the draft normally computes
    the full static topology in its 10x-fuel window.
    """
    values: dict = {}
    try:
        config.resolve()
        groups = tuple(groups)
    except Exception:
        return values
    excluded = excluded_handles(group_meta)
    if not excluded:
        return values

    terrain = fielding.terrain_walls()
    overlay = cambc.Bitboard()
    bosses = []
    for group in groups:
        try:
            handle = int(group.__index__())
        except Exception:
            handle = id(group)
        if handle not in excluded:
            continue
        bosses.append((handle, group))
        try:
            overlay |= group.spawn_tiles()
        except Exception:
            continue
    if not bosses:
        return values

    spawn = _ally_spawn()
    if spawn is None:
        return values

    roots = cambc.Bitboard()
    roots.set(spawn)
    if radius is None:
        radius = int(config.UNLOCK_RADIUS)
    radius = int(radius)

    # R0: what is reachable while every door stays shut.
    closed_walls = terrain.copy()
    closed_walls |= overlay
    closed_walls.set(spawn, False)
    closed = cambc.Bitboard()
    try:
        cambc.DistanceField().compute(
            closed_walls, roots, closed, radius
        )
    except Exception:
        return values

    # Only the most valuable doors get a delta (fuel bound).
    def _value_of(item):
        handle, group = item
        try:
            return (
                float(group.gem_value())
                + float(group.gold_value()) * float(config.GOLD_TO_GEM)
            )
        except Exception:
            return 0.0

    bosses.sort(key=_value_of, reverse=True)
    if max_groups is None:
        max_groups = int(config.UNLOCK_MAX_GROUPS)
    bosses = bosses[: int(max_groups)]

    altars = _altars()
    altar_rings = []
    for altar in altars:
        ring = cambc.Bitboard()
        try:
            ring.draw_chebyshev(altar, int(config.TRIBUTE_RANGE))
        except Exception:
            continue
        altar_rings.append(ring)

    for handle, group in bosses:
        try:
            tiles = group.spawn_tiles()
        except Exception:
            continue
        open_walls = terrain.copy()
        open_walls |= overlay & ~tiles
        open_walls.set(spawn, False)
        opened = cambc.Bitboard()
        try:
            cambc.DistanceField().compute(
                open_walls,
                roots,
                opened,
                radius,
            )
        except Exception:
            continue
        if opened.is_empty():
            continue
        value = 0.0
        for ring in altar_rings:
            try:
                newly_reached = (opened & ring) & ~closed
                if not newly_reached.is_empty():
                    value += float(config.ALTAR_UNLOCK_VALUE)
            except Exception:
                continue
        for other in groups:
            try:
                ohandle = int(other.__index__())
            except Exception:
                ohandle = id(other)
            if ohandle == handle:
                continue
            try:
                other_tiles = other.spawn_tiles()
            except Exception:
                continue
            try:
                if (other_tiles & opened).is_empty():
                    continue
                if not (other_tiles & closed).is_empty():
                    continue  # already reachable before the door opened
                value += (
                    float(other.gem_value())
                    + float(other.gold_value()) * float(config.GOLD_TO_GEM)
                )
            except Exception:
                continue
        values[handle] = int(min(value, 60000))
    return values


# ---------------------------------------------------------------------------
# Draft-memory transport (format version 2).
#
# The unlock values are computed during the draft (100x fuel window) but the
# play phase runs in a different process per champion, so the values travel
# through the 1,024-byte team draft memory - the only draft -> play channel.
#
#   offset 0      format version (2)
#   offset 1      pick count
#   offset 2      shared-memory ownership bitmap
#   offset 3      reserved
#   offset 4      7 bytes per pick (Stage 0 records, unchanged)
#   offset 4+7n   unlock entry count (1 byte)
#   offset 5+7n   unlock entries, 4 bytes each:
#                    +0..1  group handle (uint16, little endian)
#                    +2..3  unlock value  (uint16, little endian)
# ---------------------------------------------------------------------------

DRAFT_MEMORY_VERSION: int = 2
UNLOCK_ENTRY_SIZE: int = 4
MAX_UNLOCK_ENTRIES: int = 32


def pack_unlock_section(values: dict) -> bytes:
    """Serialize {handle: value} into the draft-memory unlock section."""
    entries = bytearray()
    for handle, value in sorted((values or {}).items()):
        if len(entries) >= MAX_UNLOCK_ENTRIES * UNLOCK_ENTRY_SIZE:
            break
        try:
            h = int(handle) & 0xFFFF
            v = int(value) & 0xFFFF
        except Exception:
            continue
        entries.extend((h & 0xFF, (h >> 8) & 0xFF, v & 0xFF, (v >> 8) & 0xFF))
    return bytes([min(len(entries) // UNLOCK_ENTRY_SIZE, 0xFF)]) + bytes(entries)


def unpack_unlock_section(raw: bytes, offset: int) -> dict:
    """Parse the unlock section; returns {} when absent or malformed."""
    out: dict = {}
    if not raw or offset >= len(raw):
        return out
    try:
        count = int(raw[offset])
    except Exception:
        return out
    start = offset + 1
    for i in range(min(count, MAX_UNLOCK_ENTRIES)):
        pos = start + i * UNLOCK_ENTRY_SIZE
        if pos + UNLOCK_ENTRY_SIZE > len(raw):
            break
        handle = int(raw[pos]) | (int(raw[pos + 1]) << 8)
        value = int(raw[pos + 2]) | (int(raw[pos + 3]) << 8)
        out[handle] = value
    return out


def unlock_section_offset(pick_count: int) -> int:
    """Byte offset of the unlock section for a given pick count."""
    return 4 + 7 * int(pick_count)
