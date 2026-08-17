"""Pure normalization for a read-only Sleeper live draft assistant."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence

from app.services.draft_help.sim import snake_slot_for_pick

SUPPORTED_ACTIVE_STATUSES = {"drafting", "paused", "pre_draft", "complete"}


class LiveDraftError(ValueError):
    """A user-facing invalid/unsupported live draft."""


@dataclass(frozen=True)
class LiveDraftConfig:
    teams: int
    rounds: int
    bench_size: int
    ppr: float
    superflex: bool
    slots: Dict[str, int]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "teams": self.teams,
            "rounds": self.rounds,
            "bench_size": self.bench_size,
            "ppr": self.ppr,
            "superflex": self.superflex,
            "slots": self.slots,
        }


def infer_draft_config(detail: Mapping[str, Any]) -> LiveDraftConfig:
    settings = detail.get("settings") or {}
    teams = int(settings.get("teams") or 0)
    rounds = int(settings.get("rounds") or 0)
    bench_size = max(0, int(settings.get("slots_bn") or 0))
    if teams < 2 or rounds < 1:
        raise LiveDraftError("Draft is missing a valid team count or round count.")

    scoring_type = str((detail.get("metadata") or {}).get("scoring_type") or "").lower()
    if "half" in scoring_type:
        ppr = 0.5
    elif "ppr" in scoring_type:
        ppr = 1.0
    else:
        ppr = 0.0

    key_to_slot = {
        "slots_qb": "QB",
        "slots_rb": "RB",
        "slots_wr": "WR",
        "slots_te": "TE",
        "slots_flex": "FLEX",
        "slots_rec_flex": "REC_FLEX",
        "slots_wr_rb_flex": "WRRB_FLEX",
        "slots_super_flex": "SUPER_FLEX",
    }
    slots: Dict[str, int] = {}
    for source, target in key_to_slot.items():
        count = int(settings.get(source) or 0)
        if count > 0:
            slots[target] = count
    superflex = (
        slots.get("SUPER_FLEX", 0) > 0
        or slots.get("QB", 0) >= 2
        or "2qb" in scoring_type
        or "superflex" in scoring_type
    )
    if superflex and not slots.get("SUPER_FLEX") and slots.get("QB", 0) < 2:
        slots["SUPER_FLEX"] = 1
    return LiveDraftConfig(teams, rounds, bench_size, ppr, superflex, slots)


def validate_supported_draft(detail: Mapping[str, Any]) -> LiveDraftConfig:
    draft_type = str(detail.get("type") or "").lower()
    status = str(detail.get("status") or "").lower()
    scoring_type = str((detail.get("metadata") or {}).get("scoring_type") or "").lower()
    if draft_type != "snake":
        raise LiveDraftError("Live Draft currently supports Sleeper snake drafts only.")
    if "dynasty" in scoring_type or detail.get("season_type") == "rookie":
        raise LiveDraftError("Dynasty startup and rookie drafts are not supported yet.")
    if status and status not in SUPPORTED_ACTIVE_STATUSES:
        raise LiveDraftError(f"Unsupported Sleeper draft status: {status}.")
    return infer_draft_config(detail)


def resolve_user_slot(
    detail: Mapping[str, Any],
    *,
    user_id: Optional[str] = None,
    selected_slot: Optional[int] = None,
) -> Optional[int]:
    config = infer_draft_config(detail)
    if user_id:
        raw = (detail.get("draft_order") or {}).get(str(user_id))
        if raw is not None:
            try:
                slot = int(raw)
                if 1 <= slot <= config.teams:
                    return slot
            except (TypeError, ValueError):
                pass
    if selected_slot is not None:
        slot = int(selected_slot)
        if not 1 <= slot <= config.teams:
            raise LiveDraftError(f"Draft slot must be between 1 and {config.teams}.")
        return slot
    return None


def _first_open_pick(picks: Sequence[Mapping[str, Any]], total_picks: int) -> int:
    completed = {
        int(p.get("pick_no") or 0)
        for p in picks
        if p.get("player_id") and int(p.get("pick_no") or 0) > 0
    }
    for pick_no in range(1, total_picks + 1):
        if pick_no not in completed:
            return pick_no
    return total_picks + 1


def _roster_for_slot(detail: Mapping[str, Any], slot: int) -> int:
    raw = (detail.get("slot_to_roster_id") or {}).get(str(slot), slot)
    try:
        return int(raw)
    except (TypeError, ValueError):
        return slot


def _traded_owner_map(
    traded_picks: Sequence[Mapping[str, Any]],
) -> Dict[tuple[int, int], int]:
    out: Dict[tuple[int, int], int] = {}
    for raw in traded_picks or []:
        try:
            round_no = int(raw.get("round") or 0)
            original = int(raw.get("roster_id") or 0)
            owner = int(raw.get("owner_id") or 0)
        except (TypeError, ValueError):
            continue
        if round_no > 0 and original > 0 and owner > 0:
            out[(round_no, original)] = owner
    return out


def future_pick_numbers(
    detail: Mapping[str, Any],
    traded_picks: Sequence[Mapping[str, Any]],
    *,
    user_slot: Optional[int],
    current_pick: int,
) -> List[int]:
    if user_slot is None:
        return []
    config = infer_draft_config(detail)
    user_roster = _roster_for_slot(detail, user_slot)
    traded_owner = _traded_owner_map(traded_picks)

    out: List[int] = []
    total = config.teams * config.rounds
    for pick_no in range(max(1, current_pick), total + 1):
        round_no = (pick_no - 1) // config.teams + 1
        original_slot = snake_slot_for_pick(pick_no, config.teams)
        original_roster = _roster_for_slot(detail, original_slot)
        current_owner = traded_owner.get((round_no, original_roster), original_roster)
        if current_owner == user_roster:
            out.append(pick_no)
    return out


def normalize_live_pick(raw: Mapping[str, Any]) -> Dict[str, Any]:
    metadata = raw.get("metadata") or {}
    first = str(metadata.get("first_name") or "").strip()
    last = str(metadata.get("last_name") or "").strip()
    return {
        "pick_no": int(raw.get("pick_no") or 0),
        "round": int(raw.get("round") or 0),
        "draft_slot": int(raw.get("draft_slot") or 0),
        "player_id": str(raw.get("player_id") or ""),
        "name": " ".join(part for part in (first, last) if part)
            or str(raw.get("player_id") or "Unknown"),
        "pos": metadata.get("position"),
        "team": metadata.get("team"),
        "picked_by": raw.get("picked_by") or None,
        "is_keeper": bool(raw.get("is_keeper")),
    }


def build_live_draft_state(
    detail: Mapping[str, Any],
    picks: Sequence[Mapping[str, Any]],
    traded_picks: Sequence[Mapping[str, Any]],
    *,
    user_id: Optional[str] = None,
    selected_slot: Optional[int] = None,
) -> Dict[str, Any]:
    config = validate_supported_draft(detail)
    user_slot = resolve_user_slot(
        detail, user_id=user_id, selected_slot=selected_slot,
    )
    normalized_picks = sorted(
        (normalize_live_pick(p) for p in picks or []),
        key=lambda p: p["pick_no"],
    )
    total_picks = config.teams * config.rounds
    current_pick = _first_open_pick(picks, total_picks)
    draft_over = current_pick > total_picks or str(detail.get("status")) == "complete"
    on_clock_slot = None if draft_over else snake_slot_for_pick(current_pick, config.teams)
    future = future_pick_numbers(
        detail, traded_picks, user_slot=user_slot, current_pick=current_pick,
    )
    user_pick = next((p for p in future if p >= current_pick), None)
    picks_until_user = None if user_pick is None else max(0, user_pick - current_pick)
    my_roster_ids: List[str] = []
    if user_slot is not None:
        user_roster = _roster_for_slot(detail, user_slot)
        traded_owner = _traded_owner_map(traded_picks)
        for pick in normalized_picks:
            original_roster = _roster_for_slot(detail, pick["draft_slot"])
            owner = traded_owner.get(
                (pick["round"], original_roster), original_roster,
            )
            if owner == user_roster and pick["player_id"]:
                my_roster_ids.append(pick["player_id"])
    status = str(detail.get("status") or "pre_draft")
    poll_interval = None if status == "complete" else (
        5000 if status in {"drafting", "paused"} else 20000
    )
    metadata = detail.get("metadata") or {}
    return {
        "changed": True,
        "draft_id": str(detail.get("draft_id") or ""),
        "league_id": detail.get("league_id") or metadata.get("league_id"),
        "name": metadata.get("name") or "Sleeper draft",
        "season": str(detail.get("season") or ""),
        "status": status,
        "last_picked": detail.get("last_picked"),
        "pick_timer_seconds": (detail.get("settings") or {}).get("pick_timer"),
        "config": config.to_dict(),
        "available_slots": list(range(1, config.teams + 1)),
        "needs_slot": user_slot is None,
        "user_slot": user_slot,
        "current_pick": current_pick,
        "total_picks": total_picks,
        "on_clock_slot": on_clock_slot,
        "is_user_pick": user_pick is not None and user_pick == current_pick,
        "picks_until_user": picks_until_user,
        "my_upcoming_picks": future,
        "drafted_ids": [p["player_id"] for p in normalized_picks if p["player_id"]],
        "my_roster_ids": my_roster_ids,
        "picks": normalized_picks,
        "poll_interval_ms": poll_interval,
    }


def choose_league_draft(drafts: Iterable[Mapping[str, Any]]) -> Optional[Mapping[str, Any]]:
    """Choose active/paused first, then the newest pre-draft snake draft."""
    supported = [
        d for d in drafts or []
        if str(d.get("type") or "").lower() == "snake"
        and "dynasty" not in str((d.get("metadata") or {}).get("scoring_type") or "").lower()
    ]
    for desired in ("drafting", "paused", "pre_draft"):
        hits = [d for d in supported if d.get("status") == desired]
        if hits:
            return max(hits, key=lambda d: int(d.get("created") or 0))
    return None


def unchanged_live_response(detail: Mapping[str, Any]) -> Dict[str, Any]:
    status = str(detail.get("status") or "pre_draft")
    return {
        "changed": False,
        "draft_id": str(detail.get("draft_id") or ""),
        "status": status,
        "last_picked": detail.get("last_picked"),
        "poll_interval_ms": None if status == "complete" else (
            5000 if status in {"drafting", "paused"} else 20000
        ),
    }
