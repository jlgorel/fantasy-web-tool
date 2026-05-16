"""Pure-function schedule accolades for the Wrapped pipeline.

All seven take a ``WeeklyScores`` and return JSON-serializable dicts. None
hit the network. Logic is ported from ``SleeperLeagueAnalyzer`` with three
deliberate adjustments:

* All output is keyed by username (already the case) and **all numeric
  values are vanilla Python floats / ints** so ``json.dumps`` never sees a
  ``np.float64``.
* Ties are handled explicitly (the original ignored "T" results — Sleeper
  ties are rare but real).
* ``None``-vs-``False`` returns are eliminated.

Each function returns the smallest viable shape — the orchestration layer
in ``pipeline.py`` glues them into the full payload.
"""
from __future__ import annotations

from collections import defaultdict
from typing import Any, Dict, List, Tuple

from app.services.wrapped.schedule import WeeklyScores


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _calculate_mad(scores: List[float]) -> Tuple[float, float]:
    """Mean Absolute Deviation + mean. Used by the consistency accolade."""
    if not scores:
        return 0.0, 0.0
    mean = sum(scores) / len(scores)
    mad = sum(abs(s - mean) for s in scores) / len(scores)
    return round(mad, 2), round(mean, 2)


def _bisect_list(lst: List[float]) -> Tuple[List[float], List[float]]:
    """Split a list of weekly scores into roughly-equal halves. Both halves
    include the midpoint so an odd number of weeks gives the middle week
    weight in both halves — this matches the original analyzer."""
    midpoint = len(lst) // 2
    return lst[: midpoint + 1], lst[midpoint:]


# ---------------------------------------------------------------------------
# 1. Best-ball records
# ---------------------------------------------------------------------------
def calculate_weekly_best_ball_records(scores: WeeklyScores) -> Dict[str, Dict[str, int]]:
    """For each week, rank everyone by their best-ball score; everyone above
    you that week is a "loss" and everyone below is a "win". Sums across the
    season. Returns ``{username: {"wins": int, "losses": int}}``.
    """
    out: Dict[str, Dict[str, int]] = defaultdict(lambda: {"wins": 0, "losses": 0})
    for week in scores.weeks_played:
        weekly: List[Tuple[str, float]] = []
        for username, byweek in scores.user_best_ball_score_by_week.items():
            if week in byweek:
                weekly.append((username, byweek[week]))
        if len(weekly) < 2:
            continue
        # Sort ascending — index 0 is the worst score that week.
        weekly.sort(key=lambda x: x[1])
        n = len(weekly)
        for i, (username, _) in enumerate(weekly):
            losses = n - i - 1
            wins = i  # everyone strictly below you that week
            out[username]["wins"] += wins
            out[username]["losses"] += losses
    return dict(out)


# ---------------------------------------------------------------------------
# 2. Hypothetical records — "what if you had X's schedule"
# ---------------------------------------------------------------------------
def calculate_hypothetical_records(
    target_username: str, scores: WeeklyScores
) -> Dict[str, Dict[str, int]]:
    """For a given user, compute their record if they had each other user's
    schedule. Returns ``{other_username: {"wins": int, "losses": int}}``.

    Includes the user's own schedule entry (which equals their actual
    record) — useful for the matrix viz on the frontend.
    """
    target_scores = scores.user_score_by_week.get(target_username, {})
    out: Dict[str, Dict[str, int]] = {}
    weeks = scores.weeks_played
    for username, byweek in scores.opponent_score_by_week.items():
        wins = 0
        losses = 0
        for week in weeks:
            my_score = target_scores.get(week)
            their_opp_score = byweek.get(week)
            if my_score is None or their_opp_score is None:
                continue
            # Edge case: if the *target* user was the opponent in question
            # for this user, ``their_opp_score`` is the target's score,
            # which would always tie. Use the other user's actual score
            # instead so the hypothetical actually reflects a different
            # match-up.
            if my_score == their_opp_score and username in scores.user_score_by_week:
                their_opp_score = scores.user_score_by_week[username].get(week, their_opp_score)
            if my_score > their_opp_score:
                wins += 1
            elif my_score < their_opp_score:
                losses += 1
        out[username] = {"wins": wins, "losses": losses}
    return out


def calculate_each_users_best_and_worst_schedule(
    scores: WeeklyScores,
) -> Dict[str, Dict[str, Any]]:
    """For every user, find which other user's schedule gave them the most
    wins (``best``) and the fewest (``worst``)."""
    out: Dict[str, Dict[str, Any]] = {}
    for username in scores.usernames:
        hypo = calculate_hypothetical_records(username, scores)
        if not hypo:
            continue
        best_user, best_record = max(hypo.items(), key=lambda kv: kv[1]["wins"])
        worst_user, worst_record = min(hypo.items(), key=lambda kv: kv[1]["wins"])
        out[username] = {
            "best": {"vs_schedule_of": best_user, "record": best_record},
            "worst": {"vs_schedule_of": worst_user, "record": worst_record},
        }
    return out


# ---------------------------------------------------------------------------
# 3. Luckiest / unluckiest
# ---------------------------------------------------------------------------
def calculate_luckiest_and_unluckiest(scores: WeeklyScores) -> Dict[str, Any]:
    """A "lucky" win is a W with a score below the league median; an
    "unlucky" loss is an L with a score above the league median. Picks the
    users with the most of each. Ties on count are broken by username
    alphabetical order so output stays deterministic."""
    lucky_counts: Dict[str, int] = defaultdict(int)
    unlucky_counts: Dict[str, int] = defaultdict(int)

    for username, byweek in scores.user_results_by_week.items():
        for week, result in byweek.items():
            my_score = scores.user_score_by_week.get(username, {}).get(week)
            median = scores.median_scores.get(week)
            if my_score is None or median is None:
                continue
            if result == "W" and my_score < median:
                lucky_counts[username] += 1
            elif result == "L" and my_score > median:
                unlucky_counts[username] += 1

    def _pick_top(counts: Dict[str, int]) -> Dict[str, Any]:
        if not counts:
            return {"username": None, "count": 0}
        max_count = max(counts.values())
        if max_count == 0:
            return {"username": None, "count": 0}
        username = sorted(u for u, c in counts.items() if c == max_count)[0]
        return {"username": username, "count": int(max_count)}

    # ``by_user`` exposes the per-user raw counts so the all-time
    # aggregator can sum them across seasons (instead of just counting
    # how many seasons each user wore the crown).
    return {
        "luckiest": _pick_top(lucky_counts),
        "unluckiest": _pick_top(unlucky_counts),
        "by_user": {
            user: {
                "lucky_wins": int(lucky_counts.get(user, 0)),
                "unlucky_losses": int(unlucky_counts.get(user, 0)),
            }
            for user in set(lucky_counts) | set(unlucky_counts)
        },
    }


# ---------------------------------------------------------------------------
# 4. Most / least consistent (MAD)
# ---------------------------------------------------------------------------
def calculate_consistencies(scores: WeeklyScores) -> Dict[str, Any]:
    user_mad: Dict[str, Tuple[float, float]] = {
        user: _calculate_mad(list(byweek.values()))
        for user, byweek in scores.user_score_by_week.items()
        if byweek  # skip users with no weeks scored
    }
    if not user_mad:
        return {"most_consistent": None, "least_consistent": None}

    most_user = min(user_mad, key=lambda u: user_mad[u][0])
    least_user = max(user_mad, key=lambda u: user_mad[u][0])

    return {
        "most_consistent": {
            "username": most_user,
            "mad": user_mad[most_user][0],
            "mean": user_mad[most_user][1],
        },
        "least_consistent": {
            "username": least_user,
            "mad": user_mad[least_user][0],
            "mean": user_mad[least_user][1],
        },
    }


# ---------------------------------------------------------------------------
# 5. Best / worst manager (actual ÷ best-ball efficiency)
# ---------------------------------------------------------------------------
def calculate_best_and_worst_manager(scores: WeeklyScores) -> Dict[str, Any]:
    """Manager efficiency = actual_score / best_ball_score, averaged across
    weeks. Higher is better. Skips weeks where best-ball is 0 to avoid
    division by zero (e.g. bye weeks for partial-roster leagues)."""
    eff: Dict[str, float] = {}
    for user, best_ball in scores.user_best_ball_score_by_week.items():
        weekly_eff = []
        for week, bb in best_ball.items():
            if not bb:
                continue
            actual = scores.user_score_by_week.get(user, {}).get(week, 0.0)
            weekly_eff.append(actual / bb)
        if not weekly_eff:
            continue
        eff[user] = round(sum(weekly_eff) / len(weekly_eff) * 100, 2)

    if not eff:
        return {"most_efficient": None, "least_efficient": None}

    most = max(eff, key=lambda u: eff[u])
    least = min(eff, key=lambda u: eff[u])
    return {
        "most_efficient": {"username": most, "efficiency_pct": eff[most]},
        "least_efficient": {"username": least, "efficiency_pct": eff[least]},
        "by_user": {u: float(p) for u, p in eff.items()},
    }


# ---------------------------------------------------------------------------
# 6. Falloff / come-up — first half vs second half
# ---------------------------------------------------------------------------
def calculate_biggest_falloff_and_come_up(scores: WeeklyScores) -> Dict[str, Any]:
    falloffs: Dict[str, float] = {}
    comeups: Dict[str, float] = {}
    by_user: Dict[str, Dict[str, float]] = {}

    for user, byweek in scores.user_score_by_week.items():
        if len(byweek) < 4:  # not enough weeks to bisect meaningfully
            continue
        weeks_sorted = sorted(byweek.keys())
        ordered_scores = [byweek[w] for w in weeks_sorted]
        first_half, second_half = _bisect_list(ordered_scores)
        first_avg = round(sum(first_half) / len(first_half), 2)
        second_avg = round(sum(second_half) / len(second_half), 2)
        by_user[user] = {"first_half_avg": first_avg, "second_half_avg": second_avg}

        delta = round(second_avg - first_avg, 2)
        if delta > 0:
            comeups[user] = delta
        elif delta < 0:
            falloffs[user] = -delta  # store as positive magnitude

    return {
        "biggest_come_up": (
            {
                "username": max(comeups, key=lambda u: comeups[u]),
                "delta": comeups[max(comeups, key=lambda u: comeups[u])],
            }
            if comeups else None
        ),
        "biggest_falloff": (
            {
                "username": max(falloffs, key=lambda u: falloffs[u]),
                "delta": falloffs[max(falloffs, key=lambda u: falloffs[u])],
            }
            if falloffs else None
        ),
        "by_user": by_user,
    }
