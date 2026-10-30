"""F3a - the ability layer: what we can cast, and when it is worth it.

THE GAP THIS MODULE CLOSES
==========================
Until F3a the bot's action vocabulary was move / attack / recall /
submit / ward / buy / consume: `cambc.cast` was never called. Every
P(win), fight, flee, camp-clear and threat number the bot computed was
therefore built on BASIC-ATTACK math only, against an opponent that
casts on a 8-round cooldown. This module is the missing capability
layer, and it is deliberately generic: the policy is expressed in KIT
TYPES (damage / aoe / cc / heal / shield / dash / attack-reset), never
in champion names and never in map names.

ENGINE FACTS (probed on engine 2.0.0 / SDK 4.0.0, 2026-10-29)
=============================================================
The probe (a throwaway bot, since deleted) established:

  * `ability_props(handle, slot)` returns the slot's ability info and
    the target mask. An empty slot returns None.
  * Every slot has an 8-owner-turn-end cooldown (`champion_cooldown`).
  * ONE successful cast per turn, shared by both slots: after a cast the
    champion's CAN_CAST state bit is cleared.
  * A cast does NOT consume the BASIC-ATTACK allowance - the attack
    state bit survives a cast, so a damage spell and a basic attack can
    land in the same turn.
  * A cast DOES consume the MOVE allowance when the ability's
    `exclusive_with_movement` is set ("Excludes walking" in GameRules);
    a heal / shield / attack-reset / dash keeps the movement.
  * An ATTACK RESET must be cast AFTER a basic attack: casting it while
    an attack is still available raises "An available attack cannot be
    reset". After the reset the attack allowance is back, so the
    champion can strike twice in one turn.
  * A ranged champion loses the MOVE allowance when it attacks; a melee
    champion keeps it (GameRules: only melee may walk and attack).
  * Casting a unit spell needs a visible target in range ("Ability out
    of range"), a ground spell needs a legal landing tile ("Forbidden
    landing tile" / "Known occupied destination"), and a dash must land
    on a DIFFERENT tile.
  * `ability_damage_against` / `ability_heal_against` /
    `ability_shield_against` give the EXACT number against a specific
    target (mitigation and shields included); the argument-less
    variants give the raw focus-scaled number.

THE POLICY (one rule per kit type)
==================================
  damage / aoe  - cast at the target when it is in range and the burst
                  is at least our basic attack; it is folded INTO the
                  fight action, not bolted on top of it.
  cc            - same, but the target is chosen by priority: a
                  tribute-channeler first (a stun cancels the channel),
                  then a low-HP target, then a carrier.
  heal / shield - cast when a hostile champion is close and the target
                  (ourselves or a nearby ally) is hurt enough for the
                  restoration to matter.
  dash          - engage when chasing (land next to the target so the
                  landing damage and stun reach it), escape when the
                  flee candidate wins (land away from the threat).
  attack reset  - cast AFTER a basic attack, so the champion strikes
                  twice.

FUEL CONTRACT (the hard one - the engine charges CPU instructions)
================================================================
The turn budget is ~2.5M fuel for the Python build (~3,000 statements
at ~700 fuel each, measured in S2.4). The first F3a build cost ~3x the
baseline fuel on the spot-check maps, so every read here is budgeted:

  * OUR slots: the SDK caches `Ability.info()` and
    `Ability.valid_targets()` inside the Ability object, and
    `Champion.abilities()` caches the Ability objects on the champion.
    So our per-turn cost is TWO `champion_cooldown` reads (the only
    volatile field) plus a handful of dict writes. The slot dicts
    themselves are built ONCE per process and mutated in place.
  * The CAN_CAST state is read lazily, once per turn, and only when a
    cast is actually being considered. After a successful cast the
    module's own flag closes the turn's single cast.
  * ENEMY slots: `vision.champions()` builds a FRESH Champion object
    every turn, so `enemy.abilities()` costs TWO `ability_props` engine
    reads. The enemy's slot objects are therefore cached per engine
    handle (the handle is stable for the life of the match - the draft
    fixes the kit of every slot), and only the cooldown is re-read.
  * The enemy's burst is read ONLY for the enemies that can actually
    reach us (the threat view's contact and near lists) - a far-away
    enemy's ability is not incoming damage. That is both cheaper and
    more correct than reading the two closest.
  * OUR burst: the exact "against this target" number is read only for
    the enemy we are actually fighting; the other candidates use the
    raw (focus-scaled) number, which is ONE read per turn.
"""
from __future__ import annotations

import cambc

import config


# ---------------------------------------------------------------------------
# Constants.
# ---------------------------------------------------------------------------

# Ability kinds (the values of cambc.AbilityKind; mirrored so the module
# does not depend on the enum's import-time shape).
KIND_RESET = 1
KIND_DAMAGE = 2
KIND_AOE = 3
KIND_CC = 4
KIND_HEAL = 5
KIND_DASH = 6
KIND_SHIELD = 7

# Target-mask bits (the values of cambc.AbilityTargetMask).
T_CAMPS = 1
T_ENEMIES = 2
T_ALLIES = 4
T_SELF = 8
T_GROUND = 16

# The kit types that carry a burst of damage.
DAMAGE_KINDS = (KIND_DAMAGE, KIND_AOE, KIND_CC)

# Evidence counters (the campaign reads these off the stdout).
_CASTS = 0
_CAST_BY_KIND = {}
_CAST_PRINTS = 0
_MAX_CAST_PRINTS = 20
# Set when a cast happens: the evidence line is printed at least once
# more after the last cast, so the campaign's "last line per champion"
# harvest reads the FINAL totals even on a match that ends before the
# next 100-round tick.
_EVIDENCE_DIRTY = False

# Our own slot dicts, built once per process (the SDK caches the Ability
# objects and their properties, so nothing here has to be re-read).
_OUR_SLOTS = {}
# The enemy slot dicts, cached per engine handle. The handle is stable
# for the life of the match (the draft fixes the kit of every slot).
_ENEMY_SLOTS = {}


# --- FUEL PROBES (the section-cost instrument) ---
#
# The probe reads the fuel before and after a section and prints the
# delta every 25th call. FUEL: `cambc.fuel_remaining()` is a HOST call
# and it is NOT cheap - measured at ~6.5k fuel a read - so probing every
# call cost ~26k fuel per turn, i.e. ~1.4% of the turn budget, which is
# enough to push the top percent of turns over the ceiling and to show
# up in the campaign's fuelExhausted count (the F3 campaign measured
# 61,164 exhaustions with the probe on every call).
#
# The probe is an INSTRUMENT, not a mechanism: it reads fuel and prints.
# It never changes a decision. So it samples (every 25th call) and the
# section costs stay measurable at 1/25 of the price.
_PROBE_N = {}
_PROBE_EVERY = 25


def _p0(name):
    """The fuel before a section, or -1 when this call is not sampled."""
    n = _PROBE_N.get(name, 0) + 1
    _PROBE_N[name] = n
    if n % _PROBE_EVERY != 0:
        return -1
    try:
        return int(cambc.fuel_remaining())
    except Exception:
        return -1


def _p1(name, f0):
    """The fuel after a section; prints the delta on a sampled call."""
    if f0 < 0:
        # Not a sampled call: do NOT read the fuel (the read is the
        # expensive part).
        return
    try:
        f1 = int(cambc.fuel_remaining())
    except Exception:
        return
    print(f"f3probe: {name} cost={f0 - f1}", flush=True)


def counters() -> dict:
    """The ability-layer evidence counters."""
    by_kind = {}
    for kind, count in _CAST_BY_KIND.items():
        by_kind[_kind_name(kind)] = int(count)
    return {"casts": int(_CASTS), "by_kind": by_kind}


def evidence_dirty() -> bool:
    """Has a cast happened since the last evidence line was printed?"""
    return _EVIDENCE_DIRTY


def clear_evidence_dirty() -> None:
    """Mark the evidence line as printed."""
    global _EVIDENCE_DIRTY
    _EVIDENCE_DIRTY = False


def _kind_name(kind: int) -> str:
    """The kit-type name of an ability kind (for the evidence line)."""
    return {
        KIND_RESET: "reset",
        KIND_DAMAGE: "damage",
        KIND_AOE: "aoe",
        KIND_CC: "cc",
        KIND_HEAL: "heal",
        KIND_DASH: "dash",
        KIND_SHIELD: "shield",
    }.get(int(kind), "other")


def _count_cast(kind: int) -> None:
    global _CASTS, _CAST_PRINTS, _EVIDENCE_DIRTY
    _CASTS += 1
    _EVIDENCE_DIRTY = True
    try:
        _CAST_BY_KIND[int(kind)] = _CAST_BY_KIND.get(int(kind), 0) + 1
    except Exception:
        pass
    if _CAST_PRINTS < _MAX_CAST_PRINTS:
        _CAST_PRINTS += 1
        try:
            print(
                f"f3a: cast kind={_kind_name(kind)} casts={_CASTS}",
                flush=True,
            )
        except Exception:
            pass


def _kind_of(info) -> int:
    """The kit type of an ability-info record (0 when unknown)."""
    try:
        if isinstance(info, cambc.AbilityAttackReset):
            return KIND_RESET
        if isinstance(info, cambc.AbilityDamage):
            return KIND_DAMAGE
        if isinstance(info, cambc.AbilityAoeDamage):
            return KIND_AOE
        if isinstance(info, cambc.AbilityCc):
            return KIND_CC
        if isinstance(info, cambc.AbilityHeal):
            return KIND_HEAL
        if isinstance(info, cambc.AbilityDash):
            return KIND_DASH
        if isinstance(info, cambc.AbilityShield):
            return KIND_SHIELD
    except Exception:
        pass
    return 0


def _chebyshev(ax: int, ay: int, bx: int, by: int) -> int:
    dx = ax - bx
    if dx < 0:
        dx = -dx
    dy = ay - by
    if dy < 0:
        dy = -dy
    return dx if dx > dy else dy


def _in_range(snap, slot, ex: int, ey: int) -> bool:
    """Is the target inside the slot's cast range?"""
    rng = slot["range"]
    if rng <= 0.0:
        return True
    px, py = snap.origin_xy
    return _chebyshev(px, py, ex, ey) <= rng


# ---------------------------------------------------------------------------
# Per-turn facts.
# ---------------------------------------------------------------------------


def _build_slot(index: int, ability) -> dict:
    """The static half of a slot: kind, mask, range, radius, flags.

    Every field here is a property of the KIT, not of the turn: the SDK
    caches `info()` and `valid_targets()` inside the Ability object, so
    this is paid once per process.
    """
    try:
        info = ability.info()
    except Exception:
        info = None
    if info is None:
        return None
    try:
        mask = int(ability.valid_targets())
    except Exception:
        mask = 0
    try:
        rng = float(getattr(info, "range", 0.0) or 0.0)
    except Exception:
        rng = 0.0
    try:
        radius = float(getattr(info, "radius", 0.0) or 0.0)
    except Exception:
        radius = 0.0
    try:
        duration = int(getattr(info, "duration", 0) or 0)
    except Exception:
        duration = 0
    try:
        modifier = float(getattr(info, "modifier", 0.0) or 0.0)
    except Exception:
        modifier = 0.0
    return {
        "slot": index,
        "ability": ability,
        "kind": _kind_of(info),
        "mask": mask,
        "cooldown": 99,
        "exclusive": bool(getattr(info, "exclusive_with_movement", False)),
        "range": rng,
        "radius": radius,
        "duration": duration,
        "modifier": modifier,
        "enhanced": bool(getattr(info, "enhanced_attack", False)),
    }


def facts(snap) -> dict:
    """Our ability slots for this turn (cached on the snapshot).

    FUEL: the slot dicts are built once per process; per turn this
    refreshes the only volatile field - the cooldown - with two engine
    reads, and reuses the cached "ready" lists.
    """
    cached = getattr(snap, "ability_facts", None)
    if cached is not None:
        return cached
    _pf = _p0("facts")
    slots = []
    ready = []
    ready_damage = []
    try:
        abilities = snap.me.abilities()
    except Exception:
        abilities = ()
    for index, ability in enumerate(abilities):
        slot = _OUR_SLOTS.get(index)
        if slot is None or slot["ability"] is not ability:
            slot = _build_slot(index, ability)
            if slot is None:
                continue
            _OUR_SLOTS[index] = slot
        try:
            slot["cooldown"] = int(ability.cooldown())
        except Exception:
            slot["cooldown"] = 99
        slots.append(slot)
        if slot["cooldown"] == 0:
            ready.append(slot)
            if slot["kind"] in DAMAGE_KINDS:
                ready_damage.append(slot)
    _p1("facts", _pf)
    out = {"slots": tuple(slots), "ready": tuple(ready),
           "ready_damage": tuple(ready_damage)}
    try:
        snap.ability_facts = out
    except Exception:
        pass
    return out


def can_cast_now(snap) -> bool:
    """Is the one cast of this turn still unused?

    The engine clears the CAN_CAST state bit after a successful cast. We
    read that bit ONCE per turn (lazily, only when a cast is actually
    considered) and then keep our own flag, so the second and third
    policy rules of a combat turn cost nothing.
    """
    try:
        if getattr(snap, "ability_cast_used", False):
            return False
    except Exception:
        pass
    state = getattr(snap, "ability_state", None)
    if state is None:
        _pf = _p0("can_cast")
        try:
            state = bool(cambc.ChampionState.CAN_CAST in snap.me.state())
        except Exception:
            state = False
        try:
            snap.ability_state = state
        except Exception:
            pass
        _p1("can_cast", _pf)
    return bool(state)


def ready_slots(snap) -> list:
    """The slots that are off cooldown and castable this turn."""
    return list(facts(snap)["ready"])


def damage_slots(snap) -> list:
    """The ready slots that carry a damage burst."""
    return list(facts(snap)["ready_damage"])


def slot_amount(slot, target=None, what: str = "damage") -> float:
    """The number an ability would produce (damage / heal / shield)."""
    try:
        ability = slot["ability"]
        if what == "heal":
            if target is None:
                return float(ability.heal_amount())
            return float(ability.heal_amount(target))
        if what == "shield":
            if target is None:
                return float(ability.shield_amount())
            return float(ability.shield_amount(target))
        if target is None:
            return float(ability.damage_amount())
        return float(ability.damage_amount(target))
    except Exception:
        return 0.0


# ---------------------------------------------------------------------------
# Burst estimates (the numbers the fight / threat / clear maths use).
# ---------------------------------------------------------------------------


def _bursts(snap) -> dict:
    """The per-turn burst table: the raw damage of the slots that matter.

    FUEL (measured 2026-10-29): ONE ability read costs ~25k fuel and the
    turn budget is ~2.49M, so a read per slot per candidate is the whole
    budget. The table is therefore built ONCE per turn with ONE read per
    DISTINCT slot that matters (usually one), and every consumer - the
    camp burst, the fight window, the camp cast and the offensive cast -
    reads the plain number out of it.

    Keys:
      read       - {slot index: the raw focus-scaled damage}
      camp       - our best ready damage against a camp
      enemy      - our best ready damage against an enemy
      reach      - the widest tile a ready damage slot can reach
      best_enemy - the slot the "enemy" number came from
      best_camp  - the slot the "camp" number came from
    """
    cached = getattr(snap, "ability_bursts", None)
    if cached is not None:
        return cached
    best_enemy = None
    best_camp = None
    dash = None
    for slot in facts(snap)["ready_damage"]:
        mask = slot["mask"]
        if (mask & T_ENEMIES) or (mask & T_GROUND):
            if best_enemy is None or slot["modifier"] > best_enemy["modifier"]:
                best_enemy = slot
        if mask & T_CAMPS:
            if best_camp is None or slot["modifier"] > best_camp["modifier"]:
                best_camp = slot
    for slot in facts(snap)["ready"]:
        if slot["kind"] == KIND_DASH and slot["modifier"] > 0.0:
            if dash is None or slot["modifier"] > dash["modifier"]:
                dash = slot
    read = {}
    if best_enemy is not None:
        read[best_enemy["slot"]] = slot_amount(best_enemy, None)
    if best_camp is not None and best_camp is not best_enemy:
        read[best_camp["slot"]] = slot_amount(best_camp, None)
    if dash is not None and dash["slot"] not in read:
        read[dash["slot"]] = slot_amount(dash, None)
    camp = 0.0
    if best_camp is not None:
        camp = read.get(best_camp["slot"], 0.0)
    enemy = 0.0
    reach = 0.0
    if best_enemy is not None:
        enemy = read.get(best_enemy["slot"], 0.0)
        span = best_enemy["range"] + best_enemy["radius"]
        if span > reach:
            reach = span
    out = {"read": read, "camp": camp, "enemy": enemy, "reach": reach,
           "best_enemy": best_enemy, "best_camp": best_camp, "dash": dash}
    try:
        snap.ability_bursts = out
    except Exception:
        pass
    return out


def slot_damage(snap, slot) -> float:
    """The raw damage of a slot, from the per-turn table (0 if unknown)."""
    try:
        return float(_bursts(snap)["read"].get(slot["slot"], 0.0))
    except Exception:
        return 0.0


def raw_burst(snap) -> float:
    """OUR best ready ability damage, with no target (focus-scaled)."""
    try:
        return float(_bursts(snap)["enemy"])
    except Exception:
        return 0.0


def raw_burst_reach(snap) -> tuple:
    """(our raw burst, the reach of the slots that carry it)."""
    try:
        b = _bursts(snap)
        return float(b["enemy"]), float(b["reach"])
    except Exception:
        return 0.0, 0.0


def raw_burst_within(snap, ex: int, ey: int) -> float:
    """The raw burst, but only when a damage slot can reach the target."""
    burst, reach = raw_burst_reach(snap)
    if burst <= 0.0:
        return 0.0
    px, py = snap.origin_xy
    if _chebyshev(px, py, ex, ey) > reach:
        return 0.0
    return burst


def our_burst_against(snap, enemy, ex: int, ey: int) -> float:
    """The damage our best ready ability would land on THIS enemy.

    This is the F3a fold into the fight math: the fight window counts
    the hits our basic attack needs, and the ability burst removes some
    of them (a one-shot burst at the start of the fight, not a per-turn
    damage source - the cooldown is 8 rounds).

    FUEL: the exact number comes from `ability_damage_against` (one
    engine read per candidate slot), and only for the slots that are
    actually ready and in range.
    """
    _pf = _p0("our_burst")
    px, py = snap.origin_xy
    best = 0.0
    for slot in facts(snap)["ready_damage"]:
        kind = slot["kind"]
        mask = slot["mask"]
        if kind == KIND_AOE:
            # An aoe spell is cast at a GROUND tile; it still needs the
            # enemy tile to be inside the cast range.
            if not (mask & (T_GROUND | T_ENEMIES | T_CAMPS)):
                continue
        else:
            if not (mask & T_ENEMIES):
                continue
        if not _in_range(snap, slot, ex, ey):
            continue
        dmg = slot_amount(slot, enemy)
        if dmg > best:
            best = dmg
    _p1("our_burst", _pf)
    # A DASH's landing damage reaches the tiles around the landing tile.
    # It counts as burst when the target is close enough for a landing
    # next to it to reach the target.
    for slot in facts(snap)["ready"]:
        if slot["kind"] != KIND_DASH:
            continue
        if slot["modifier"] <= 0.0:
            continue
        reach = slot["range"] + max(1.0, slot["radius"])
        if _chebyshev(px, py, ex, ey) > reach:
            continue
        dmg = slot_amount(slot, enemy)
        if dmg > best:
            best = dmg
    return best


def _enemy_slots(enemy) -> list:
    """The enemy's slot dicts, cached per engine handle.

    `vision.champions()` rebuilds the Champion object every turn, so
    `enemy.abilities()` would cost TWO `ability_props` engine reads
    every turn for every enemy. The handle is stable for the life of
    the match (the draft fixes the kit of every slot), so the slots are
    built once and only the cooldown is refreshed.
    """
    try:
        handle = int(enemy.__index__())
    except Exception:
        return []
    slots = _ENEMY_SLOTS.get(handle)
    if slots is None:
        try:
            abilities = enemy.abilities()
        except Exception:
            return []
        slots = []
        for index, ability in enumerate(abilities):
            slot = _build_slot(index, ability)
            if slot is not None:
                slots.append(slot)
        if slots:
            # An empty build means the champion was not in vision when
            # its properties were read; do NOT cache that, so the slots
            # are read again on the next turn.
            _ENEMY_SLOTS[handle] = slots
        return slots


def enemy_burst_against(snap, enemy, ex: int, ey: int) -> float:
    """The damage a visible enemy's ready ability could land on US.

    This is the F3a fold into the threat model: an enemy with its
    cooldowns down is much more dangerous than its basic attack says,
    and every P(survive) the bot computes has to know that.

    FUEL: only the enemies that can actually reach us are priced (the
    caller passes the threat view's contact and near lists), and each of
    them costs one cooldown read per slot plus one damage read.
    """
    _pf = _p0("enemy_burst")
    px, py = snap.origin_xy
    best = 0.0
    for slot in _enemy_slots(enemy):
        kind = slot["kind"]
        if kind not in DAMAGE_KINDS and kind != KIND_DASH:
            continue
        try:
            cooldown = int(slot["ability"].cooldown())
        except Exception:
            continue
        slot["cooldown"] = cooldown
        if cooldown != 0:
            continue
        rng = slot["range"]
        if rng > 0.0 and _chebyshev(px, py, ex, ey) > rng:
            continue
        try:
            dmg = float(slot["ability"].damage_amount(snap.me))
        except Exception:
            continue
        if dmg > best:
            best = dmg
    _p1("enemy_burst", _pf)
    return best


def camp_burst(snap) -> float:
    """The damage our ready ability would land on a camp.

    Used by the camp-clear time estimate: a burst changes the clear
    arithmetic (and therefore the "too strong to engage" verdicts),
    which the basic-attack-only estimate got wrong.

    FUEL: the RAW number (one engine read per ready damage slot),
    cached for the whole turn. The targeted number would cap at one
    camp member's health, which is not what the clear estimate needs.
    """
    cached = getattr(snap, "ability_camp_burst", None)
    if cached is not None:
        return float(cached)
    _pf = _p0("camp_burst")
    best = 0.0
    for slot in facts(snap)["ready_damage"]:
        if not (slot["mask"] & T_CAMPS):
            continue
        dmg = slot_amount(slot, None)
        if dmg > best:
            best = dmg
    _p1("camp_burst", _pf)
    try:
        snap.ability_camp_burst = best
    except Exception:
        pass
    return best


# ---------------------------------------------------------------------------
# Casting.
# ---------------------------------------------------------------------------


def _cast(snap, slot, target=None, xy=None) -> bool:
    """Perform one cast. Never raises: a refused cast is a no-op."""
    try:
        ability = slot["ability"]
        if xy is not None:
            ok = ability.use(cambc.Vec2(int(xy[0]), int(xy[1])))
        elif target is not None:
            ok = ability.use(target)
        else:
            ok = ability.use(None)
    except Exception:
        return False
    if ok:
        # The turn's single cast is spent: the engine clears its
        # CAN_CAST state bit, and so do we.
        try:
            snap.ability_cast_used = True
            snap.ability_state = False
        except Exception:
            pass
        _count_cast(slot["kind"])
    return bool(ok)


def _target_label(target, xy) -> str:
    if xy is not None:
        return f"({int(xy[0])},{int(xy[1])})"
    if target is None:
        return "self"
    try:
        return f"{target.champion_name()}"
    except Exception:
        pass
    try:
        pos = target.position()
        return f"({int(pos.x)},{int(pos.y)})"
    except Exception:
        return "?"


def can_move_after(slot) -> bool:
    """May we still walk after casting this ability?"""
    return not bool(slot.get("exclusive"))


# ---------------------------------------------------------------------------
# Policy: the per-kit-type rules.
# ---------------------------------------------------------------------------


def _basic_damage(snap) -> float:
    """Our basic-attack damage (the yardstick every cast is measured in)."""
    try:
        from managers import utility
        return utility._our_damage(snap)
    except Exception:
        return 10.0


def _is_channeler(enemy) -> bool:
    """Is this enemy in a channel (tribute or recall) right now?

    A stun CANCELS a channel (GameRules: damage does not stop a tribute,
    a stun does), so a channeler is the best crowd-control target.
    """
    try:
        if enemy.is_submitting_gems():
            return True
    except Exception:
        pass
    try:
        if enemy.is_recalling():
            return True
    except Exception:
        pass
    return False


def _cc_target(snap, enemies) -> tuple:
    """The enemy a crowd-control spell should land on.

    The priority follows the fight-candidate ordering: a tribute
    CHANNELER first (a stun cancels the channel - GameRules: damage does
    not stop a tribute, a stun does), then a low-HP target (a certain
    kill), then a CARRIER (the kill takes the bag).
    """
    best = None
    best_score = -1.0
    for entry in enemies or ():
        try:
            enemy, dist, dmg, pos = entry
            ex = int(pos.x)
            ey = int(pos.y)
        except Exception:
            continue
        score = 0.0
        if _is_channeler(enemy):
            score += float(config.ABILITY_CC_CHANNEL_BONUS)
        try:
            hp = float(enemy.health())
            max_hp = float(enemy.max_health())
            if max_hp > 0.0 and hp <= max_hp * float(config.ABILITY_CC_LOW_HP_FRAC):
                score += float(config.ABILITY_CC_LOW_HP_BONUS)
        except Exception:
            pass
        try:
            if int(enemy.gem_count()) > 0:
                score += float(config.ABILITY_CC_CARRIER_BONUS)
        except Exception:
            pass
        if score > best_score:
            best_score = score
            best = (enemy, ex, ey)
    return best


def _visible_enemies(snap) -> list:
    """The visible enemies (the threat view's 'all' list), cached."""
    try:
        from managers import utility
        view = utility._hostile_threat(snap)
        return view.get("all", ())
    except Exception:
        return ()


def _our_reach(snap) -> int:
    """Our own basic-attack range (1 for melee, 3 for ranged)."""
    try:
        return int(snap.me.attack_range())
    except Exception:
        return 1


def _ring(cx: int, cy: int, d: int):
    """The tiles at Chebyshev distance exactly d from (cx, cy)."""
    if d <= 0:
        return ((cx, cy),)
    out = []
    for x in range(cx - d, cx + d + 1):
        out.append((x, cy - d))
    for x in range(cx - d, cx + d + 1):
        out.append((x, cy + d))
    for y in range(cy - d + 1, cy + d):
        out.append((cx - d, y))
    for y in range(cy - d + 1, cy + d):
        out.append((cx + d, y))
    return out


def _dash_engage_tile(snap, ex: int, ey: int, rng: float, radius: float):
    """The tiles a dash should land on to reach a target.

    The landing tile must be inside the dash's travel range AND inside
    our own basic-attack range of the target: the whole point of the
    engage dash is that it turns a chase into a strike. A dash with a
    LANDING EFFECT (damage + stun) additionally needs the target inside
    the landing radius, so the landing tile is next to it.

    FUEL: the tiles are enumerated as RINGS around the target (the
    closest first), not as the whole dash box - the box is up to 81
    tiles of pure Python work for a single cast attempt, while the
    first ring is 8 tiles and almost always holds a legal landing.
    """
    px, py = snap.origin_xy
    limit = int(rng) if rng >= 1.0 else 1
    reach = _our_reach(snap)
    max_d = reach
    if radius >= 1.0 and radius < max_d:
        max_d = radius
    for d in range(0, max_d + 1):
        found = []
        for tx, ty in _ring(ex, ey, d):
            if tx == px and ty == py:
                continue
            if _chebyshev(px, py, tx, ty) > limit:
                continue
            if _blocked(snap, (tx, ty)):
                continue
            found.append((_chebyshev(px, py, tx, ty), tx, ty))
        if found:
            found.sort()
            return found
    return []


def _dash_escape_tile(snap, ex: int, ey: int, rng: float):
    """The tiles a dash could land on to break contact.

    Ranked by the distance from the threat (farthest first): the escape
    dash is only worth its cooldown when it takes us OUT of the threat's
    reach.

    FUEL: the FARTHEST tile of a square box is always one of its four
    corners, so the corners are tried first (four tiles) and the whole
    box is enumerated only when they are all blocked.
    """
    px, py = snap.origin_xy
    limit = int(rng) if rng >= 1.0 else 1
    corners = ((px - limit, py - limit), (px + limit, py - limit),
               (px - limit, py + limit), (px + limit, py + limit))
    ranked = sorted(corners, key=lambda t: -_chebyshev(t[0], t[1], ex, ey))
    out = []
    for tx, ty in ranked:
        if tx == px and ty == py:
            continue
        if _blocked(snap, (tx, ty)):
            continue
        out.append((-_chebyshev(tx, ty, ex, ey), 0, tx, ty))
    if out:
        return out
    candidates = []
    for dx in range(-limit, limit + 1):
        for dy in range(-limit, limit + 1):
            if dx == 0 and dy == 0:
                continue
            tx = px + dx
            ty = py + dy
            if _blocked(snap, (tx, ty)):
                continue
            candidates.append((-_chebyshev(tx, ty, ex, ey),
                               abs(dx) + abs(dy), tx, ty))
    candidates.sort()
    return candidates


def _blocked(snap, tile) -> bool:
    """Is the tile statically impassable (wall / hole / altar)?

    FUEL: the terrain wall set is cached on the snapshot (one map read
    for the whole match), so this is a bitboard test and no engine call.
    """
    walls = getattr(snap, "terrain_walls", None)
    if walls is None:
        return False
    try:
        return bool(walls.is_set(cambc.Vec2(int(tile[0]), int(tile[1]))))
    except Exception:
        return False


def _dash_slot(snap):
    """The ready dash slot, if any."""
    for slot in facts(snap)["ready"]:
        if slot["kind"] == KIND_DASH:
            return slot
    return None


def dash_ready(snap) -> bool:
    """Is a dash off cooldown and castable this turn?"""
    return _dash_slot(snap) is not None


def dash_engage(snap, enemy, ex: int, ey: int) -> bool:
    """Dash next to a target we are chasing (the landing hits it).

    The dash only fires when it actually turns the chase into a strike:
    the landing tile has to be inside our own attack range of the target.

    A RANGED champion does not need the engage dash - it already
    attacks at range, and the dash would spend its move allowance
    (a ranged champion loses the attack when it moves). The ranged
    engage is only worth it when the dash carries a LANDING effect
    (damage + stun), which reaches the target from further away.
    """
    slot = _dash_slot(snap)
    if slot is None:
        return False
    rng = slot["range"]
    if rng <= 0.0:
        rng = 1.0
    radius = slot["radius"]
    landing = slot["modifier"] > 0.0
    if landing:
        if radius < 1.0:
            radius = 1.0
    else:
        radius = 0.0
        if _our_reach(snap) > 1:
            # A ranged champion: the dash would only cost us the shot.
            return False
    candidates = _dash_engage_tile(snap, ex, ey, rng, radius)
    if not candidates:
        return False
    tried = 0
    for entry in candidates:
        tile = (entry[1], entry[2])
        tried += 1
        if _cast(snap, slot, xy=tile):
            return True
        if tried >= 3:
            break
    return False


def dash_escape(snap, enemy, ex: int, ey: int) -> bool:
    """Dash away from the threat we are fleeing.

    The dash only fires when it takes us OUT of the threat's reach: an
    escape that leaves us in range is not worth the 8-round cooldown.
    """
    slot = _dash_slot(snap)
    if slot is None:
        return False
    rng = slot["range"]
    if rng <= 0.0:
        rng = 1.0
    try:
        reach = int(enemy.attack_range())
    except Exception:
        reach = 1
    px, py = snap.origin_xy
    if _chebyshev(px, py, ex, ey) <= reach:
        # Already in contact: the dash has to add real distance.
        need = reach + 1
    else:
        need = reach
    candidates = _dash_escape_tile(snap, ex, ey, rng)
    tried = 0
    for entry in candidates:
        tx = entry[2]
        ty = entry[3]
        d = _chebyshev(tx, ty, ex, ey)
        if d < need:
            continue
        tried += 1
        if _cast(snap, slot, xy=(tx, ty)):
            return True
        if tried >= 3:
            break
    return False


def heal_slot(snap):
    """The ready heal slot, if any."""
    for slot in facts(snap)["ready"]:
        if slot["kind"] == KIND_HEAL:
            return slot
    return None


def shield_slot(snap):
    """The ready shield slot, if any."""
    for slot in facts(snap)["ready"]:
        if slot["kind"] == KIND_SHIELD:
            return slot
    return None


def _ally_targets(snap, mask: int) -> list:
    """The healable/shieldable allies as (champion, hp, max_hp).

    The mask decides WHO can be targeted: a SELF spell can take us, an
    ALLIES-only spell needs another champion (GameRules: Willow's heal
    targets "another allied champion"). A target that the mask does not
    allow is not even a candidate - the engine would refuse the cast.
    """
    out = []
    try:
        me = snap.me
        if mask & T_SELF:
            out.append((me, float(me.health()), float(me.max_health())))
    except Exception:
        pass
    if not (mask & T_ALLIES):
        return out
    try:
        for ally in cambc.vision.champions(cambc.Team.ALLY):
            try:
                if ally is snap.me:
                    continue
                out.append((ally, float(ally.health()), float(ally.max_health())))
            except Exception:
                continue
    except Exception:
        pass
    return out


def _threat_close(snap) -> bool:
    """Is a hostile champion close enough to warrant a heal or a shield?"""
    try:
        from managers import utility
        view = utility._hostile_threat(snap)
    except Exception:
        return False
    if view.get("contact"):
        return True
    if view.get("near"):
        return True
    return False


def defensive_cast(snap) -> bool:
    """Heal or shield when the incoming burst warrants it.

    The rule: a hostile champion is close, and the target (ourselves or
    a nearby ally) is hurt enough that the restoration changes the
    outcome. The amount comes from the engine (`ability_heal_against` /
    `ability_shield_against`), so it is the real number, capped at the
    target's missing health.

    FUEL: the threat test is FREE (the threat view is cached on the
    snapshot), and it comes FIRST - a quiet turn pays nothing at all.
    """
    if not _threat_close(snap):
        return False
    if not can_cast_now(snap):
        return False
    heal = heal_slot(snap)
    if heal is not None:
        best = None
        best_amount = 0.0
        for champion, hp, max_hp in _ally_targets(snap, heal["mask"]):
            if max_hp <= 0.0:
                continue
            if hp >= max_hp * (1.0 - float(config.ABILITY_HEAL_MIN_FRAC)):
                continue
            amount = slot_amount(heal, champion, "heal")
            if amount <= 0.0:
                continue
            if amount < best_amount:
                continue
            best = champion
            best_amount = amount
        if best is not None:
            if _cast(snap, heal, target=best):
                return True
    shield = shield_slot(snap)
    if shield is not None:
        try:
            me = snap.me
            if me.buffs().has_shield():
                return False
        except Exception:
            pass
        for champion, hp, max_hp in _ally_targets(snap, shield["mask"]):
            if max_hp <= 0.0:
                continue
            if hp >= max_hp * (1.0 - float(config.ABILITY_SHIELD_MIN_FRAC)):
                continue
            amount = slot_amount(shield, champion, "shield")
            if amount <= 0.0:
                continue
            if _cast(snap, shield, target=champion):
                return True
    return False


def attack_reset(snap) -> bool:
    """Cast the attack reset so the champion can strike twice.

    The engine refuses a reset while an attack is still available, so
    this is called AFTER the basic attack (bot.py's fight path).
    """
    if not can_cast_now(snap):
        return False
    for slot in facts(snap)["ready"]:
        if slot["kind"] != KIND_RESET:
            continue
        if not (slot["mask"] & T_SELF):
            continue
        return _cast(snap, slot)
    return False


def _target_hp(enemy) -> float:
    """The target's current health (0 when unknown)."""
    try:
        return float(enemy.health())
    except Exception:
        return 0.0


def offensive_cast(snap, enemy, ex: int, ey: int, allow_walk: bool = True) -> bool:
    """Cast the best damage/cc spell at a target.

    `allow_walk` is False when the caller is about to walk anyway (a
    champion closing on a target): a spell that "excludes walking" would
    cost us the step, and a champion that cannot close never lands the
    basic attack either. The exception is a spell that ENDS the
    engagement (it kills the target): then the step does not matter.
    """
    if enemy is None:
        return False
    if not can_cast_now(snap):
        return False
    _pf = _p0("offensive_cast")
    basic = _basic_damage(snap)
    threshold = basic * float(config.ABILITY_CAST_MIN_DAMAGE_FACTOR)
    best_slot = None
    best_dmg = 0.0
    best_target = None
    best_xy = None
    fallback_slot = None
    fallback_target = None
    for slot in facts(snap)["ready_damage"]:
        kind = slot["kind"]
        mask = slot["mask"]
        target = enemy
        xy = None
        if kind == KIND_CC:
            # The CC target is chosen by priority: a channeler first (a
            # stun cancels the channel), else the enemy we are fighting.
            picked = _cc_target(snap, _visible_enemies(snap))
            if picked is not None:
                cand, cx, cy = picked
                if _in_range(snap, slot, cx, cy):
                    target = cand
                    ex = cx
                    ey = cy
        if kind == KIND_AOE:
            if not (mask & (T_GROUND | T_ENEMIES | T_CAMPS)):
                continue
            if not (mask & T_ENEMIES) and not (mask & T_GROUND):
                continue
        else:
            if not (mask & T_ENEMIES):
                continue
        if not _in_range(snap, slot, ex, ey):
            continue
        if not allow_walk and slot["exclusive"]:
            # The spell would cost us the step. It is still worth it when
            # it ENDS the engagement.
            hp = _target_hp(target)
            dmg_probe = slot_amount(slot, target)
            if hp <= 0.0 or dmg_probe < hp:
                continue
        dmg = slot_amount(slot, target)
        if dmg <= 0.0:
            # A pure stun (no damage) still has value in a fight: it
            # takes the enemy's turn away. It is the fallback.
            if kind == KIND_CC and fallback_slot is None:
                fallback_slot = slot
                fallback_target = target
            continue
        if kind == KIND_CC and dmg < threshold:
            if fallback_slot is None:
                fallback_slot = slot
                fallback_target = target
            continue
        if dmg < threshold:
            continue
        if dmg > best_dmg:
            best_dmg = dmg
            best_slot = slot
            best_target = target
            best_xy = (ex, ey) if (kind == KIND_AOE and (slot["mask"] & T_GROUND)
                                   and not (slot["mask"] & T_ENEMIES)) else None
    _p1("offensive_cast", _pf)
    if best_slot is not None:
        if best_xy is not None:
            return _cast(snap, best_slot, xy=best_xy)
        return _cast(snap, best_slot, target=best_target)
    if fallback_slot is not None:
        # The pure stun: it cancels a channel and takes a turn away.
        return _cast(snap, fallback_slot, target=fallback_target)
    return False


def cc_cast(snap, enemy, ex: int, ey: int) -> bool:
    """Cast a crowd-control spell at the priority target."""
    if enemy is None:
        return False
    if not can_cast_now(snap):
        return False
    picked = _cc_target(snap, _visible_enemies(snap))
    if picked is None:
        return False
    target, tx, ty = picked
    for slot in facts(snap)["ready_damage"]:
        if slot["kind"] != KIND_CC:
            continue
        if not (slot["mask"] & T_ENEMIES):
            continue
        if not _in_range(snap, slot, tx, ty):
            continue
        if _cast(snap, slot, target=target):
            return True
    return False


def camp_cast(snap, camp) -> bool:
    """Cast a damage spell at a camp we are already hitting.

    The camp burst is real economy: it shortens the clear, and the
    clear-time estimate (which prices the burst) then tells the truth.
    """
    if camp is None:
        return False
    if not can_cast_now(snap):
        return False
    basic = _basic_damage(snap)
    threshold = basic * float(config.ABILITY_CAST_MIN_DAMAGE_FACTOR)
    for slot in facts(snap)["ready_damage"]:
        if not (slot["mask"] & T_CAMPS):
            continue
        dmg = slot_amount(slot, camp)
        if dmg <= 0.0:
            dmg = slot_amount(slot, None)
        if dmg < threshold:
            continue
        if _cast(snap, slot, target=camp):
            return True
    return False


# ---------------------------------------------------------------------------
# The one cast of a turn (the fight path).
# ---------------------------------------------------------------------------


def combat_cast(snap, enemy, ex: int, ey: int) -> str:
    """The ONE cast of a fight turn. Returns the kind cast (or "").

    The order (one cast per turn, shared by both slots):

      1. DEFENCE - a heal or a shield when the incoming burst is real.
         Survival beats damage: the shield is compatible with walking,
         so it costs no step, and it is the difference between a trade
         and a death.
      2. OFFENCE - the damage / cc spell, when it beats our basic
         attack. A cast does not consume the attack allowance, so the
         basic attack and the spell land in the SAME turn.
      3. RESET - the attack reset, which restores the attack allowance
         so the champion can strike a SECOND time this turn. It is the
         fallback: a reset is worth one basic attack, a damage spell is
         worth at least that (and usually much more).

    The engine refuses a reset while an attack is still available, so
    the caller must have made the basic attack first.
    """
    if not can_cast_now(snap):
        return ""
    if defensive_cast(snap):
        return "defence"
    if offensive_cast(snap, enemy, ex, ey):
        return "offence"
    if attack_reset(snap):
        return "reset"
    return ""
