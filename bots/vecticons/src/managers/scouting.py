"""Scouting manager (S2.4) - the ward mini-argmax.

A ward is a FREE parallel action: it spends the champion's TRINKET
CHARGE, not the move/attack allowance (GameRules :116 - "Wards require
their trinket charge; consumables require inventory"), and it can be
relocated once per round (GameRules "One active ward: a new ward replaces
the old one"). Our campaign stats showed wardPlacement = 0 everywhere:
the ward was never placed at all.

The score is INFORMATION GAIN, in the task's own terms:
  - unseen camp spawn tiles covered by the ward's vision (where the map's
    gem economy actually is),
  - objective-path tiles covered (the route to the altar we bank at),
  - enemy-side altar tiles covered (where the enemy tributes).

FUEL CONTRACT: the candidate set is the 5x5 box around us (the ward's
placement radius is 2), so the argmax is ~25 tiles, and every interesting
tile is a PLAIN INT cached by the observation layer. No map-wide loop,
no per-tile engine read: the engine is asked for the legal placement
tiles ONCE per round, and only the top WARD_CANDIDATE_CAP candidates by
the coarse score are checked against it.

CHURN: a tie leaves the ward where it is. Relocating is free in gold,
but it throws away the coverage the ward already has, so a place-move-
place-move loop would blind us every round (the ward is our only
persistent vision).

Purpose / update frequency / read path: the ward is placed once per
play turn from bot.play_turn (managers.scouting.place_ward); the engine
then folds the ward's vision into the champion's own vision, so the
observation layer (visible camps, enemy sightings) and the S2.1/S2.2
signal path see through it without any extra code.
"""
from __future__ import annotations

import cambc

import config


# ---------------------------------------------------------------------------
# Helpers (plain-int arithmetic only - a Python call inside the inner loop
# costs real fuel, so the hot loops are inlined).
# ---------------------------------------------------------------------------


def _dist(ax: int, ay: int, bx: int, by: int) -> int:
    """Chebyshev distance between two int tiles."""
    dx = ax - bx
    if dx < 0:
        dx = -dx
    dy = ay - by
    if dy < 0:
        dy = -dy
    return dx if dx > dy else dy


def _gain(tx: int, ty: int, camps, path, altars, radius: int) -> float:
    """Information gain of a ward on tile (tx, ty).

    The ward covers the Chebyshev disk of WARD_VISION_RADIUS around its
    own tile (the engine's ward vision is shadowcast, so this disk is an
    UPPER bound on the real coverage - a coarse but cheap score).
    """
    score = 0.0
    camp_w = float(config.WARD_CAMP_WEIGHT)
    for tile in camps:
        if _dist(tx, ty, tile[0], tile[1]) <= radius:
            score += camp_w
    path_w = float(config.WARD_PATH_WEIGHT)
    for tile in path:
        if _dist(tx, ty, tile[0], tile[1]) <= radius:
            score += path_w
    altar_w = float(config.WARD_ALTAR_WEIGHT)
    for tile in altars:
        if _dist(tx, ty, tile[0], tile[1]) <= radius:
            score += altar_w
    return score


def _coarse_gain(tx: int, ty: int, anchors, path, altars, radius: int) -> float:
    """Cheap pre-score: camp GROUPS (not tiles), the path and the altars."""
    score = 0.0
    camp_w = float(config.WARD_CAMP_WEIGHT)
    for tile in anchors:
        if _dist(tx, ty, tile[0], tile[1]) <= radius:
            score += camp_w
    path_w = float(config.WARD_PATH_WEIGHT)
    for tile in path:
        if _dist(tx, ty, tile[0], tile[1]) <= radius:
            score += path_w
    altar_w = float(config.WARD_ALTAR_WEIGHT)
    for tile in altars:
        if _dist(tx, ty, tile[0], tile[1]) <= radius:
            score += altar_w
    return score


# ---------------------------------------------------------------------------
# The interesting tiles (all local: a ward placed within 2 of us can reach
# at most PLACEMENT + VISION tiles from us, so anything further is out of
# reach by construction).
# ---------------------------------------------------------------------------

# Caps on the per-turn work (fuel contract).
_CAMP_TILE_CAP: int = 16
_CAMP_ANCHOR_CAP: int = 4
_PATH_CAP: int = 6
_ALTAR_CAP: int = 3


def _unseen_camp_tiles(snap):
    """Spawn tiles of the camp groups we have NOT seen yet.

    The static map cache holds every group's spawn tiles as plain ints;
    the visible set is known for free (the observation layer read the
    visible camps this turn). Only the groups inside the ward's reach
    are considered - a ward cannot see further than that from us.
    """
    out = []
    px, py = snap.origin_xy
    anchors = getattr(snap, "group_anchors", None) or {}
    tiles_map = getattr(snap, "group_tile_xy", None) or {}
    visible = getattr(snap, "visible_group_keys", frozenset()) or frozenset()
    reach = int(config.WARD_PLACEMENT_RADIUS) + int(config.WARD_VISION_RADIUS) + 1
    for key, _group in getattr(snap, "group_index", ()) or ():
        if key in visible:
            continue
        entry = anchors.get(key)
        if entry is None:
            continue
        if _dist(entry[0], entry[1], px, py) > reach:
            continue
        for tile in tiles_map.get(key) or ():
            if tile[0] < 0 or tile[1] < 0:
                continue
            out.append(tile)
            if len(out) >= _CAMP_TILE_CAP:
                return out
    return out


def _unseen_camp_anchors(snap):
    """The anchors (one tile per group) of the unseen groups in reach."""
    out = []
    px, py = snap.origin_xy
    anchors = getattr(snap, "group_anchors", None) or {}
    visible = getattr(snap, "visible_group_keys", frozenset()) or frozenset()
    reach = int(config.WARD_PLACEMENT_RADIUS) + int(config.WARD_VISION_RADIUS) + 1
    for key, _group in getattr(snap, "group_index", ()) or ():
        if key in visible:
            continue
        entry = anchors.get(key)
        if entry is None:
            continue
        if _dist(entry[0], entry[1], px, py) > reach:
            continue
        out.append(entry)
        if len(out) >= _CAMP_ANCHOR_CAP:
            break
    return out


def _path_tiles(snap):
    """The objective path: the Chebyshev line from us to the nearest altar.

    The banking route is the objective every champion walks; a ward that
    covers it tells us whether the route is contested before we commit
    the gems to it.
    """
    coords = getattr(snap, "altar_coords", None) or ()
    if not coords:
        return []
    px, py = snap.origin_xy
    best = None
    best_d = 10 ** 9
    for ax, ay in coords:
        d = _dist(ax, ay, px, py)
        if d < best_d:
            best_d = d
            best = (ax, ay)
    if best is None:
        return []
    ax, ay = best
    steps = best_d
    limit = int(config.WARD_VISION_RADIUS) + int(config.WARD_PLACEMENT_RADIUS)
    if steps > limit:
        steps = limit
    if steps > _PATH_CAP:
        steps = _PATH_CAP
    sx = 0 if ax == px else (1 if ax > px else -1)
    sy = 0 if ay == py else (1 if ay > py else -1)
    out = []
    x = px
    y = py
    for _i in range(steps):
        x += sx
        y += sy
        out.append((x, y))
    return out


def _spawn_centre(entries):
    """The centre of a spawn region (plain ints), or None."""
    if not entries:
        return None
    try:
        sx = 0
        sy = 0
        count = 0
        for entry in entries:
            sx += int(entry[0])
            sy += int(entry[1])
            count += 1
        if not count:
            return None
        return (sx // count, sy // count)
    except Exception:
        return None


def _enemy_side_altars(snap):
    """Altars on the ENEMY's half of the map, within the ward's reach.

    The two spawn regions are static map data (cached by the observation
    layer); an altar closer to the enemy spawn than to ours is the altar
    the enemy is most likely to tribute at.
    """
    coords = getattr(snap, "altar_coords", None) or ()
    if not coords:
        return []
    ours = _spawn_centre(getattr(snap, "spawn_coords", None) or ())
    theirs = _spawn_centre(getattr(snap, "enemy_spawn_coords", None) or ())
    if ours is None or theirs is None:
        return []
    px, py = snap.origin_xy
    reach = int(config.WARD_PLACEMENT_RADIUS) + int(config.WARD_VISION_RADIUS) + 1
    out = []
    for ax, ay in coords:
        if _dist(ax, ay, px, py) > reach:
            continue
        if _dist(ax, ay, theirs[0], theirs[1]) > _dist(ax, ay, ours[0], ours[1]):
            continue
        out.append((ax, ay))
        if len(out) >= _ALTAR_CAP:
            break
    return out


# ---------------------------------------------------------------------------
# The mini-argmax.
# ---------------------------------------------------------------------------


def ward_candidates(snap):
    """The placeable tiles with their coarse score: [(score, x, y)]."""
    px, py = snap.origin_xy
    radius = int(config.WARD_VISION_RADIUS)
    anchors = _unseen_camp_anchors(snap)
    path = _path_tiles(snap)
    altars = _enemy_side_altars(snap)
    if not anchors and not path and not altars:
        return []
    placement = int(config.WARD_PLACEMENT_RADIUS)
    out = []
    for dy in range(-placement, placement + 1):
        for dx in range(-placement, placement + 1):
            tx = px + dx
            ty = py + dy
            score = _coarse_gain(tx, ty, anchors, path, altars, radius)
            if score > 0.0:
                out.append((score, tx, ty))
    out.sort(key=lambda item: (-item[0], item[1], item[2]))
    return out


def current_ward(snap):
    """Our active ward's tile as (x, y), or None."""
    try:
        positions = snap.me.ward_positions()
    except Exception:
        return None
    try:
        for pos in positions:
            return (int(pos.x), int(pos.y))
    except Exception:
        return None
    return None


def place_ward(snap) -> bool:
    """The ward mini-argmax: place (or keep) the ward.

    Returns True when a ward was placed this turn. The tie rule is
    explicit: a candidate that does not beat the ward's current gain
    leaves the ward in place (no churn).
    """
    me = snap.me
    # The trinket charge: the engine tells us when another ward can be
    # placed. One engine read, and it skips the whole argmax.
    try:
        if int(me.ward_cooldown()) > 0:
            return False
    except Exception:
        return False
    candidates = ward_candidates(snap)
    if not candidates:
        return False
    radius = int(config.WARD_VISION_RADIUS)
    camps = _unseen_camp_tiles(snap)
    path = _path_tiles(snap)
    altars = _enemy_side_altars(snap)
    # The ward's current gain: the tie rule needs it.
    current = current_ward(snap)
    current_score = -1.0
    if current is not None:
        current_score = _gain(current[0], current[1], camps, path, altars, radius)
    # Legality: ONE engine read for the whole placement set.
    try:
        legal = me.ward_placement_tiles()
    except Exception:
        return False
    cap = int(config.WARD_CANDIDATE_CAP)
    if cap < 1:
        cap = 1
    best = None
    best_score = -1.0
    for _coarse, tx, ty in candidates[:cap]:
        if current is not None and tx == current[0] and ty == current[1]:
            continue  # the ward is already here: no churn
        try:
            vec = cambc.Vec2(tx, ty)
        except Exception:
            continue
        try:
            if not legal.is_set(vec):
                continue
        except Exception:
            continue
        score = _gain(tx, ty, camps, path, altars, radius)
        if score > best_score:
            best_score = score
            best = vec
        break  # the coarse score already ranked them: take the best legal one
    if best is None:
        return False
    if current is not None and best_score <= current_score:
        # A tie (or worse) leaves the ward where it is.
        return False
    try:
        return bool(me.place_ward(best))
    except Exception:
        return False
