"""Build the user's actual current starting lineup and compare it to optimizer
output ('your lineup' vs Boris-optimized vs Vegas-optimized).

Why this lives in its own module:
- ``lineup_optimizer.py`` is already large and laser-focused on producing
  *suggested* starts. Adding "what is the user actually starting?" + cross-
  lineup delta math there would double its responsibilities.
- The Sleeper API exposes the user's current `starters` list directly. We
  consume that list as the source of truth and feed each starter through the
  same ``_format_starter_entry`` formatter to keep the rendered shape
  byte-identical to optimizer output.

The delta annotation logic intentionally compares **rosters as sets of
starters** rather than slot-by-slot. Reason: if the optimizer just shuffles
WR2 -> WR1, that's not a material lineup change. Only a player moving from
the bench into a starting slot (and the displaced player going to the bench)
counts as an upgrade we want to highlight.
"""
from __future__ import annotations

import logging
from collections import defaultdict
from copy import copy, deepcopy
from typing import Any, Dict, Iterable, List, Optional, Tuple

from app.config import Config
from app.services.blob_store import load_blob
from app.services.boris_chen import get_tier_page_names_from_league_settings
from app.services.lineup_optimizer import (
    _annotate_qb_stacks,
    _build_team_rank_dict,
    _format_starter_entry,
    clean_up_pos_names,
)
from app.services.scoring import calculate_potential_fantasy_score

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# "Your Lineup" builder
# ---------------------------------------------------------------------------
def build_your_lineup(
    user_rosters: List[Dict[str, Any]],
    name_to_pid: Dict[str, str],
    boris_chen_tiers: Dict[str, Dict[str, str]],
) -> Dict[str, Optional[List[Dict[str, Any]]]]:
    """Convert each Sleeper roster's ``starters`` pid list into the same
    list-of-player-dicts shape that ``form_suggested_starts_based_on_boris``
    emits.

    Returns ``{league_name: [player_dicts] | None}``. ``None`` is used when
    the league lacks a ``starters`` key (Fleaflicker, IDP-skipped leagues, or
    leagues where Sleeper didn't return starter data) so the frontend can
    hide the 3-way toggle for that league specifically.
    """
    sportsbook_projections = load_blob("hand_calculated_projections.json")
    backup_projections = load_blob("backup_fantasypros_projections.json")
    fantasypros_data = load_blob("fantasypros_data.json")
    player_data = load_blob("players.json")

    # pid -> name lookup (inverse of name_to_pid). For DEF entries we fall
    # back to the team-name lookup table that ``_format_starter_entry`` also
    # consults.
    pid_to_name = {pid: name for name, pid in name_to_pid.items()}

    out: Dict[str, Optional[List[Dict[str, Any]]]] = {}

    for roster in user_rosters:
        league_name = roster["league"]
        starters_pids: Optional[List[str]] = roster.get("starters")
        if starters_pids is None:
            out[league_name] = None
            continue

        positions: List[str] = list(roster.get("positions") or [])
        bench_pids = [pid for pid in roster.get("pids") or [] if pid not in starters_pids]

        normal_prefix, te_prefix = get_tier_page_names_from_league_settings(roster["settings"])

        # We need the same team_rank_dict the optimizer builds so that
        # _format_starter_entry can stamp tiers on each row.
        position_groups: Dict[str, List[str]] = defaultdict(list)
        for pid in roster.get("pids") or []:
            pdata = player_data.get(pid)
            if pdata is None:
                continue
            try:
                pos = pdata["fantasy_positions"][0]
            except (KeyError, IndexError, TypeError):
                continue
            name = Config.nfl_teams.get(pid) or pdata.get("full_name")
            if name:
                position_groups[pos].append(name)

        starting_position_set = clean_up_pos_names(positions)
        if isinstance(starting_position_set, str):
            starting_position_set = {starting_position_set: 1}
        tiers_to_lookup: set = set()
        for pos_name in starting_position_set:
            if pos_name in ("RB", "WR", "Flex"):
                tiers_to_lookup.add(normal_prefix + pos_name)
            elif pos_name == "TE":
                tiers_to_lookup.add(te_prefix + pos_name)
            elif pos_name == "WT":
                tiers_to_lookup.add(normal_prefix + "Flex")
            else:
                tiers_to_lookup.add(pos_name)

        team_rank_dict = _build_team_rank_dict(
            position_groups, boris_chen_tiers, tiers_to_lookup, normal_prefix, te_prefix
        )

        stat_point_multipliers = Config.get_stat_point_multipliers(roster["settings"])

        rows: List[Dict[str, Any]] = []
        # Pair Sleeper's starters list with the league's roster_positions
        # list slot-by-slot. Sleeper guarantees these are aligned in order.
        # If the lengths drift (e.g. an empty slot), we fall back to BN for
        # any leftover starter pids.
        for slot_pos, pid in zip(positions, starters_pids):
            name = _resolve_player_name(pid, pid_to_name, player_data)
            if name is None:
                continue
            tiers = team_rank_dict.get(name, {})
            row = _format_starter_entry(
                slot_pos,
                {"Name": name, "Tiers": tiers},
                name_to_pid,
                player_data,
                fantasypros_data,
                sportsbook_projections,
                backup_projections,
                stat_point_multipliers,
            )
            rows.append(row)

        for pid in bench_pids:
            name = _resolve_player_name(pid, pid_to_name, player_data)
            if name is None:
                continue
            tiers = team_rank_dict.get(name, {})
            row = _format_starter_entry(
                "BN",
                {"Name": name, "Tiers": tiers or {"BN": "Unranked"}},
                name_to_pid,
                player_data,
                fantasypros_data,
                sportsbook_projections,
                backup_projections,
                stat_point_multipliers,
            )
            rows.append(row)

        _annotate_qb_stacks(rows)
        out[league_name] = rows

    return out


def _resolve_player_name(
    pid: str, pid_to_name: Dict[str, str], player_data: Dict[str, Any]
) -> Optional[str]:
    if pid in Config.nfl_teams:
        return Config.nfl_teams[pid]
    if pid in pid_to_name:
        return pid_to_name[pid]
    pdata = player_data.get(pid) or {}
    return pdata.get("full_name")


# ---------------------------------------------------------------------------
# Delta annotation
# ---------------------------------------------------------------------------
# When comparing "your lineup" to an optimized lineup, we only annotate
# starters who *moved between bench and starting* — pure intra-starter slot
# shuffles aren't material. For each newly-promoted starter we pair them
# with a demoted starter (someone who was starting in your lineup but isn't
# in the optimized version) at the same / a compatible position.

# Position eligibility for pairing. The key is the optimizer slot's
# REALLIFE_POS; the value is the set of REALLIFE_POSes a demoted player
# can come from to count as "the player they replaced". This is a superset
# of the strict positional match because a Flex swap (RB -> WR) is real.
_PAIRING_ELIGIBLE: Dict[str, Tuple[str, ...]] = {
    "QB": ("QB",),
    "RB": ("RB", "WR", "TE"),
    "WR": ("WR", "RB", "TE"),
    "TE": ("TE", "WR", "RB"),
    "K":  ("K",),
    "DEF": ("DEF",),
}


def _starter_rows(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return [r for r in rows if r.get("POS") != "BN"]


def _pid_or_name(row: Dict[str, Any]) -> str:
    return row.get("PID") or row.get("NAME") or ""


def _vegas_float(row: Dict[str, Any]) -> float:
    """VEGAS field can be 'N/A' (DEF/K) or '12.34\\t Old projection...'.
    Coerce best-effort to a float; missing -> 0.0 so deltas still compute."""
    raw = row.get("VEGAS")
    if isinstance(raw, (int, float)):
        return float(raw)
    if isinstance(raw, str):
        head = raw.split()[0] if raw.strip() else ""
        try:
            return float(head)
        except ValueError:
            return 0.0
    return 0.0


def annotate_lineup_deltas(
    optimized_lineup: List[Dict[str, Any]],
    your_lineup: List[Dict[str, Any]],
) -> None:
    """Mutate ``optimized_lineup`` rows in place to add ``DELTA_VS_YOUR_LINEUP``
    (signed float, points gained over the displaced player) and
    ``DELTA_VS_PLAYER`` (the displaced player's name).

    Only newly-promoted starters get annotations. Pairing strategy:

    1. ``promoted`` = starters in optimized but not in your lineup.
    2. ``demoted``  = starters in your lineup but not in optimized.
    3. For each promoted player (sorted by VEGAS desc — highest-impact swap
       gets first pick of the demoted pool), match against a demoted player
       whose REALLIFE_POS is in the eligibility set for the promoted player's
       slot. Among eligible candidates we pick the one with the highest VEGAS
       (the one whose loss "hurts" most), giving the biggest informative
       delta. Ties broken by name for determinism.
    """
    your_starter_pids = {_pid_or_name(r) for r in _starter_rows(your_lineup)}
    opt_starter_pids = {_pid_or_name(r) for r in _starter_rows(optimized_lineup)}

    promoted_keys = opt_starter_pids - your_starter_pids
    demoted_keys = your_starter_pids - opt_starter_pids

    if not promoted_keys or not demoted_keys:
        return

    promoted_rows = [r for r in optimized_lineup if _pid_or_name(r) in promoted_keys]
    demoted_pool: List[Dict[str, Any]] = [
        r for r in your_lineup if _pid_or_name(r) in demoted_keys
    ]

    # Highest-VEGAS promoted players choose first.
    promoted_rows.sort(key=lambda r: _vegas_float(r), reverse=True)

    used_demoted: set = set()
    for promoted in promoted_rows:
        slot_pos = promoted.get("REALLIFE_POS") or ""
        eligible = _PAIRING_ELIGIBLE.get(slot_pos, (slot_pos,))
        candidates = [
            d for d in demoted_pool
            if _pid_or_name(d) not in used_demoted
            and (d.get("REALLIFE_POS") or "") in eligible
        ]
        if not candidates:
            # Fallback: any unused demoted player. Better to show a delta
            # against something than swallow the swap silently.
            candidates = [d for d in demoted_pool if _pid_or_name(d) not in used_demoted]
        if not candidates:
            continue
        candidates.sort(key=lambda r: (-_vegas_float(r), r.get("NAME") or ""))
        partner = candidates[0]
        used_demoted.add(_pid_or_name(partner))

        delta = round(_vegas_float(promoted) - _vegas_float(partner), 2)
        promoted["DELTA_VS_YOUR_LINEUP"] = delta
        promoted["DELTA_VS_PLAYER"] = partner.get("NAME") or "bench"
