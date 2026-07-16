"""SCAFFOLD: build your own VBD (value-based drafting) numbers.

This module is a *starting point* for a live-season draft model that does not
depend on the historical spreadsheet blobs. Nothing here is wired into the app
yet -- it documents the recommended approach and implements the parts that are
unambiguous (replacement-level VBD + blending), while leaving the projection
sources (Vegas-implied "man games" etc.) as clearly-marked stubs.

The big idea
------------
VBD = (a player's projected fantasy points) - (the projected points of a
"replacement-level" player at the same position). A player is only worth what
they give you *over what you could get for free on waivers*, so a 300-pt QB in a
1-QB league (where the ~12th QB also scores ~240) is worth far less than a
280-pt RB (where the ~30th RB scores ~90). VBD makes positions comparable on one
axis, which is exactly what the draft sim needs as its ``adp``/value signal.

Pipeline
--------
    1. PROJECT points per player for the season (the hard part). Options:
         a. Pull projections from the YearlyRankings spreadsheets (you already
            parse these in rankings_source.py -- reuse that path).
         b. Build your own from Vegas lines (see vegas_implied_points below).
         c. Blend (a) and (b) -- usually the most robust.
    2. Compute REPLACEMENT LEVEL per position from league size + starting slots.
    3. VBD = projection - replacement projection at that position.
    4. (optional) BLEND multiple VBD sources by weight.
    5. Feed the result into sim.SimPlayer(adp=overall_rank_by_vbd, proj=points).

Only steps 2-4 are implemented; step 1b is a documented stub.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

# Positions we compute VBD for. K/DEF are intentionally excluded.
RANKED_POSITIONS: Tuple[str, ...] = ("QB", "RB", "WR", "TE")

# Roughly how flex slots split across RB/WR/TE when computing replacement level.
# A standard FLEX is mostly RB/WR; tune to taste (or derive empirically).
DEFAULT_FLEX_SHARE: Dict[str, float] = {"RB": 0.4, "WR": 0.5, "TE": 0.1}


@dataclass
class PlayerProjection:
    """One player's projected season points from a single source."""
    player_id: str
    name: str
    pos: str
    points: float
    team: Optional[str] = None


# ---------------------------------------------------------------------------
# Step 2: replacement level
# ---------------------------------------------------------------------------
def replacement_rank_by_position(
    teams: int,
    slots: Mapping[str, int],
    *,
    flex_share: Mapping[str, float] = DEFAULT_FLEX_SHARE,
) -> Dict[str, int]:
    """How many of each position are "startable" league-wide (= replacement rank).

    The replacement-level player at a position is the *first one nobody has to
    start*. With ``teams`` teams each starting ``slots[pos]`` at that position,
    plus that position's share of flex slots, the baseline is::

        replacement_rank[pos] = teams * (dedicated_starters + flex_share*flex_slots)

    e.g. 12 teams, 2 RB + 1 FLEX (40% RB): 12 * (2 + 0.4) = ~29, so RB29's
    points are the RB replacement baseline. Returns 1-based ranks (min 1).
    """
    flex_total = 0
    for slot, count in slots.items():
        # Anything that isn't a dedicated QB/RB/WR/TE slot is treated as flex.
        if slot.upper() not in RANKED_POSITIONS:
            flex_total += int(count)

    out: Dict[str, int] = {}
    for pos in RANKED_POSITIONS:
        dedicated = int(slots.get(pos, 0))
        share = flex_share.get(pos, 0.0) * flex_total
        out[pos] = max(1, round(teams * (dedicated + share)))
    return out


# ---------------------------------------------------------------------------
# Step 3: VBD from a single projection source
# ---------------------------------------------------------------------------
def vbd_from_projections(
    projections: Iterable[PlayerProjection],
    teams: int,
    slots: Mapping[str, int],
    *,
    flex_share: Mapping[str, float] = DEFAULT_FLEX_SHARE,
) -> Dict[str, float]:
    """VBD per player = projected points - replacement points at their position.

    Replacement points = the projection of the player ranked at
    ``replacement_rank_by_position`` within their position. Players below
    replacement get a (small) negative VBD, which is correct -- they're
    free.
    """
    by_pos: Dict[str, List[PlayerProjection]] = defaultdict(list)
    for pr in projections:
        if pr.pos in RANKED_POSITIONS:
            by_pos[pr.pos].append(pr)

    repl_rank = replacement_rank_by_position(teams, slots, flex_share=flex_share)
    vbd: Dict[str, float] = {}
    for pos, players in by_pos.items():
        players.sort(key=lambda p: p.points, reverse=True)
        idx = min(repl_rank[pos], len(players)) - 1
        baseline = players[idx].points if players else 0.0
        for p in players:
            vbd[p.player_id] = round(p.points - baseline, 2)
    return vbd


# ---------------------------------------------------------------------------
# Step 1b (STUB): Vegas-implied projections ("man games" / beer-sheets style)
# ---------------------------------------------------------------------------
def vegas_implied_points(
    *,
    team_totals: Mapping[str, float],
    spreads: Mapping[str, float],
    usage_shares: Mapping[str, "UsageShare"],
    scoring: "ScoringSettings",
) -> List[PlayerProjection]:
    """STUB -- derive per-player season projections from Vegas lines.

    Recommended approach (the "beer sheets" intuition, made explicit):

      1. SEASON TEAM POINTS: turn each team's Vegas win total / implied points
         per game into an expected season point total. A team implied to score
         ~24.5/game over 17 games is ~417 offensive points; convert points to
         expected TDs / FGs (e.g. ~7 pts per offensive TD drive) to get a TD +
         yardage budget per team.

      2. SPLIT THE BUDGET into passing vs rushing using the spread/total:
         favored, low-total teams run more (positive game script); big
         underdogs throw more (negative script). Use spreads[team] to nudge the
         pass/run split around a league-average baseline.

      3. ALLOCATE to players via usage_shares: target share, carry share, air
         yards / aDOT, red-zone share, and -- crucially -- expected GAMES
         PLAYED ("man games": discount for injury history / suspensions /
         committee risk). points_player = team_budget * usage_share *
         games_played_fraction, scored with `scoring`.

      4. CONVERT to fantasy points with the league's scoring settings (PPR,
         passing TD value, etc.).

    Inputs you'll need to source:
      * team_totals  -- implied points/game or season totals (Vegas).
      * spreads      -- game/season spread per team (Vegas) for script.
      * usage_shares -- your own target/carry/RZ share + games-played model.
      * scoring      -- the league's scoring settings.

    Return a list[PlayerProjection]. Until built, this raises so callers don't
    silently use empty data.
    """
    raise NotImplementedError(
        "vegas_implied_points is a scaffold. Implement the team-budget -> "
        "usage-share -> games-played allocation described in the docstring."
    )


@dataclass
class UsageShare:
    """Per-player usage inputs for the Vegas projection model (step 1b)."""
    player_id: str
    name: str
    pos: str
    team: str
    target_share: float = 0.0     # fraction of team targets
    carry_share: float = 0.0      # fraction of team carries
    rz_share: float = 0.0         # fraction of red-zone touches
    games_played: float = 17.0    # expected "man games" (injury-adjusted)


@dataclass
class ScoringSettings:
    """Minimal scoring knobs needed to turn yards/TDs into fantasy points."""
    ppr: float = 0.5
    pass_td: float = 4.0
    pass_yd: float = 0.04         # 1 pt / 25 yds
    rush_rec_td: float = 6.0
    rush_rec_yd: float = 0.1      # 1 pt / 10 yds


# ---------------------------------------------------------------------------
# Step 4: blend multiple VBD sources
# ---------------------------------------------------------------------------
def blend_vbd(
    sources: Sequence[Mapping[str, float]],
    weights: Optional[Sequence[float]] = None,
) -> Dict[str, float]:
    """Weighted average of several ``{player_id: vbd}`` maps.

    Missing players in a source are skipped (averaged only over the sources
    that have them), so you can blend a spreadsheet VBD with a Vegas VBD even
    when their player universes differ slightly. Example:
    ``blend_vbd([sheet_vbd, vegas_vbd], weights=[0.6, 0.4])``.
    """
    if not sources:
        return {}
    if weights is None:
        weights = [1.0] * len(sources)
    if len(weights) != len(sources):
        raise ValueError("weights must match sources length")

    acc: Dict[str, float] = defaultdict(float)
    wsum: Dict[str, float] = defaultdict(float)
    for src, w in zip(sources, weights):
        for pid, v in src.items():
            acc[pid] += v * w
            wsum[pid] += w
    return {pid: round(acc[pid] / wsum[pid], 2) for pid in acc if wsum[pid]}


# ---------------------------------------------------------------------------
# Step 5: hand off to the sim
# ---------------------------------------------------------------------------
def overall_ranks_from_vbd(vbd: Mapping[str, float]) -> Dict[str, int]:
    """Rank players 1..N by descending VBD -> use as the sim's ``adp`` input.

    (The Monte-Carlo sim treats ``adp`` as "draft order"; ranking by your own
    VBD makes opponents in the sim draft to *your* board, which is a reasonable
    default until you wire in a real ADP feed.)
    """
    ordered = sorted(vbd.items(), key=lambda kv: kv[1], reverse=True)
    return {pid: i for i, (pid, _) in enumerate(ordered, start=1)}
