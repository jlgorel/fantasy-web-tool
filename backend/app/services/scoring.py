"""Fantasy point projection from Vegas alt-line projections + scoring multipliers.

Behavior here is preserved bit-for-bit from the original
``calculate_potential_fantasy_score`` so we can refactor the rest of the
pipeline without disturbing displayed point values.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional, Tuple

logger = logging.getLogger(__name__)

# Keys in projection blobs that aren't true stat lines and should not be scored.
_NON_STAT_KEYS = ("Opponent Rating", "Team Name", "Simulations")


def _player_key(name: str) -> str:
    """Normalize a display name into the alphanumeric-lower key used in
    projections blobs (e.g. ``"Ja'Marr Chase"`` -> ``"jamarrchase"``)."""
    return "".join(ch for ch in name if ch.isalnum()).lower()


def _select_simulation_distribution(
    simulations: Dict[str, Any], rec_points: float, six_point_td: bool
) -> Optional[Dict[str, Any]]:
    """Pick the right boom/bust distribution for the league's scoring rules."""
    if "error" in simulations:
        return None
    if "QB_6PT" in simulations or "QB_STD" in simulations:
        return simulations["QB_6PT" if six_point_td else "QB_STD"]
    if rec_points < 0.3:
        return simulations.get("STD")
    if rec_points < 0.75:
        return simulations.get("HalfPPR")
    return simulations.get("PPR")


def calculate_potential_fantasy_score(
    player: str,
    pos_group: str,
    player_stat_projections: Dict[str, Any],
    backup_stat_projections: Dict[str, Any],
    stat_point_multipliers: Dict[str, float],
) -> Tuple[float, bool, str, Optional[Dict[str, Any]]]:
    """Compute projected fantasy score for a player.

    Returns ``(projected_points, used_old_projection, statline_str, boom_bust)``.

    - Primary projection source is ``player_stat_projections`` (Vegas).
    - Any stat key present in the backup but missing from the primary is added on
      top, matching legacy behavior.
    """
    rec_points = (
        stat_point_multipliers["TE Receptions"]
        if pos_group == "TE"
        else stat_point_multipliers["Receptions"]
    )

    key = _player_key(player)
    p_projections = player_stat_projections.get(key, {})
    backup = backup_stat_projections.get(key, {})

    statline = ", ".join(
        f"{k}: {round(v, 2)}"
        for k, v in p_projections.items()
        if k not in _NON_STAT_KEYS
    )

    if not p_projections and not backup:
        logger.info("Didnt find %s in standard or backup projections", player)
        return 0, False, "No stats projected for player.", None

    six_point_td = stat_point_multipliers["Passing Touchdowns"] > 4

    proj_points = 0.0
    boom_bust_probabilities: Optional[Dict[str, Any]] = None

    for stat_key, val in p_projections.items():
        if stat_key in ("Opponent Rating", "Team Name"):
            continue
        if stat_key == "Simulations":
            boom_bust_probabilities = _select_simulation_distribution(
                val, rec_points, six_point_td
            )
            continue
        if stat_key == "Receptions":
            proj_points += float(val) * rec_points
        else:
            proj_points += float(val) * stat_point_multipliers[stat_key]

    try:
        missing_projections = [k for k in backup if k not in p_projections]
        if missing_projections:
            logger.info(
                "Backup projections loaded. The projections missing from primary were %s",
                ", ".join(missing_projections),
            )
        for stat_key in missing_projections:
            if stat_key in ("Opponent Rating", "Team Name"):
                continue
            if stat_key == "Receptions":
                proj_points += float(backup[stat_key]) * rec_points
            else:
                proj_points += float(backup[stat_key]) * stat_point_multipliers[stat_key]
    except Exception as e:  # pragma: no cover - defensive
        logger.info("Exception was %s", e)

    return proj_points, False, statline, boom_bust_probabilities
