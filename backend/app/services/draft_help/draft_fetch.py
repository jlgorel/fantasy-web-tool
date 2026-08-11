"""Fetch + normalize Sleeper drafts for the Draft Help features.

Sleeper draft endpoints:
    GET /v1/league/{id}/drafts     -> [{draft_id, type, season, settings, metadata}]
    GET /v1/draft/{draft_id}       -> full draft (type, settings, slot_to_roster_id, ...)
    GET /v1/draft/{draft_id}/picks -> [{pick_no, round, draft_slot, roster_id,
                                        picked_by, player_id, metadata:{first_name,
                                        last_name, position, amount}}]

``type`` is ``"snake"``, ``"linear"`` or ``"auction"``; auction picks carry
``metadata.amount`` (the winning bid, as a string). All fetchers degrade to
``None``/``[]`` on failure (read-only feature) and the normalization helpers are
pure so they can be unit tested without the network.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from app.services.http_utils import fetch_json

_SLEEPER = "https://api.sleeper.app/v1"


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------
@dataclass
class NormalizedPick:
    pick_no: int
    round: int
    draft_slot: Optional[int]
    player_id: Optional[str]
    name: Optional[str] = None
    position: Optional[str] = None
    roster_id: Optional[int] = None
    user_id: Optional[str] = None  # picked_by
    amount: Optional[int] = None   # auction winning bid ($); None for snake

    def to_dict(self) -> Dict[str, Any]:
        return {
            "pick_no": self.pick_no,
            "round": self.round,
            "draft_slot": self.draft_slot,
            "player_id": self.player_id,
            "name": self.name,
            "position": self.position,
            "roster_id": self.roster_id,
            "user_id": self.user_id,
            "amount": self.amount,
        }


@dataclass
class NormalizedDraft:
    draft_id: str
    league_id: Optional[str]
    season: Optional[str]
    draft_type: Optional[str]  # "snake" | "linear" | "auction"
    teams: Optional[int]
    rounds: Optional[int]
    scoring_type: Optional[str]
    status: Optional[str] = None
    slot_to_roster_id: Dict[int, int] = field(default_factory=dict)
    picks: List[NormalizedPick] = field(default_factory=list)

    @property
    def is_auction(self) -> bool:
        return (self.draft_type or "").lower() == "auction"

    @property
    def is_dynasty(self) -> bool:
        """True for dynasty/rookie drafts.

        Sleeper tags these drafts with a ``dynasty_*`` ``scoring_type`` (e.g.
        ``"dynasty_2qb"``). Draft Help is redraft/keeper only, so these drafts
        are skipped wherever picks are accumulated -- a belt-and-suspenders
        guard alongside the league-level ``settings.type == 2`` check.
        """
        return "dynasty" in (self.scoring_type or "").lower()

    def to_dict(self) -> Dict[str, Any]:
        return {
            "draft_id": self.draft_id,
            "league_id": self.league_id,
            "season": self.season,
            "draft_type": self.draft_type,
            "teams": self.teams,
            "rounds": self.rounds,
            "scoring_type": self.scoring_type,
            "status": self.status,
            "slot_to_roster_id": self.slot_to_roster_id,
            "picks": [p.to_dict() for p in self.picks],
        }


# ---------------------------------------------------------------------------
# Pure normalization
# ---------------------------------------------------------------------------
def _coerce_amount(value: Any) -> Optional[int]:
    if value is None or value == "":
        return None
    try:
        return int(round(float(value)))
    except (TypeError, ValueError):
        return None


def normalize_pick(raw: Dict[str, Any]) -> NormalizedPick:
    """Project a raw Sleeper pick into :class:`NormalizedPick`."""
    meta = raw.get("metadata") or {}
    first = (meta.get("first_name") or "").strip()
    last = (meta.get("last_name") or "").strip()
    name = (f"{first} {last}").strip() or None
    return NormalizedPick(
        pick_no=int(raw.get("pick_no") or 0),
        round=int(raw.get("round") or 0),
        draft_slot=raw.get("draft_slot"),
        player_id=(str(raw["player_id"]) if raw.get("player_id") is not None else None),
        name=name,
        position=(meta.get("position") or None),
        roster_id=raw.get("roster_id"),
        user_id=(raw.get("picked_by") or None),
        amount=_coerce_amount(meta.get("amount")),
    )


def normalize_draft(detail: Dict[str, Any], picks_raw: List[Dict[str, Any]]) -> NormalizedDraft:
    """Combine a draft detail object + its picks into a :class:`NormalizedDraft`."""
    settings = detail.get("settings") or {}
    slot_map_raw = detail.get("slot_to_roster_id") or {}
    slot_map: Dict[int, int] = {}
    for k, v in slot_map_raw.items():
        try:
            slot_map[int(k)] = int(v)
        except (TypeError, ValueError):
            continue
    return NormalizedDraft(
        draft_id=str(detail.get("draft_id") or ""),
        league_id=(str(detail["league_id"]) if detail.get("league_id") else None),
        season=(str(detail["season"]) if detail.get("season") else None),
        draft_type=detail.get("type"),
        teams=settings.get("teams"),
        rounds=settings.get("rounds"),
        scoring_type=(detail.get("metadata") or {}).get("scoring_type"),
        status=detail.get("status"),
        slot_to_roster_id=slot_map,
        picks=[normalize_pick(p) for p in (picks_raw or [])],
    )


def infer_league_config(league: Dict[str, Any]) -> Dict[str, Any]:
    """Infer ``(teams, ppr, superflex)`` from a Sleeper league object.

    - ``teams``     <- ``total_rosters``
    - ``ppr``       <- ``scoring_settings.rec`` bucketed to 0 / 0.5 / 1.0
    - ``superflex`` <- a ``SUPER_FLEX`` slot or 2+ ``QB`` starter slots
    """
    teams = league.get("total_rosters") or league.get("settings", {}).get("num_teams")
    scoring = league.get("scoring_settings") or {}
    rec = scoring.get("rec", 0) or 0
    if rec < 0.25:
        ppr = 0.0
    elif rec < 0.75:
        ppr = 0.5
    else:
        ppr = 1.0
    roster_positions = league.get("roster_positions") or []
    superflex = ("SUPER_FLEX" in roster_positions) or (
        sum(1 for p in roster_positions if p == "QB") >= 2
    )
    return {"teams": teams, "ppr": ppr, "superflex": superflex}


def is_dynasty_league(league: Optional[Dict[str, Any]]) -> bool:
    """True when a Sleeper league object is a dynasty league.

    Sleeper encodes league type in ``settings.type``: ``0`` redraft, ``1``
    keeper, ``2`` dynasty. Draft Help only analyzes full snake/auction drafts,
    so dynasty leagues (rookie-only drafts) are excluded everywhere -- from the
    habit crawls, the league dropdown and the mock draft.
    """
    if not league:
        return False
    return (league.get("settings") or {}).get("type") == 2


# ---------------------------------------------------------------------------
# Thin fetchers (network; degrade to None/[])
# ---------------------------------------------------------------------------
def fetch_league_drafts(league_id: str) -> List[Dict[str, Any]]:
    return fetch_json(f"{_SLEEPER}/league/{league_id}/drafts") or []


def fetch_league(league_id: str) -> Optional[Dict[str, Any]]:
    """The league object (settings, scoring_settings, roster_positions, ...)."""
    return fetch_json(f"{_SLEEPER}/league/{league_id}")


def fetch_league_users(league_id: str) -> Dict[str, str]:
    """Map ``user_id -> display_name`` for a league (``{}`` on failure)."""
    users = fetch_json(f"{_SLEEPER}/league/{league_id}/users") or []
    out: Dict[str, str] = {}
    for u in users:
        uid = u.get("user_id")
        if uid:
            out[str(uid)] = u.get("display_name") or u.get("username") or str(uid)
    return out


def fetch_user_leagues(user_id: str, year: str) -> List[Dict[str, Any]]:
    """A user's leagues for a season, keyed by user_id (not username)."""
    return fetch_json(f"{_SLEEPER}/user/{user_id}/leagues/nfl/{year}") or []


def resolve_user_id(username: str) -> Optional[str]:
    """Resolve a Sleeper username (or user_id) to a user_id. ``None`` if absent."""
    if not username:
        return None
    user = fetch_json(f"{_SLEEPER}/user/{username}")
    if user and user.get("user_id"):
        return str(user["user_id"])
    return None


def fetch_draft_detail(draft_id: str) -> Optional[Dict[str, Any]]:
    return fetch_json(f"{_SLEEPER}/draft/{draft_id}")


def fetch_draft_picks(draft_id: str) -> List[Dict[str, Any]]:
    return fetch_json(f"{_SLEEPER}/draft/{draft_id}/picks") or []


def fetch_draft_traded_picks(draft_id: str) -> List[Dict[str, Any]]:
    return fetch_json(f"{_SLEEPER}/draft/{draft_id}/traded_picks") or []


def fetch_user_drafts(user_id: str, year: str) -> List[Dict[str, Any]]:
    return fetch_json(f"{_SLEEPER}/user/{user_id}/drafts/nfl/{year}") or []


def load_draft(draft_id: str) -> Optional[NormalizedDraft]:
    """Fetch a draft's detail + picks and normalize. ``None`` on failure."""
    detail = fetch_draft_detail(draft_id)
    if not detail:
        return None
    picks = fetch_draft_picks(draft_id)
    return normalize_draft(detail, picks)


def load_league_drafts(league_id: str) -> List[NormalizedDraft]:
    """Load + normalize every completed draft for a league.

    Most leagues have exactly one draft; keepers/dynasty startups can have
    more. Drafts still in ``pre_draft`` (no picks) are skipped.
    """
    out: List[NormalizedDraft] = []
    for d in fetch_league_drafts(league_id):
        draft_id = d.get("draft_id")
        if not draft_id:
            continue
        nd = load_draft(str(draft_id))
        if nd and nd.picks:
            out.append(nd)
    return out
