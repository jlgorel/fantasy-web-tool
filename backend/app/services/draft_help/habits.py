"""Draft-habit analytics (pure functions over normalized drafts).

Implements the snake + auction tendencies surfaced on the Draft Help tab:

Snake
    * position-by-round distribution (e.g. "RB-heavy round 1", zero-RB)
    * positional runs / when the Nth QB|TE leaves the board
    * reach-vs-value relative to the spreadsheet's expected overall rank

Auction
    * spend distribution by position (stars-and-scrubs vs balanced)
    * max bid / % of budget on the top buy
    * market inflation curve + "the WR market crashes after N WRs" detection

Cross-draft
    * favorite / repeat players a manager keeps drafting

Everything here is pure over its inputs (``NormalizedDraft`` objects plus an
optional ``{player_id: RankingPlayer}`` value map), so it is unit-testable with
hand-built fixtures and free of any network/Excel dependency.
"""
from __future__ import annotations

import bisect
from collections import defaultdict
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from app.services.draft_help.draft_fetch import NormalizedDraft, NormalizedPick
from app.services.draft_help.rankings_source import RANKED_POSITIONS, RankingPlayer

_RANKED = set(RANKED_POSITIONS)


# ---------------------------------------------------------------------------
# Pick selection helpers
# ---------------------------------------------------------------------------
def skill_picks(picks: Iterable[NormalizedPick]) -> List[NormalizedPick]:
    """Picks at a ranked skill position (QB/RB/WR/TE) with a player id."""
    return [
        p for p in picks
        if p.player_id and (p.position or "").upper() in _RANKED
    ]


def picks_for_user(draft: NormalizedDraft, user_id: str) -> List[NormalizedPick]:
    return [p for p in draft.picks if p.user_id == user_id]


# ---------------------------------------------------------------------------
# Snake: position-by-round
# ---------------------------------------------------------------------------
def position_by_round(picks: Iterable[NormalizedPick]) -> Dict[int, Dict[str, int]]:
    """Count picks per ``{round: {position: count}}`` (ranked positions)."""
    out: Dict[int, Dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for p in skill_picks(picks):
        out[p.round][(p.position or "").upper()] += 1
    return {rnd: dict(counts) for rnd, counts in sorted(out.items())}


def first_n_round_position_mix(
    picks: Iterable[NormalizedPick], n_rounds: int = 3
) -> Dict[str, int]:
    """Position counts within the first ``n_rounds`` (early-draft archetype)."""
    mix: Dict[str, int] = defaultdict(int)
    for p in skill_picks(picks):
        if p.round and p.round <= n_rounds:
            mix[(p.position or "").upper()] += 1
    return dict(mix)


def draft_archetype(picks: Iterable[NormalizedPick]) -> str:
    """Label an early-round strategy from the first 3 rounds.

    ``zero_rb`` (no RB in first 3), ``hero_rb`` (exactly one RB),
    ``rb_heavy`` (3+ RB), ``balanced`` otherwise.
    """
    mix = first_n_round_position_mix(picks, 3)
    rb = mix.get("RB", 0)
    total = sum(mix.values())
    if total == 0:
        return "unknown"
    if rb == 0:
        return "zero_rb"
    if rb == 1:
        return "hero_rb"
    if rb >= 3:
        return "rb_heavy"
    return "balanced"


# ---------------------------------------------------------------------------
# Snake: positional runs
# ---------------------------------------------------------------------------
def position_off_board(picks: Iterable[NormalizedPick]) -> Dict[str, List[int]]:
    """For each position, the ascending ``pick_no`` at which each successive
    player at that position was drafted. ``["QB"][0]`` is the first QB off
    the board, ``[4]`` the fifth, etc."""
    seq: Dict[str, List[int]] = defaultdict(list)
    for p in sorted(skill_picks(picks), key=lambda x: x.pick_no):
        seq[(p.position or "").upper()].append(p.pick_no)
    return {pos: nums for pos, nums in seq.items()}


def nth_off_board(picks: Iterable[NormalizedPick], position: str, n: int) -> Optional[int]:
    """``pick_no`` of the Nth (1-based) player taken at ``position``."""
    board = position_off_board(picks).get(position.upper(), [])
    return board[n - 1] if 0 < n <= len(board) else None


def detect_runs(
    picks: Iterable[NormalizedPick], position: str, gap: int = 4, min_len: int = 3
) -> List[Dict[str, int]]:
    """Detect "runs": clusters of a position taken within ``gap`` picks of
    each other. Returns ``[{start_pick, end_pick, count}]`` for clusters of
    at least ``min_len``. Captures "TEs/QBs go in runs after the top dogs".
    """
    board = position_off_board(picks).get(position.upper(), [])
    runs: List[Dict[str, int]] = []
    if not board:
        return runs
    start = prev = board[0]
    count = 1
    for pick_no in board[1:]:
        if pick_no - prev <= gap:
            count += 1
        else:
            if count >= min_len:
                runs.append({"start_pick": start, "end_pick": prev, "count": count})
            start = pick_no
            count = 1
        prev = pick_no
    if count >= min_len:
        runs.append({"start_pick": start, "end_pick": prev, "count": count})
    return runs


# ---------------------------------------------------------------------------
# Reach vs value (snake) -- sized by VBD
# ---------------------------------------------------------------------------
# Reaches/steals only matter while the board is steep. Past this pick the VBD
# curve is flat, so a "reach" there is noise -- we stop scoring entirely.
REACH_PICK_CUTOFF = 70


def _vbd_by_rank(value_by_pid: Dict[str, RankingPlayer]) -> List[Tuple[int, float]]:
    """Ascending ``[(overall_rank, vbd)]`` for players that have both."""
    pairs = [
        (rp.overall_rank, rp.vbd)
        for rp in value_by_pid.values()
        if rp.overall_rank is not None and rp.vbd is not None
    ]
    pairs.sort(key=lambda t: t[0])
    return pairs


def _par_vbd_fn(value_by_pid: Dict[str, RankingPlayer]):
    """Return ``par(pick_no) -> expected VBD`` for the player who *should* be
    taken at that slot (the board's VBD at ``overall_rank == pick_no``).

    Uses the nearest available rank when the board is sparse; the returned
    function only yields ``None`` when there is no VBD data at all.
    """
    pairs = _vbd_by_rank(value_by_pid)
    if not pairs:
        return lambda _pick_no: None
    ranks = [r for r, _ in pairs]
    vbds = [v for _, v in pairs]

    def par(pick_no: int) -> Optional[float]:
        i = bisect.bisect_left(ranks, pick_no)
        if i <= 0:
            return vbds[0]
        if i >= len(ranks):
            return vbds[-1]
        before, after = ranks[i - 1], ranks[i]
        return vbds[i - 1] if (pick_no - before) <= (after - pick_no) else vbds[i]

    return par


def reach_value_entries(
    picks: Iterable[NormalizedPick],
    value_by_pid: Dict[str, RankingPlayer],
    *,
    pick_cutoff: int = REACH_PICK_CUTOFF,
) -> List[Dict[str, Any]]:
    """Per-pick reach/steal entries sized by **VBD** (snake only).

    For each early pick (``pick_no <= pick_cutoff``) we compare the drafted
    player's VBD to the VBD the board offered at that slot -- ``par_vbd`` = the
    VBD of the player ranked at ``pick_no``::

        vbd_delta = player_vbd - par_vbd

    Positive = a steal (more value than the slot warranted, e.g. an elite who
    fell); negative = a reach (VBD left on the board). Because the VBD curve is
    steep early and flat late, the same rank gap costs far more at pick 1 than
    at pick 50 automatically, and picks past ``pick_cutoff`` are ignored. Each
    entry: ``{player_id, name, position, pick_no, expected_overall_rank,
    player_vbd, par_vbd, vbd_delta}``.
    """
    par = _par_vbd_fn(value_by_pid)
    entries: List[Dict[str, Any]] = []
    for p in skill_picks(picks):
        if not p.pick_no or p.pick_no > pick_cutoff:
            continue
        rp = value_by_pid.get(p.player_id or "")
        if not rp or rp.vbd is None:
            continue
        par_vbd = par(p.pick_no)
        if par_vbd is None:
            continue
        entries.append({
            "player_id": p.player_id,
            "name": p.name or rp.name,
            "position": (p.position or "").upper(),
            "pick_no": p.pick_no,
            "expected_overall_rank": rp.overall_rank,
            "player_vbd": round(float(rp.vbd), 1),
            "par_vbd": round(float(par_vbd), 1),
            "vbd_delta": round(float(rp.vbd - par_vbd), 1),
        })
    return entries


def summarize_reach_entries(entries: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    """Aggregate VBD reach entries (possibly spanning multiple drafts)."""
    if not entries:
        return {"picks_evaluated": 0}
    by_pos: Dict[str, List[float]] = defaultdict(list)
    for e in entries:
        by_pos[e["position"]].append(e["vbd_delta"])
    deltas = [e["vbd_delta"] for e in entries]
    return {
        "picks_evaluated": len(deltas),
        "avg_vbd_delta": round(sum(deltas) / len(deltas), 1),
        "avg_by_position": {
            pos: round(sum(v) / len(v), 1) for pos, v in sorted(by_pos.items())
        },
        "biggest_reach": min(entries, key=lambda e: e["vbd_delta"]),
        "biggest_value": max(entries, key=lambda e: e["vbd_delta"]),
    }


def reach_summary(
    picks: Iterable[NormalizedPick],
    value_by_pid: Dict[str, RankingPlayer],
) -> Dict[str, Any]:
    """VBD reach/steal summary for one or more drafts' picks.

    ``vbd_delta = player_vbd - par_vbd`` (positive = steal, negative = reach),
    scored only for picks inside :data:`REACH_PICK_CUTOFF`. Reports the average
    VBD delta overall and per position plus the single biggest steal/reach.
    """
    return summarize_reach_entries(reach_value_entries(picks, value_by_pid))


# ---------------------------------------------------------------------------
# Auction: spend
# ---------------------------------------------------------------------------
def auction_spend_by_position(picks: Iterable[NormalizedPick]) -> Dict[str, int]:
    """Total auction $ spent per ranked position for the given picks."""
    out: Dict[str, int] = defaultdict(int)
    for p in skill_picks(picks):
        if p.amount:
            out[(p.position or "").upper()] += p.amount
    return dict(out)


def auction_spend_summary(
    picks: Sequence[NormalizedPick], budget: int = 200
) -> Dict[str, Any]:
    """Spend distribution + stars-and-scrubs indicators for one manager.

    Returns total/positional spend, the share each position took, the single
    max bid and its budget share, and a ``stars_and_scrubs`` index = the
    fraction of total spend concentrated in the manager's two priciest buys.
    """
    sp = skill_picks(picks)
    spends = [p.amount for p in sp if p.amount]
    by_pos = auction_spend_by_position(sp)
    total = sum(spends)
    top_two = sorted(spends, reverse=True)[:2]
    max_bid = max(spends) if spends else 0
    return {
        "total_spent": total,
        "budget": budget,
        "by_position": by_pos,
        "share_by_position": {
            pos: round(amt / total, 3) for pos, amt in by_pos.items()
        } if total else {},
        "max_bid": max_bid,
        "max_bid_pct_budget": round(max_bid / budget, 3) if budget else 0.0,
        "stars_and_scrubs_index": round(sum(top_two) / total, 3) if total else 0.0,
        "players_bought": len(spends),
    }


# ---------------------------------------------------------------------------
# Auction: market inflation + crash detection
# ---------------------------------------------------------------------------
def auction_inflation_curve(
    picks: Iterable[NormalizedPick],
    value_by_pid: Dict[str, RankingPlayer],
    position: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Per-buy actual-vs-expected $ in nomination order.

    Each entry: ``{order, pick_no, player_id, name, position, expected,
    actual, inflation_pct}``. ``inflation_pct`` is ``(actual/expected - 1) *
    100`` (only when expected > 0). Optionally restricted to one position.
    """
    rows: List[Dict[str, Any]] = []
    order = 0
    for p in sorted(skill_picks(picks), key=lambda x: x.pick_no):
        pos = (p.position or "").upper()
        if position and pos != position.upper():
            continue
        if not p.amount:
            continue
        rp = value_by_pid.get(p.player_id or "")
        expected = rp.auction if rp else None
        order += 1
        inflation = None
        if expected and expected > 0:
            inflation = round((p.amount / expected - 1.0) * 100.0, 1)
        rows.append({
            "order": order,
            "pick_no": p.pick_no,
            "player_id": p.player_id,
            "name": p.name or (rp.name if rp else None),
            "position": pos,
            "expected": round(expected, 2) if expected is not None else None,
            "actual": p.amount,
            "inflation_pct": inflation,
        })
    return rows


def detect_market_crash(
    picks: Iterable[NormalizedPick],
    value_by_pid: Dict[str, RankingPlayer],
    position: str,
    window: int = 3,
    min_before: int = 3,
) -> Optional[Dict[str, Any]]:
    """Find where a position's market "crashes" below expected price.

    Walks the position's buys in order; the crash point is the first buy
    (after ``min_before`` buys) where the trailing ``window`` average
    inflation turns negative while the leading average was positive.
    Returns ``{crash_after, crash_pick_no, avg_inflation_before,
    avg_inflation_after}`` or ``None`` when no clear crash is seen.
    """
    curve = [r for r in auction_inflation_curve(picks, value_by_pid, position)
             if r["inflation_pct"] is not None]
    if len(curve) < min_before + window:
        return None
    infl = [r["inflation_pct"] for r in curve]
    for i in range(min_before, len(curve) - window + 1):
        before = infl[:i]
        after = infl[i:i + window]
        avg_before = sum(before) / len(before)
        avg_after = sum(after) / len(after)
        if avg_before > 0 and avg_after < 0:
            return {
                "crash_after": i,  # number of buys before the crash
                "crash_pick_no": curve[i]["pick_no"],
                "avg_inflation_before": round(avg_before, 1),
                "avg_inflation_after": round(avg_after, 1),
            }
    return None


# Size of the "elite" pool (priciest players by value) for the hot/cold read.
ELITE_POOL = 16


def market_status(
    picks: Iterable[NormalizedPick],
    value_by_pid: Dict[str, RankingPlayer],
    position: str,
    *,
    window: int = 3,
    min_before: int = 3,
    crash_drop: float = 25.0,
    min_expected: float = 5.0,
) -> Dict[str, Any]:
    """Always-returns auction read for one position's market.

    Walks the position's *meaningful* buys (expected value ``>= min_expected``,
    so $1 deep bench fliers don't swamp the percentages) in nomination order
    and reports early-vs-late inflation plus whether/where the market "crashes"
    -- the biggest sustained drop (``>= crash_drop`` points) into below-expected
    pricing. Unlike :func:`detect_market_crash` this never returns ``None``, so
    a caller can always surface a verdict (e.g. for the WR market). Fields:
    ``position, buys_analyzed, crashed, crash_after, crash_pick_no,
    avg_inflation_before, avg_inflation_after, early_inflation,
    late_inflation``.
    """
    curve = [r for r in auction_inflation_curve(picks, value_by_pid, position)
             if r["inflation_pct"] is not None and (r["expected"] or 0) >= min_expected]
    status: Dict[str, Any] = {
        "position": position.upper(),
        "buys_analyzed": len(curve),
        "crashed": False,
        "crash_after": None,
        "crash_pick_no": None,
        "avg_inflation_before": None,
        "avg_inflation_after": None,
        "early_inflation": None,
        "late_inflation": None,
    }
    if not curve:
        return status
    infl = [r["inflation_pct"] for r in curve]
    third = max(1, len(infl) // 3)
    status["early_inflation"] = round(sum(infl[:third]) / third, 1)
    status["late_inflation"] = round(sum(infl[-third:]) / third, 1)
    if len(curve) >= min_before + window:
        # Earliest "was hot, then cratered" flip -> the actionable crash point.
        for i in range(min_before, len(curve) - window + 1):
            avg_before = sum(infl[:i]) / i
            avg_after = sum(infl[i:i + window]) / window
            if avg_before > 0 and avg_after < 0 and (avg_before - avg_after) >= crash_drop:
                status.update({
                    "crashed": True,
                    "crash_after": i,
                    "crash_pick_no": curve[i]["pick_no"],
                    "avg_inflation_before": round(avg_before, 1),
                    "avg_inflation_after": round(avg_after, 1),
                })
                break
    return status


def elite_market_curve(
    picks: Iterable[NormalizedPick],
    value_by_pid: Dict[str, RankingPlayer],
    *,
    pool: int = ELITE_POOL,
    margin: float = 15.0,
) -> Optional[Dict[str, Any]]:
    """Hot- vs cold-start read on the elite (priciest by value) buys.

    Takes the ``pool`` most expensive players by expected auction $, orders
    them by *when* they were bought, and compares the first half's inflation to
    the second half's:

      * ``hot_start``  -- elites overpaid early then cool off (wait for a stud)
      * ``cold_start`` -- elites are steals early then prices climb (pounce early)
      * ``flat``       -- no meaningful tilt

    Returns ``None`` when there are too few priced elite buys to judge.
    """
    rows = [r for r in auction_inflation_curve(picks, value_by_pid)
            if r["inflation_pct"] is not None and r.get("expected")]
    if len(rows) < 4:
        return None
    elites = sorted(rows, key=lambda r: r["expected"], reverse=True)[:pool]
    if len(elites) < 4:
        return None
    by_order = sorted(elites, key=lambda r: r["pick_no"])
    half = len(by_order) // 2
    early, late = by_order[:half], by_order[half:]
    early_infl = sum(r["inflation_pct"] for r in early) / len(early)
    late_infl = sum(r["inflation_pct"] for r in late) / len(late)
    diff = early_infl - late_infl
    if diff >= margin:
        pattern = "hot_start"
    elif -diff >= margin:
        pattern = "cold_start"
    else:
        pattern = "flat"
    return {
        "elite_count": len(elites),
        "early_inflation": round(early_infl, 1),
        "late_inflation": round(late_infl, 1),
        "diff": round(diff, 1),
        "pattern": pattern,
    }


# ---------------------------------------------------------------------------
# Cross-draft: favorites
# ---------------------------------------------------------------------------
def favorite_players(
    pick_lists: Iterable[Sequence[NormalizedPick]],
    min_count: int = 2,
) -> List[Dict[str, Any]]:
    """Players a manager drafted in ``>= min_count`` separate drafts.

    ``pick_lists`` is one list of (already user-filtered) picks per draft.
    Returns ``[{player_id, name, position, count}]`` sorted by count desc.
    """
    counts: Dict[str, int] = defaultdict(int)
    meta: Dict[str, Dict[str, Any]] = {}
    for picks in pick_lists:
        seen: set = set()
        for p in skill_picks(picks):
            if not p.player_id or p.player_id in seen:
                continue
            seen.add(p.player_id)
            counts[p.player_id] += 1
            meta.setdefault(p.player_id, {
                "player_id": p.player_id,
                "name": p.name,
                "position": (p.position or "").upper(),
            })
    favs = [{**meta[pid], "count": c} for pid, c in counts.items() if c >= min_count]
    favs.sort(key=lambda d: (-d["count"], d["name"] or ""))
    return favs


# ---------------------------------------------------------------------------
# Summarizers
# ---------------------------------------------------------------------------
def summarize_snake(
    pick_lists: Sequence[Sequence[NormalizedPick]],
    value_by_pid: Dict[str, RankingPlayer],
) -> Dict[str, Any]:
    """Aggregate snake habits across one or more drafts' (filtered) picks."""
    flat: List[NormalizedPick] = [p for picks in pick_lists for p in picks]
    return {
        "draft_type": "snake",
        "drafts_counted": len(pick_lists),
        "position_by_round": position_by_round(flat),
        "early_round_mix": first_n_round_position_mix(flat, 3),
        "archetypes": _archetype_counts(pick_lists),
        "reach": reach_summary(flat, value_by_pid),
    }


def summarize_auction(
    pick_lists: Sequence[Sequence[NormalizedPick]],
    value_by_pid: Dict[str, RankingPlayer],
    budget: int = 200,
) -> Dict[str, Any]:
    """Aggregate auction habits across one or more drafts' (filtered) picks."""
    flat: List[NormalizedPick] = [p for picks in pick_lists for p in picks]
    spend_summaries = [auction_spend_summary(picks, budget) for picks in pick_lists if picks]
    return {
        "draft_type": "auction",
        "drafts_counted": len(pick_lists),
        "avg_spend_by_position": _avg_spend_by_position(spend_summaries),
        "avg_stars_and_scrubs_index": _avg_metric(spend_summaries, "stars_and_scrubs_index"),
        "avg_max_bid_pct_budget": _avg_metric(spend_summaries, "max_bid_pct_budget"),
        "inflation_curve": auction_inflation_curve(flat, value_by_pid),
    }


def _archetype_counts(pick_lists: Iterable[Sequence[NormalizedPick]]) -> Dict[str, int]:
    counts: Dict[str, int] = defaultdict(int)
    for picks in pick_lists:
        counts[draft_archetype(picks)] += 1
    return dict(counts)


def _avg_spend_by_position(summaries: Sequence[Dict[str, Any]]) -> Dict[str, float]:
    totals: Dict[str, float] = defaultdict(float)
    for s in summaries:
        for pos, amt in (s.get("by_position") or {}).items():
            totals[pos] += amt
    n = len(summaries) or 1
    return {pos: round(amt / n, 1) for pos, amt in totals.items()}


def _avg_metric(summaries: Sequence[Dict[str, Any]], key: str) -> float:
    vals = [s.get(key, 0.0) for s in summaries]
    return round(sum(vals) / len(vals), 3) if vals else 0.0
