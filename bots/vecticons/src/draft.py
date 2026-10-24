"""Draft manager (Stage 1; fuel-wall fix for the patched 40-map pool).

Pick order: Ryth -> Stellar -> Vesper Vale; if team_size() == 5, append Varg, then Willow.
Writes the draft-memory layout (frozen in plan/architecture.md; S2.3 extends
it to format version 2 with the boss unlock section).

Key decisions: the map layout (altars, camp groups, zone) is computed ONCE
per process on the first draft turn (10x fuel) and cached at module level —
the pre-fix code re-derived it per pick, and on the patched pool's bigger
maps that exhausted the 1x fuel of later draft turns, making the engine
random-substitute our picks (~15/40 maps).

S2.3: the boss "unlock value" (the reachability delta of an excluded group —
what opens if its tiles open) is static map topology, so it is computed in
the draft's 10x-fuel window and shipped to the play phase through the
extended (v2) team draft memory. The play phase falls back to computing it
on its first (2x-fuel) turn when the draft memory carries none.

Limitations: zone ids are still degenerate (all picks share the nearest
group's zone, unchanged Stage 1 semantics — fixed only if a consumer ever
reads it); the cache assumes map data is static across the draft, which
the engine guarantees.
"""
from __future__ import annotations

import cambc
from cambc import game_map

from managers import observation, topology


# Draft-memory header.  The S2.3 format (version 2 plus the unlock
# section) is enabled by main.py only for the new utility bot.  Keeping
# the legacy default preserves the frozen baseline's draft payload and
# engine-call profile.
_HEADER_VERSION = 1
_HEADER_RESERVED = 0
_S23_ENABLED = False


def enable_s23() -> None:
    """Enable the S2.3 draft-memory extension for this process."""
    global _S23_ENABLED
    _S23_ENABLED = True

# S2.3: the unlock precompute is skipped when the draft turn is out of
# fuel (the engine then random-substitutes our pick). The play phase
# recomputes the values on its first turn, so skipping is safe.
_UNLOCK_MIN_FUEL = 1_000_000


# Sprint 1 pick priority.
_PRIORITY = (
    cambc.ChampionDraft.RYTH,
    cambc.ChampionDraft.STELLAR,
    cambc.ChampionDraft.VESPER_VALE,
    cambc.ChampionDraft.VARG,
    cambc.ChampionDraft.WILLOW,
)

# One-time map layout for draft memory packing (fuel-wall fix):
# (altar count, camp zone id). Computed on the first draft turn.
_layout_cache = None

# One-time boss unlock section (S2.3). Computed on the first draft turn.
_unlock_cache = None


def _pick_order() -> tuple[int, ...]:
    size = cambc.draft.team_size()
    return tuple(_PRIORITY[: max(3, min(size, len(_PRIORITY)))])


def _nearest_spawn_pos():
    try:
        positions = game_map.spawn_region(cambc.Team.ALLY).positions()
    except Exception:
        return None
    return positions[0] if positions else None


def _camp_zone_nearest_spawn(spawn, groups) -> int:
    if not groups or spawn is None:
        return 0
    best_zone = 0
    best_distance = None
    for zone, group in enumerate(groups):
        positions = group.spawn_tiles().positions()
        if not positions:
            d = 0
        else:
            d = min(abs(p.x - spawn.x) + abs(p.y - spawn.y) for p in positions)
        if best_distance is None or d < best_distance:
            best_distance = d
            best_zone = zone
    return best_zone


def _layout() -> tuple[int, int]:
    """Altar count and camp zone, computed once (first draft turn, 10x fuel).

    Re-deriving this per pick (and per draft turn) is what exhausted fuel on
    the patched pool's larger maps — each subsequent draft turn only gets 1x
    fuel, and the pre-fix code iterated every camp group's spawn tiles per
    pick record.
    """
    global _layout_cache
    if _layout_cache is not None:
        return _layout_cache
    try:
        altar_count = len(game_map.altars().positions())
    except Exception:
        altar_count = 0
    try:
        groups = tuple(game_map.camp_groups())
    except Exception:
        groups = ()
    spawn = _nearest_spawn_pos()
    zone_id = _camp_zone_nearest_spawn(spawn, groups)
    _layout_cache = (altar_count, zone_id)
    return _layout_cache


def _unlock_section() -> bytes:
    """The boss unlock section (S2.3), computed once per draft process.

    The unlock value of an excluded group is the reachability delta of the
    map: what opens up if that group's tiles open. It is static topology, so
    it is computed in the draft's 10x-fuel window and shipped to the play
    phase through the v2 draft memory (the play phase falls back to its own
    first-turn precompute when this section is empty).
    """
    global _unlock_cache
    if _unlock_cache is not None:
        return _unlock_cache
    _unlock_cache = b"\x00"  # empty section (count = 0)
    try:
        fuel = int(cambc.fuel_remaining())
    except Exception:
        fuel = 0
    if fuel < _UNLOCK_MIN_FUEL:
        print(
            f"draft: unlock precompute skipped (fuel={fuel})",
            flush=True,
        )
        return _unlock_cache
    values: dict = {}
    try:
        groups = tuple(game_map.camp_groups())
        meta = observation.build_group_meta(groups)
        values = topology.compute_unlock_values(groups, meta)
    except Exception as error:
        print(f"draft: unlock precompute failed: {error!r}", flush=True)
        values = {}
    _unlock_cache = topology.pack_unlock_section(values)
    print(
        f"draft: unlock entries={len(values)} section_bytes={len(_unlock_cache)}",
        flush=True,
    )
    return _unlock_cache


def _pack_draft_memory(picks):
    """Build the draft-memory payload per the v2 layout (cheap).

    Uses the cached layout; the per-pick cost is appending one 7-byte
    record. The S2.3 unlock section is appended once (cached).
    """
    altar_count, zone_id = _layout()

    arena = bytearray()
    arena.append(topology.DRAFT_MEMORY_VERSION if _S23_ENABLED else _HEADER_VERSION)
    arena.append(len(picks) & 0xFF)
    # Byte 2: shared-byte ownership bit map (Stage 0 format): bit i =
    # champion i owns shared-memory byte i. Written spec-true since S2.1
    # put the channel to work (Stage 1 shipped 0 — nothing used it).
    arena.append((1 << len(picks)) - 1 & 0xFF)
    arena.append(_HEADER_RESERVED)

    role_id = 0
    for altar_index, champion_id in enumerate(picks):
        assigned_altar = altar_index if altar_index < altar_count else 0xFF
        record = bytes(
            (
                champion_id & 0xFF,
                assigned_altar & 0xFF,
                zone_id & 0xFF,
                role_id & 0xFF,
                0,
                0,
                0,
            )
        )
        arena.extend(record)
    # S2.3: the boss unlock section rides behind the pick records.  Keep
    # the legacy payload byte-for-byte compatible when S2.3 is disabled.
    if _S23_ENABLED:
        arena.extend(_unlock_section())
    return bytes(arena[:1024])


def play():
    """Run the draft turn: pick, write draft memory."""
    try:
        raw = cambc.draft.current_draft(cambc.Team.ALLY)
        if isinstance(raw, int):
            chosen = ()
        else:
            try:
                chosen = tuple(int(p) for p in raw)
            except TypeError:
                chosen = ()
        chosen_set = set(chosen)

        order = _pick_order()
        print(
            f"draft: team_size={cambc.draft.team_size()} order={order} chosen={chosen}",
            flush=True,
        )
        for champion_id in order:
            if int(champion_id) in chosen_set:
                continue
            picks = chosen + (int(champion_id),)
            payload = _pack_draft_memory(picks)
            print(f"draft: picks={picks} payload_len={len(payload)}", flush=True)
            cambc.draft_memory.submit(payload)
            try:
                cambc.draft.pick(champion_id)
            except Exception as pick_error:
                print(f"draft pick error: {pick_error!r}", flush=True)
                raise
            return
    except Exception as error:
        print(f"draft play error: {error!r}", flush=True)
        raise
