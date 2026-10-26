"""Scouting manager (S2.4) - the ward mini-argmax.

A ward is a FREE parallel action: it spends the champion's TRINKET
CHARGE, not the move/attack allowance (GameRules :116 - "Wards require
their trinket charge; consumables require inventory"), and it can be
relocated once per round (GameRules "One active ward: a new ward replaces
the old one"). Our campaign stats showed wardPlacement = 0 everywhere:
the ward was never placed at all.

THE SCORE is INFORMATION GAIN, in the task's own terms:
  - unseen camp spawn tiles covered by the ward's vision (where the map's
    gem economy actually is),
  - objective-path tiles covered (the route to the altar we bank at),
  - enemy-side altar tiles covered (where the enemy tributes).

FUEL CONTRACT (measured, S2.4): in this wasm Python build ONE simple
statement inside a loop costs ~700 fuel (calibrated: a 100-iteration
`x += i` loop burns 72,003 fuel), and the turn budget is ~2.5M - about
3,000 statements. The first ward implementation scored all 25 cells of
the 5x5 placement box against every interesting tile (~1,400 statements
= ~1.4M fuel) and the campaign ran out of fuel 1,491 times on 00gem_fields
alone. So the argmax here is deliberately SMALL:

  - the interesting tiles are REUSED from the utility layer's farm scan
    (the nearest UNSEEN camp groups - plain ints, no extra scan), plus
    the chosen bank altar and the objective path's midpoint,
  - the candidate set is FIVE placeable tiles within two of us
    (config.WARD_CANDIDATE_CAP), not the whole box,
  - the scoring is a straight distance comparison per (cell, tile) pair.

The ward is placed only when its trinket charge is up (the engine's
cooldown), so the whole argmax runs on a small fraction of the turns;
bot.py mirrors the cooldown locally so the common turn costs one
comparison instead of an engine read.

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


# The candidate tiles: our own tile and the four tiles two steps away
# along the cardinal directions. Five placeable tiles within the
# placement radius of two - the task's "~5 placeable tiles within 2".
_CANDIDATE_OFFSETS = ((0, 0), (2, 0), (-2, 0), (0, 2), (0, -2))

# How many interesting tiles the score looks at (the fuel contract).
_INTEREST_CAP = 5


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


def place_ward(snap, interest) -> bool:
    """The ward mini-argmax: place (or keep) the ward.

    `interest` is the list of tiles worth seeing: [(x, y, weight), ...]
    (the utility layer hands over the nearest unseen camp groups, the
    chosen bank altar and the objective path's midpoint).

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
    if not interest:
        return False
    tiles = interest[:_INTEREST_CAP]
    radius = int(config.WARD_VISION_RADIUS)
    px, py = snap.origin_xy
    # The tile offsets from us (plain ints) - computed once.
    offsets = []
    for tile in tiles:
        offsets.append((tile[0] - px, tile[1] - py, tile[2]))
    # The ward's current gain: the tie rule needs it.
    current = current_ward(snap)
    current_score = -1.0
    if current is not None:
        wx = current[0]
        wy = current[1]
        for tile in tiles:
            dx = tile[0] - wx
            if dx < 0:
                dx = -dx
            dy = tile[1] - wy
            if dy < 0:
                dy = -dy
            if (dx if dx > dy else dy) <= radius:
                current_score += tile[2]
    # Legality: ONE engine read for the whole placement set.
    try:
        legal = me.ward_placement_tiles()
    except Exception:
        return False
    best = None
    best_score = -1.0
    for ox, oy in _CANDIDATE_OFFSETS:
        tx = px + ox
        ty = py + oy
        if current is not None and tx == current[0] and ty == current[1]:
            continue  # the ward is already here: no churn
        score = 0.0
        for dx, dy, weight in offsets:
            ax = dx - ox
            if ax < 0:
                ax = -ax
            ay = dy - oy
            if ay < 0:
                ay = -ay
            if (ax if ax > ay else ay) <= radius:
                score += weight
        if score <= best_score:
            continue
        try:
            vec = cambc.Vec2(tx, ty)
        except Exception:
            continue
        try:
            if not legal.is_set(vec):
                continue
        except Exception:
            continue
        best = vec
        best_score = score
    if best is None:
        return False
    if current is not None and best_score <= current_score:
        # A tie (or worse) leaves the ward where it is.
        return False
    try:
        return bool(me.place_ward(best))
    except Exception:
        return False
