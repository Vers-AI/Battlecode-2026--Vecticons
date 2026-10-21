"""Intelligence manager: persistent per-champion enemy fog memory.

Purpose: track every enemy champion this bot has personally seen and its
last known location. The dict lives on the Bot instance (plain WASM RAM,
survives across turns) — it does NOT touch the 1,024-byte draft memory or
the 8-byte shared memory.

Key decisions:
  - Keyed by the engine handle (int(champion)), the only stable identity:
    Champion view objects are recreated every turn, so id() would never
    match across turns.
  - Records are facts only (round, x, y, champion type). No decisions.
  - Staleness is handled at read time (recent_sightings + ENEMY_TRACK_TTL),
    never by deleting records — old intel can still matter later.

Limitations: per-champion only (vision is not shared; allies never see
this dict — broadcasting sightings would consume the 8-byte shared
memory, a Sprint 2 concern). Positions can be arbitrarily stale; callers
must go through recent_sightings() instead of reading the raw dict.

Update frequency: once per play turn from bot.play_turn().
Read path: snap.enemy_memory (same dict, exposed via the knowledge
snapshot) -> recent_sightings(bot) for TTL-filtered last-known locations.
"""
from __future__ import annotations

import cambc

import config


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
    """
    memory = bot.enemy_memory
    if not snap.is_alive:
        return []
    first_sights: list = []
    round_now = cambc.round_number()
    for enemy in snap.visible_enemies:
        try:
            if not enemy.is_visible():
                continue
            pos = enemy.position()
            record = {
                "round": int(round_now),
                "x": int(pos.x),
                "y": int(pos.y),
                "kind": int(enemy.champion_name()),
            }
        except Exception:
            continue
        key = _handle(enemy)
        if key not in memory:
            first_sights.append(record)
        memory[key] = record
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