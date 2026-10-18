"""Draft manager (Stage 1).

Pick order: Ryth -> Stellar -> Vesper Vale; if team_size() == 5, append Varg, then Willow.
Writes the Stage 0 draft-memory layout (frozen in plan/architecture.md).
"""
from __future__ import annotations

import cambc
from cambc import game_map


# Stage 0 frozen header.
_HEADER_VERSION = 1
_HEADER_RESERVED = 0


# Sprint 1 pick priority.
_PRIORITY = (
    cambc.ChampionDraft.RYTH,
    cambc.ChampionDraft.STELLAR,
    cambc.ChampionDraft.VESPER_VALE,
    cambc.ChampionDraft.VARG,
    cambc.ChampionDraft.WILLOW,
)


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


def _pack_draft_memory(picks):
    """Build the Sprint 1 draft-memory payload per Stage 0 layout."""
    try:
        altars = game_map.altars().positions()
    except Exception:
        altars = []
    try:
        groups = game_map.camp_groups()
    except Exception:
        groups = ()
    spawn = _nearest_spawn_pos()
    altar_count = len(altars)

    arena = bytearray()
    arena.append(_HEADER_VERSION)
    arena.append(len(picks) & 0xFF)
    arena.append(0)
    arena.append(_HEADER_RESERVED)

    role_id = 0
    for altar_index, champion_id in enumerate(picks):
        zone_id = _camp_zone_nearest_spawn(spawn, groups)
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
