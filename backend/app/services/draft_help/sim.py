"""Monte-Carlo "probabilistic drafting" engine for snake drafts.

Inspired by ADP-based draft aids: tell it who's been drafted, and it predicts
who will still be available at each of your upcoming picks (sampling opponents
by ADP), simulates the rest of the draft many times, and scores the
projected starting lineup you'd end up with for each candidate you could take
*now*. The candidate with the best average resulting lineup wins the rec.

Pure stdlib (``random`` only -- no numpy) so it adds no backend runtime
dependency and is deterministic under a seed for testing. ADP + projections
are injected (currently the spreadsheet-derived blobs); a live ADP feed can be
swapped in later without touching this engine.
"""
from __future__ import annotations

import random
import statistics
from collections import defaultdict
from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

# Flex slot -> eligible positions. Matches the league_context flex groups.
FLEX_GROUPS: Dict[str, Tuple[str, ...]] = {
    "FLEX": ("RB", "WR", "TE"),
    "REC_FLEX": ("WR", "TE"),
    "WRRB_FLEX": ("RB", "WR"),
    "SUPER_FLEX": ("QB", "RB", "WR", "TE"),
}
_DEDICATED = ("QB", "RB", "WR", "TE")
# Positions that are only ever a "guest" in a flex. A TE dropped into the FLEX
# should compete for points like a WR/RB, not at its own (low) TE replacement
# baseline -- so only these get a points-based flex value. RB/WR (and QB in
# superflex) are the natural flex fillers and keep their own VBD in a flex so
# their scarcity value is preserved.
_FLEX_GUEST_POS = frozenset({"TE"})


@dataclass
class SimPlayer:
    player_id: str
    name: str
    pos: str
    adp: float            # lower = drafted earlier (real ADP, or overall_rank)
    adp_stdev: float = 0.0  # ADP standard deviation -> opponent draw variance
    proj: float = 0.0     # value currency: VBD (value over replacement), or raw points
    fpts: float = 0.0     # raw projected points; scores a TE guest in a FLEX slot
    # Value credited when this player fills a FLEX slot. Set (to raw points over
    # the shared flex baseline) only for flex "guests" (TE); ``None`` for the
    # natural flex positions (RB/WR/QB) and synthetic rosters without fpts, which
    # fall back to ``proj`` (their VBD) so their scarcity value is preserved.
    flex_proj: Optional[float] = None


def default_starting_slots(superflex: bool = False) -> Dict[str, int]:
    slots = {"QB": 1, "RB": 2, "WR": 2, "TE": 1, "FLEX": 1}
    if superflex:
        slots["SUPER_FLEX"] = 1
    return slots


def slots_from_roster_positions(roster_positions: Iterable[str]) -> Dict[str, int]:
    """Derive starting-lineup slot counts from Sleeper ``roster_positions``.

    Ignores bench (``BN``), IR and K/DEF (not part of the skill-value model).
    """
    counts: Dict[str, int] = defaultdict(int)
    for raw in roster_positions or []:
        pos = str(raw).upper()
        if pos in _DEDICATED or pos in FLEX_GROUPS:
            counts[pos] += 1
    return dict(counts) or default_starting_slots(False)


# ---------------------------------------------------------------------------
# Lineup scoring
# ---------------------------------------------------------------------------
def _fill_lineup(
    roster: Sequence[SimPlayer], slots: Dict[str, int]
) -> Tuple[float, Dict[str, List[Tuple[float, float]]], Dict[str, int]]:
    """Fill the optimal starting lineup; return ``(starter_points, by_pos,
    used)`` where ``by_pos[pos]`` holds that position's ``(dedicated_value,
    flex_value)`` pairs (desc) and ``used`` is how many were consumed as
    starters.

    Dedicated slots are scored by ``proj`` (VBD) -- an elite TE's scarcity value
    counts fully in the dedicated TE slot. A FLEX slot is scored by each
    candidate's ``flex_value``: the natural flex fillers (RB/WR, and QB in
    superflex) keep their ``proj`` (VBD), while a TE guest uses raw points over
    the shared flex baseline, so a second, lower-scoring TE can't out-punch a
    WR/RB in the flex just because TE's own replacement baseline is low.
    ``flex_value`` falls back to ``proj`` when ``flex_proj`` is unset.

    Greedy: dedicated positions first, then each flex slot with the best
    remaining eligible player. Optimal for the usual nested/superset flex
    structures fantasy uses.
    """
    by_pos: Dict[str, List[Tuple[float, float]]] = defaultdict(list)
    for p in roster:
        proj = p.proj or 0.0
        flex_val = p.flex_proj if p.flex_proj is not None else proj
        by_pos[p.pos].append((proj, flex_val))
    # Sort by dedicated value desc. Within a position flex_value is a monotonic
    # shift of proj (both are points minus a per-position constant), so the
    # pairs stay index-aligned whether a slot reads the dedicated or flex value.
    for v in by_pos.values():
        v.sort(key=lambda t: t[0], reverse=True)
    used: Dict[str, int] = defaultdict(int)
    total = 0.0

    for pos in _DEDICATED:
        avail = by_pos.get(pos, [])
        for _ in range(slots.get(pos, 0)):
            if used[pos] < len(avail):
                total += avail[used[pos]][0]
                used[pos] += 1

    for flex, cnt in slots.items():
        eligible = FLEX_GROUPS.get(flex)
        if not eligible:
            continue
        for _ in range(cnt):
            best_val: Optional[float] = None
            best_pos: Optional[str] = None
            for pos in eligible:
                avail = by_pos.get(pos, [])
                if used[pos] < len(avail):
                    val = avail[used[pos]][1]  # flex scored by points, not VBD
                    if best_val is None or val > best_val:
                        best_val, best_pos = val, pos
            if best_pos is not None:
                total += best_val or 0.0
                used[best_pos] += 1
    return total, by_pos, used


def lineup_value(roster: Sequence[SimPlayer], slots: Dict[str, int]) -> float:
    """Optimal projected points from a roster's **starting** lineup.

    This is the headline "VAL": only players who crack the starting lineup
    (dedicated + flex slots) count, so a roster stacked at one position but
    missing a starter elsewhere scores low -- you are rewarded for fielding a
    full, strong starting lineup across every required position, not for bench
    hoarding.
    """
    return _fill_lineup(roster, slots)[0]


# How much a non-starter (bench) player is worth relative to a starter, and how
# many bench bodies per position we bother valuing. Depth is strictly secondary:
# the best backup is worth ``DEPTH_WEIGHT`` of its projection, the next far less.
DEPTH_WEIGHT = 0.2
DEPTH_SPOTS = 2


def _startable_positions(slots: Dict[str, int]) -> set:
    """Positions that can ever crack the starting lineup under ``slots``.

    A dedicated slot makes its position startable; a flex slot makes all of its
    eligible positions startable. Depth only counts at these positions.
    """
    startable: set = set()
    for slot, cnt in slots.items():
        if cnt <= 0:
            continue
        if slot in _DEDICATED:
            startable.add(slot)
        elif slot in FLEX_GROUPS:
            startable.update(FLEX_GROUPS[slot])
    return startable


def _depth_bonus(
    by_pos: Dict[str, List[Tuple[float, float]]], used: Dict[str, int], slots: Dict[str, int],
    *, depth_weight: float = DEPTH_WEIGHT, depth_spots: int = DEPTH_SPOTS,
) -> float:
    """Discounted value of the best bench players left over after starters.

    Geometric decay (``depth_weight ** (j+1)``) so the first backup at a
    position matters a little and each subsequent one much less -- enough to
    differentiate rosters that field equally strong starters but never enough
    to outweigh an actual starting-lineup upgrade. Only counts depth at
    positions you actually start (a player you can never start is not "depth").
    Depth is valued by ``proj`` (VBD); ``by_pos`` entries are
    ``(dedicated_value, flex_value)`` pairs, so we read the dedicated value.
    """
    startable = _startable_positions(slots)
    depth = 0.0
    for pos in _DEDICATED:
        if pos not in startable:
            continue
        avail = by_pos.get(pos, [])
        start = used.get(pos, 0)
        for j in range(depth_spots):
            idx = start + j
            if idx < len(avail) and avail[idx][0] > 0:  # below-replacement depth is worthless
                depth += avail[idx][0] * (depth_weight ** (j + 1))
    return depth


def roster_value(
    roster: Sequence[SimPlayer], slots: Dict[str, int],
    *, depth_weight: float = DEPTH_WEIGHT, depth_spots: int = DEPTH_SPOTS,
) -> float:
    """Starting lineup (primary) + a small depth bonus (secondary).

    Used as the in-sim drafting *policy* target so the simulated you fills all
    starting slots first, then adds the best available depth -- "starters
    across every position, then depth", never depth over a needed starter.
    """
    total, by_pos, used = _fill_lineup(roster, slots)
    return total + _depth_bonus(by_pos, used, slots, depth_weight=depth_weight, depth_spots=depth_spots)


# ---------------------------------------------------------------------------
# Snake order
# ---------------------------------------------------------------------------
def snake_slot_for_pick(pick_no: int, teams: int) -> int:
    """1-based draft slot on the clock at overall ``pick_no`` (snake)."""
    rnd = (pick_no - 1) // teams       # 0-based round
    idx = (pick_no - 1) % teams
    return idx + 1 if rnd % 2 == 0 else teams - idx


def my_upcoming_picks(current_pick: int, teams: int, rounds: int, my_slot: int) -> List[int]:
    return [
        p for p in range(current_pick, teams * rounds + 1)
        if snake_slot_for_pick(p, teams) == my_slot
    ]


# ---------------------------------------------------------------------------
# Pick policies
# ---------------------------------------------------------------------------
def _opponent_pick(available_by_adp: List[SimPlayer], rng: random.Random, *, k: int = 40) -> SimPlayer:
    """Sample an opponent's pick from a Gaussian-ADP board.

    ``available_by_adp`` must be pre-sorted by ADP ascending. Each of the top
    ``k`` available players draws a would-be draft slot ~ ``Normal(adp,
    adp_stdev)`` and the earliest draw is taken. This reproduces realistic
    positional runs and "who slides / who gets reached for" variance, instead
    of drafting in strict value order (which is what made the old model chalk).
    """
    head = available_by_adp[:k] if len(available_by_adp) > k else available_by_adp
    best: Optional[SimPlayer] = None
    best_draw = 0.0
    for p in head:
        draw = rng.gauss(p.adp, p.adp_stdev if p.adp_stdev > 0 else 1.0)
        if best is None or draw < best_draw:
            best_draw, best = draw, p
    return best if best is not None else head[0]


def _flex_or_proj(p: SimPlayer) -> float:
    """Consideration weight: the better of a player's dedicated (VBD) and flex
    values. Ensures a flex-first value (e.g. a QB's superflex worth) doesn't
    drop a player out of the greedy shortlist even when its VBD is modest."""
    proj = p.proj or 0.0
    return max(proj, p.flex_proj) if p.flex_proj is not None else proj


def _greedy_my_pick(
    available: List[SimPlayer], roster: List[SimPlayer], slots: Dict[str, int],
    consider: int = 14,
) -> Optional[SimPlayer]:
    """Pick the available player that most improves my roster value.

    Maximizes the gain in :func:`roster_value` (starters first, then depth), so
    an empty starting slot is always filled before adding bench depth. Considers
    only the ``consider`` highest-projection available players to keep
    simulation cost bounded; ties break toward higher projection.
    """
    if not available:
        return None
    pool = sorted(available, key=_flex_or_proj, reverse=True)[:consider]
    base = roster_value(roster, slots)
    best: Optional[SimPlayer] = None
    best_gain = -1.0
    for p in pool:
        gain = roster_value(roster + [p], slots) - base
        if gain > best_gain or (gain == best_gain and best is not None and (p.proj or 0) > (best.proj or 0)):
            best_gain, best = gain, p
    return best


# ---------------------------------------------------------------------------
# Simulation
# ---------------------------------------------------------------------------
def _simulate_once(
    candidate: SimPlayer,
    available: List[SimPlayer],
    my_roster: List[SimPlayer],
    my_future: List[int],
    teams: int,
    slots: Dict[str, int],
    rng: random.Random,
) -> Tuple[float, float, List[str]]:
    """One rollout: take ``candidate`` now, then play out to my last pick.

    Returns ``(starter_points, depth_bonus, follow_pick_ids)`` where
    ``follow_pick_ids`` are the players I drafted at my *subsequent* picks in
    this rollout (drives the "likely next picks" aggregation).
    """
    avail = [p for p in available if p.player_id != candidate.player_id]
    roster = my_roster + [candidate]
    follows: List[str] = []
    if len(my_future) <= 1:
        starters, by_pos, used = _fill_lineup(roster, slots)
        return starters, _depth_bonus(by_pos, used, slots), follows

    my_pick_set = set(my_future)
    last = my_future[-1]
    avail.sort(key=lambda p: p.adp)
    for pick in range(my_future[0] + 1, last + 1):
        if not avail:
            break
        if pick in my_pick_set:
            choice = _greedy_my_pick(avail, roster, slots)
            if choice is not None:
                roster.append(choice)
                avail.remove(choice)
                follows.append(choice.player_id)
        else:
            choice = _opponent_pick(avail, rng)
            avail.remove(choice)
    starters, by_pos, used = _fill_lineup(roster, slots)
    return starters, _depth_bonus(by_pos, used, slots), follows


@dataclass
class PickRecommendation:
    player_id: str
    name: str
    pos: str
    adp: float
    proj: float
    avg_lineup: float
    sims: int
    avg_depth: float = 0.0
    likely_next: Optional[List[Dict[str, object]]] = None

    def to_dict(self) -> Dict[str, object]:
        return {
            "player_id": self.player_id,
            "name": self.name,
            "pos": self.pos,
            "adp": self.adp,
            "proj": round(self.proj, 1),
            "avg_lineup": round(self.avg_lineup, 1),
            "avg_depth": round(self.avg_depth, 1),
            "likely_next": self.likely_next or [],
            "sims": self.sims,
        }


def _modal_path(
    all_follows: Sequence[Sequence[str]],
    future: List[int],
    by_id: Dict[str, SimPlayer],
    n_next: int,
) -> List[Dict[str, object]]:
    """Most-likely *coherent* continuation of my next few picks.

    Taking the modal pick at each future slot independently is wrong: the
    modals come from different rollouts, so together they can describe an
    impossible draft (the same player twice, or three TEs in a row). Instead we
    build a single valid path -- pick the modal at the first slot, then keep
    only the rollouts that actually made that pick before choosing the next
    slot, and so on. ``pct`` is the joint fraction of rollouts that follow the
    path this far.
    """
    total = len(all_follows) or 1
    subset = list(all_follows)
    used: set = set()
    out: List[Dict[str, object]] = []
    for j in range(n_next):
        counts: Dict[str, int] = defaultdict(int)
        for f in subset:
            if j < len(f) and f[j] not in used:
                counts[f[j]] += 1
        if not counts:
            break
        pid, cnt = max(counts.items(), key=lambda kv: kv[1])
        pl = by_id.get(pid)
        out.append({
            "pick_no": future[j + 1],
            "player_id": pid,
            "name": pl.name if pl else pid,
            "pos": pl.pos if pl else "",
            "pct": round(cnt / total, 2),
        })
        used.add(pid)
        subset = [f for f in subset if j < len(f) and f[j] == pid]
    return out


def recommend_pick(
    players: Sequence[SimPlayer],
    drafted_ids: Iterable[str],
    my_roster_ids: Iterable[str],
    *,
    teams: int,
    rounds: int,
    my_slot: int,
    slots: Optional[Dict[str, int]] = None,
    current_pick: Optional[int] = None,
    n_sims: int = 150,
    top_k: int = 6,
    show_top: int = 5,
    tau: float = 3.0,
    seed: Optional[int] = None,
) -> Dict[str, object]:
    """Rank the best players to draft *now* via Monte-Carlo rollout.

    Returns ``{current_pick, candidates: [PickRecommendation...],
    recommendation: <best>}`` where each candidate's ``avg_lineup`` is the mean
    projected **starting-lineup** value across ``n_sims`` rollouts in which you
    take that player now and draft greedily thereafter while opponents draft by
    ADP. The candidate pool is the top ``top_k`` available by ADP plus the best
    available player at each startable position, so a needed position is always
    evaluated even if it has slid well down the board; only the top ``show_top``
    by value are returned, so a positional long-shot is shown only when scarcity
    actually lifts it into the best picks. Candidates are ranked by
    starting lineup first, then depth as a tiebreaker. Each candidate also
    carries ``likely_next`` -- for each of your
    next few pick *slots* (pick 16, 17, ...), the single player you most often
    end up taking *at that slot* (with the fraction of sims). This is per-slot
    on purpose: it answers "who will actually still be there at my next pick?"
    and avoids surfacing late-round fallers you only grab 8 rounds from now.
    """
    slots = slots or default_starting_slots(False)
    drafted = set(drafted_ids)
    by_id = {p.player_id: p for p in players}
    my_roster = [by_id[i] for i in my_roster_ids if i in by_id]
    available = [p for p in players if p.player_id not in drafted]
    if not available:
        return {"current_pick": current_pick, "candidates": [], "recommendation": None}

    if current_pick is None:
        current_pick = len(drafted) + 1
    future = my_upcoming_picks(current_pick, teams, rounds, my_slot)
    if not future:
        # Not my pick / draft over -> nothing to recommend.
        return {"current_pick": current_pick, "candidates": [], "recommendation": None}

    available_by_adp = sorted(available, key=lambda p: p.adp)
    candidates = list(available_by_adp[:top_k])
    # Also always evaluate, at each startable position, BOTH the highest-value
    # (VBD) player and the next one due off the board (lowest ADP). The value
    # pick is what a greedy "best player available" drafter would take, so it
    # must be in the pool or the sim can't match (let alone beat) greedy; the
    # ADP pick captures "grab him now or lose him". Either can be well down the
    # overall board yet still be the right pick.
    seen = {p.player_id for p in candidates}
    startable = _startable_positions(slots) or set(_DEDICATED)
    best_value: Dict[str, SimPlayer] = {}
    best_adp: Dict[str, SimPlayer] = {}
    for p in available_by_adp:  # ascending ADP -> first per pos is the lowest ADP
        if p.pos not in startable:
            continue
        best_adp.setdefault(p.pos, p)
        cur = best_value.get(p.pos)
        if cur is None or p.proj > cur.proj:
            best_value[p.pos] = p
    for p in list(best_value.values()) + list(best_adp.values()):
        if p.player_id not in seen:
            candidates.append(p)
            seen.add(p.player_id)
    n_next = min(3, max(0, len(future) - 1))  # how many upcoming slots to surface

    recs: List[PickRecommendation] = []
    for cand in candidates:
        rng = random.Random(seed)  # same opponent stream per candidate = fair compare
        total_lineup = 0.0
        total_depth = 0.0
        all_follows: List[List[str]] = []
        for _ in range(n_sims):
            starters, depth, follows = _simulate_once(
                cand, available, my_roster, future, teams, slots, rng)
            total_lineup += starters
            total_depth += depth
            all_follows.append(follows[:n_next])
        likely_next = _modal_path(all_follows, future, by_id, n_next)
        recs.append(PickRecommendation(
            player_id=cand.player_id, name=cand.name, pos=cand.pos,
            adp=cand.adp, proj=cand.proj or 0.0,
            avg_lineup=total_lineup / n_sims,
            avg_depth=total_depth / n_sims,
            likely_next=likely_next,
            sims=n_sims,
        ))

    # Starters first (rounded so near-ties go to depth), then depth bonus.
    recs.sort(key=lambda r: (round(r.avg_lineup, 1), r.avg_depth), reverse=True)
    return {
        "current_pick": current_pick,
        "my_upcoming_picks": future,
        "candidates": [r.to_dict() for r in recs[:show_top]],
        "recommendation": recs[0].to_dict() if recs else None,
    }


def _model_adp_stdev(adp: float) -> float:
    """Fallback ADP standard deviation when none is supplied.

    Grows with draft position (roughly matching FantasyFootballCalculator:
    ~1 early, ~5 mid, ~15 deep) so late picks are appropriately more variable.
    """
    return max(1.0, 0.1 * adp + 0.5)


def _flex_replacement_baseline(rows: Sequence[Dict[str, object]]) -> Optional[float]:
    """Points of a replacement-level FLEX filler, or ``None`` if not derivable.

    Each position's replacement baseline is recovered as ``fpts - vbd`` (a
    constant per position for above-replacement players, since VBD is points
    over that baseline). A FLEX is filled from the RB/WR/TE pool, but RB/WR are
    the deep positions that set the flex replacement level, so we take the
    deeper (higher-points) of the RB and WR baselines. Using this single
    cross-positional baseline for the flex is what stops a low-baseline position
    (TE) from being over-credited there. Returns ``None`` when fpts/vbd are
    missing, so the caller leaves flex scoring on VBD.
    """
    per_pos: Dict[str, List[float]] = defaultdict(list)
    for r in rows:
        pos = str(r.get("pos") or "").upper()
        if pos not in ("RB", "WR"):
            continue
        fpts = r.get("fpts")
        vbd = r.get("vbd")
        if fpts is None or vbd is None:
            continue
        v = float(vbd)
        if v > 0:  # above replacement -> fpts - vbd is exactly the baseline
            per_pos[pos].append(float(fpts) - v)
    bases = [statistics.median(vals) for vals in per_pos.values() if vals]
    return max(bases) if bases else None


def sim_players_from_config_players(config_players: Iterable[Dict[str, object]]) -> List[SimPlayer]:
    """Build ``SimPlayer``s from rankings-config player dicts.

    ``adp`` uses real ADP (``adp`` field, from the FantasyFootballCalculator
    blob) when present, else falls back to ``overall_rank`` (VBD order).
    ``adp_stdev`` uses the real ADP stdev when present, else a modeled value.
    ``proj`` is the **VBD** (value over replacement) when available, falling
    back to raw ``fpts`` -- VBD is the right currency for the dedicated,
    position-locked slots. ``flex_proj`` is the player's value in a *flex* slot:
    raw points over the shared flex replacement baseline, so a cross-positional
    flex is decided on who actually scores more, not on each position's own VBD
    baseline (which over-credits TEs). Separating ADP (who comes off the board,
    and when) from value is what lets the sim reproduce real positional runs
    instead of drafting in pure value order.
    """
    rows = list(config_players)
    flex_baseline = _flex_replacement_baseline(rows)
    out: List[SimPlayer] = []
    for p in rows:
        pid = p.get("player_id")
        if not pid:
            continue
        overall_rank = p.get("overall_rank")
        adp_val = p.get("adp")
        if adp_val is not None:
            adp = float(adp_val)
        elif overall_rank is not None:
            adp = float(overall_rank)
        else:
            adp = 9999.0
        stdev_val = p.get("adp_stdev")
        adp_stdev = float(stdev_val) if stdev_val is not None else _model_adp_stdev(adp)
        vbd = p.get("vbd")
        fpts_val = p.get("fpts")
        fpts = float(fpts_val) if fpts_val is not None else 0.0
        proj = float(vbd) if vbd is not None else fpts
        pos = str(p.get("pos") or "").upper()
        # Only a flex "guest" (TE) is judged at the shared RB/WR flex level; the
        # natural flex positions (RB/WR) and QB keep their own VBD so their
        # scarcity value isn't distorted. This de-inflates a 2nd TE dropped into
        # the flex (the reported over-valuation) without hurting RB/WR.
        if pos in _FLEX_GUEST_POS and fpts_val is not None and flex_baseline is not None:
            flex_proj: Optional[float] = fpts - flex_baseline
        else:
            flex_proj = None
        out.append(SimPlayer(
            player_id=str(pid),
            name=str(p.get("name") or pid),
            pos=pos,
            adp=adp,
            adp_stdev=adp_stdev,
            proj=proj,
            fpts=fpts,
            flex_proj=flex_proj,
        ))
    return out
