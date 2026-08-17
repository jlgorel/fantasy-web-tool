"""Draft Help summary orchestration (features 1-3).

Ties together Sleeper draft fetching (``draft_fetch``), the historical value
baseline (``rankings_source``) and the pure analyzers (``habits``) into the
three habit-summary payloads:

    1. ``user_habits``       -- your tendencies across all your leagues
    2. ``league_habits``     -- a league's managers' tendencies (this league)
    3. ``opponents_habits``  -- your league-mates' tendencies in their OTHER
                                leagues (capped + flagged as potentially slow)

Network/blob access is injected (defaults wired to the real fetchers) so the
orchestration is unit-testable with in-memory fakes.
"""
from __future__ import annotations

import datetime as _dt
import time
from collections import defaultdict
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from app.services.blob_store import load_blob
from app.services.draft_help import draft_fetch as df
from app.services.draft_help import habits
from app.services.draft_help.draft_fetch import NormalizedDraft, NormalizedPick, infer_league_config
from app.services.draft_help.rankings_source import (
    DEFAULT_AUCTION_BUDGET,
    DEFAULT_VALUE_PROVIDER_ID,
    RankingPlayer,
    RankingsRepository,
    adp_blob_name,
    config_key,
    profile_registry_blob_name,
    rankings_blob_name,
    value_providers_registry_blob_name,
    value_provider_status_blob_name,
)
from app.services.sleeper_league_lookup import get_league_season_chain

# Per-opponent crawl caps (feature 3) -- keep it from fanning out forever.
DEFAULT_MAX_OPP_LEAGUES = 5
DEFAULT_SEASONS = 3

_SOURCE_CACHE_TTL_SECONDS = 300
_REPO_CACHE: Dict[str, Tuple[float, RankingsRepository]] = {}
_PROFILE_REGISTRY_CACHE: Dict[str, Tuple[float, Dict[str, Any]]] = {}
_PROVIDER_REGISTRY_CACHE: Dict[str, Tuple[float, Dict[str, Any]]] = {}
_PROVIDER_STATUS_CACHE: Dict[str, Tuple[float, Dict[str, Any]]] = {}

# Drafts/value pair fed to the aggregators.
DraftCtx = Tuple[NormalizedDraft, Dict[str, RankingPlayer]]


# ---------------------------------------------------------------------------
# Rankings repo loading
# ---------------------------------------------------------------------------
def load_rankings_repo(
    year: Any, *, profile_id: Optional[str] = None,
    provider_id: str = DEFAULT_VALUE_PROVIDER_ID,
    blob_loader: Optional[Callable[[str], Any]] = None,
) -> Optional[RankingsRepository]:
    """Load (and cache) the ``draft_rankings_{year}.json`` repo for a season."""
    year = str(year)
    provider_id = str(provider_id or DEFAULT_VALUE_PROVIDER_ID).strip().lower()
    blob_name = rankings_blob_name(year)
    if profile_id:
        registry = load_profile_registry(
            year, provider_id=provider_id, blob_loader=blob_loader,
        ) or {}
        entry = (registry.get("profiles") or {}).get(str(profile_id)) or {}
        candidate_name = entry.get("blob_name")
        if not candidate_name:
            return None
        blob_name = str(candidate_name)
    elif provider_id != DEFAULT_VALUE_PROVIDER_ID:
        return None
    if blob_loader is not None:  # tests: bypass cache
        try:
            return RankingsRepository(blob_loader(blob_name))
        except Exception:
            return None
    cache_key = f"{year}:{blob_name}"
    cached = _REPO_CACHE.get(cache_key)
    if cached and time.monotonic() - cached[0] < _SOURCE_CACHE_TTL_SECONDS:
        return cached[1]
    try:
        repo: Optional[RankingsRepository] = RankingsRepository(load_blob(blob_name))
    except Exception:
        repo = None
    if repo is not None:
        _REPO_CACHE[cache_key] = (time.monotonic(), repo)
    else:
        _REPO_CACHE.pop(cache_key, None)
    return repo


def load_profile_registry(
    year: Any, *, provider_id: str = DEFAULT_VALUE_PROVIDER_ID,
    blob_loader: Optional[Callable[[str], Any]] = None,
) -> Optional[Dict[str, Any]]:
    year = str(year)
    provider_id = str(provider_id or DEFAULT_VALUE_PROVIDER_ID).strip().lower()
    providers = load_value_providers_registry(year, blob_loader=blob_loader) or {}
    provider = (providers.get("providers") or {}).get(provider_id) or {}
    name = provider.get("profile_registry_blob_name")
    if not name and provider_id == DEFAULT_VALUE_PROVIDER_ID:
        name = profile_registry_blob_name(year)
    if not name:
        return None
    name = str(name)
    loader = blob_loader or load_blob
    cache_key = f"{year}:{provider_id}:{name}"
    if blob_loader is None:
        cached = _PROFILE_REGISTRY_CACHE.get(cache_key)
        if cached and time.monotonic() - cached[0] < _SOURCE_CACHE_TTL_SECONDS:
            return cached[1]
    try:
        registry = loader(name)
    except Exception:
        registry = None
    if isinstance(registry, dict) and isinstance(registry.get("profiles"), dict):
        if blob_loader is None:
            _PROFILE_REGISTRY_CACHE[cache_key] = (time.monotonic(), registry)
        return registry
    if blob_loader is None:
        _PROFILE_REGISTRY_CACHE.pop(cache_key, None)
    return None


def load_value_providers_registry(
    year: Any, *, blob_loader: Optional[Callable[[str], Any]] = None,
) -> Optional[Dict[str, Any]]:
    """Load provider capabilities, synthesizing ElBoberto during migration."""
    year = str(year)
    loader = blob_loader or load_blob
    if blob_loader is None:
        cached = _PROVIDER_REGISTRY_CACHE.get(year)
        if cached and time.monotonic() - cached[0] < _SOURCE_CACHE_TTL_SECONDS:
            return cached[1]
    try:
        registry = loader(value_providers_registry_blob_name(year))
    except Exception:
        registry = None
    if not isinstance(registry, dict) or not isinstance(registry.get("providers"), dict):
        try:
            legacy = loader(profile_registry_blob_name(year))
        except Exception:
            legacy = None
        if isinstance(legacy, dict) and isinstance(legacy.get("profiles"), dict):
            registry = {
                "schema_version": 1,
                "year": year,
                "default_provider_id": DEFAULT_VALUE_PROVIDER_ID,
                "providers": {
                    DEFAULT_VALUE_PROVIDER_ID: {
                        "id": DEFAULT_VALUE_PROVIDER_ID,
                        "name": "ElBoberto Custom Auction Value Generator",
                        "profile_registry_blob_name": profile_registry_blob_name(year),
                        "profile_count": len(legacy["profiles"]),
                    },
                },
            }
        else:
            try:
                legacy_rankings = loader(rankings_blob_name(year))
            except Exception:
                legacy_rankings = None
            if isinstance(legacy_rankings, dict) and isinstance(
                legacy_rankings.get("configs"), dict
            ):
                registry = {
                    "schema_version": 1,
                    "year": year,
                    "default_provider_id": DEFAULT_VALUE_PROVIDER_ID,
                    "providers": {
                        DEFAULT_VALUE_PROVIDER_ID: {
                            "id": DEFAULT_VALUE_PROVIDER_ID,
                            "name": legacy_rankings.get("source") or "Draft rankings",
                            "profile_registry_blob_name": None,
                            "profile_count": 0,
                        },
                    },
                }
    if isinstance(registry, dict) and isinstance(registry.get("providers"), dict):
        if blob_loader is None:
            _PROVIDER_REGISTRY_CACHE[year] = (time.monotonic(), registry)
        return registry
    if blob_loader is None:
        _PROVIDER_REGISTRY_CACHE.pop(year, None)
    return None


def load_value_provider_status(
    year: Any, provider_id: str,
    *, blob_loader: Optional[Callable[[str], Any]] = None,
) -> Optional[Dict[str, Any]]:
    cache_key = f"{year}:{provider_id}"
    if blob_loader is None:
        cached = _PROVIDER_STATUS_CACHE.get(cache_key)
        if cached and time.monotonic() - cached[0] < _SOURCE_CACHE_TTL_SECONDS:
            return cached[1]
    loader = blob_loader or load_blob
    try:
        status = loader(value_provider_status_blob_name(year, provider_id))
    except Exception:
        status = None
    if isinstance(status, dict):
        if blob_loader is None:
            _PROVIDER_STATUS_CACHE[cache_key] = (time.monotonic(), status)
        return status
    if blob_loader is None:
        _PROVIDER_STATUS_CACHE.pop(cache_key, None)
    return None


def value_map_for(repo: Optional[RankingsRepository], league_cfg: Dict[str, Any]) -> Dict[str, RankingPlayer]:
    if not repo:
        return {}
    cfg = repo.get_config(
        int(league_cfg.get("teams") or 12),
        float(league_cfg.get("ppr") or 0.0),
        bool(league_cfg.get("superflex")),
    )
    return cfg.by_player_id() if cfg else {}


def rankings_config_players(
    year: Any, teams: int, ppr: float, superflex: bool,
    *, profile_id: Optional[str] = None,
    provider_id: str = DEFAULT_VALUE_PROVIDER_ID,
    blob_loader: Optional[Callable[[str], Any]] = None,
) -> List[Dict[str, Any]]:
    """Player rows for a (year, teams, ppr, superflex) config -- drives the
    mock draft board + the sim. Real ADP (``adp``/``adp_stdev``, from the FFC
    blob) is merged in when available; absent it, the sim falls back to VBD
    order. When the season value sheet is absent, a current ADP-only pool is
    returned if available so the browser can attach uploaded values."""
    repo = load_rankings_repo(
        year, profile_id=profile_id, provider_id=provider_id,
        blob_loader=blob_loader,
    )
    if not repo:
        return _adp_only_config_players(
            year, teams, ppr, superflex, blob_loader=blob_loader,
        )
    cfg = repo.get_config(int(teams), float(ppr), bool(superflex), fallback=False)
    if not cfg:
        return []
    adp = _adp_for_config(year, teams, ppr, superflex, blob_loader=blob_loader)
    provider_values: Dict[str, Dict[str, Dict[str, Any]]] = defaultdict(dict)
    provider_registry = load_value_providers_registry(
        year, blob_loader=blob_loader,
    ) or {}
    for comparison_provider_id in (provider_registry.get("providers") or {}):
        comparison_repo = load_rankings_repo(
            year, profile_id=profile_id,
            provider_id=comparison_provider_id,
            blob_loader=blob_loader,
        )
        comparison_cfg = comparison_repo.get_config(
            int(teams), float(ppr), bool(superflex), fallback=False,
        ) if comparison_repo else None
        if not comparison_cfg:
            continue
        for comparison_player in comparison_cfg.players:
            provider_values[comparison_player.player_id][comparison_provider_id] = {
                "value": comparison_player.vbd,
                "rank": comparison_player.overall_rank,
                "points": comparison_player.provider_points,
                "tier": comparison_player.tier,
            }
    rows: List[Dict[str, Any]] = []
    for p in cfg.players:
        row = p.to_dict()
        entry = adp.get(row["player_id"])
        if entry:
            row["adp"] = entry.get("adp")
            row["adp_stdev"] = entry.get("stdev")
            row["adp_stdev_source"] = entry.get("stdev_source")
            row["adp_sample_size"] = entry.get("times_drafted")
            row["adp_high"] = entry.get("high")
            row["adp_low"] = entry.get("low")
        row["provider_values"] = provider_values.get(row["player_id"], {})
        rows.append(row)
    return rows


def rankings_config_sources(
    year: Any, teams: int, ppr: float, superflex: bool,
    *, profile_id: Optional[str] = None,
    provider_id: str = DEFAULT_VALUE_PROVIDER_ID,
    blob_loader: Optional[Callable[[str], Any]] = None,
) -> Dict[str, Any]:
    """Source/freshness metadata for a board configuration."""
    repo = load_rankings_repo(
        year, profile_id=profile_id, provider_id=provider_id,
        blob_loader=blob_loader,
    )
    registry = load_profile_registry(
        year, provider_id=provider_id, blob_loader=blob_loader,
    ) or {}
    provider_registry = load_value_providers_registry(
        year, blob_loader=blob_loader,
    ) or {}
    blob = _load_adp_blob(year, blob_loader=blob_loader) or {}
    if repo:
        value_source = repo.source or repo.source_file or "draft rankings"
    elif blob.get("adp_only"):
        value_source = "custom upload required"
    else:
        value_source = "unavailable"
    key = config_key(int(teams), float(ppr), bool(superflex))
    cfg = (blob.get("configs") or {}).get(key) or {}
    if not isinstance(cfg.get("players"), dict) and blob_loader is None:
        _ADP_CACHE.pop(str(year), None)
        blob = _load_adp_blob(year, force_reload=True) or {}
        cfg = (blob.get("configs") or {}).get(key) or {}
    available_providers: List[Dict[str, Any]] = []
    for available_provider_id, raw_entry in (
        provider_registry.get("providers") or {}
    ).items():
        entry = dict(raw_entry) if isinstance(raw_entry, dict) else {
            "id": available_provider_id,
        }
        available_repo = load_rankings_repo(
            year, profile_id=profile_id,
            provider_id=available_provider_id,
            blob_loader=blob_loader,
        )
        entry["available"] = bool(
            available_repo and available_repo.has_config(
                int(teams), float(ppr), bool(superflex),
            )
        )
        entry["status"] = load_value_provider_status(
            year, available_provider_id, blob_loader=blob_loader,
        )
        available_providers.append(entry)
    return {
        "values": {
            "source": value_source,
            "provider": repo.provider if repo else None,
            "source_url": repo.source_url if repo else None,
            "source_version": repo.source_version if repo else None,
            "generated_at_utc": repo.generated_at_utc if repo else None,
            "retrieved_at_utc": repo.retrieved_at_utc if repo else None,
            "attribution": repo.attribution if repo else None,
            "profile": repo.profile if repo else None,
            "requested_profile_id": profile_id,
            "selected_provider_id": provider_id,
            "available_providers": available_providers,
            "available_profiles": list((registry.get("profiles") or {}).values()),
            "source_revision": repo.source_content_sha256 if repo else None,
        },
        "adp": {
            "source": blob.get("source"),
            "generated_at_utc": blob.get("generated_at_utc"),
            "format": cfg.get("format"),
            "total_drafts": cfg.get("total_drafts"),
            "matched": cfg.get("matched"),
            "total": cfg.get("total"),
        },
    }


_ADP_CACHE: Dict[str, Tuple[float, Dict[str, Any]]] = {}


def _load_adp_blob(
    year: Any,
    *,
    blob_loader: Optional[Callable[[str], Any]] = None,
    force_reload: bool = False,
) -> Optional[Dict[str, Any]]:
    """Load (and cache) the ``draft_adp_{year}.json`` blob; ``None`` if absent."""
    year = str(year)
    loader = blob_loader or load_blob
    if blob_loader is None and not force_reload:
        cached = _ADP_CACHE.get(year)
        if cached and time.monotonic() - cached[0] < _SOURCE_CACHE_TTL_SECONDS:
            return cached[1]
    try:
        blob = loader(adp_blob_name(year))
    except Exception:
        blob = None
    has_configs = (
        isinstance(blob, dict)
        and isinstance(blob.get("configs"), dict)
        and bool(blob.get("configs"))
    )
    if blob_loader is None and has_configs:
        _ADP_CACHE[year] = (time.monotonic(), blob)
    elif blob_loader is None:
        # Never negative-cache a missing blob: the scheduled scraper may create
        # it moments later, especially at the start of a new draft season.
        _ADP_CACHE.pop(year, None)
    return blob


def _adp_for_config(
    year: Any, teams: int, ppr: float, superflex: bool,
    *, blob_loader: Optional[Callable[[str], Any]] = None,
) -> Dict[str, Dict[str, Any]]:
    """``{player_id: {adp, stdev}}`` for one config, or ``{}`` when unavailable.

    Defensive: a rankings-shaped blob (config ``players`` is a list, e.g. when
    a test injects its rankings loader) yields ``{}`` rather than erroring.
    """
    blob = _load_adp_blob(year, blob_loader=blob_loader)
    if not blob:
        return {}
    key = config_key(int(teams), float(ppr), bool(superflex))
    cfg = (blob.get("configs") or {}).get(key) or {}
    players = cfg.get("players")
    if not isinstance(players, dict) and blob_loader is None:
        # A partial/stale blob may have been cached while the scheduled source
        # was still publishing. Reload once instead of serving a five-minute
        # board with no ADP/source metadata.
        _ADP_CACHE.pop(str(year), None)
        blob = _load_adp_blob(year, force_reload=True) or {}
        cfg = (blob.get("configs") or {}).get(key) or {}
        players = cfg.get("players")
    return players if isinstance(players, dict) else {}


def _adp_only_config_players(
    year: Any, teams: int, ppr: float, superflex: bool,
    *, blob_loader: Optional[Callable[[str], Any]] = None,
) -> List[Dict[str, Any]]:
    """Build display/sim rows from a current ADP-only player pool.

    No VBD or projected points are invented. The board is useful for CSV
    matching and ADP timing; recommendations become meaningful only after the
    browser supplies value overrides.
    """
    blob = _load_adp_blob(year, blob_loader=blob_loader) or {}
    if not blob.get("adp_only"):
        return []
    key = config_key(int(teams), float(ppr), bool(superflex))
    cfg = (blob.get("configs") or {}).get(key) or {}
    players = cfg.get("players")
    if not isinstance(players, dict):
        return []
    ordered = sorted(
        players.items(),
        key=lambda item: float((item[1] or {}).get("adp") or 9999),
    )
    rows: List[Dict[str, Any]] = []
    for rank, (pid, entry) in enumerate(ordered, start=1):
        if not isinstance(entry, dict) or not entry.get("name") or not entry.get("pos"):
            continue
        rows.append({
            "player_id": str(pid),
            "name": entry.get("name"),
            "pos": entry.get("pos"),
            "team": entry.get("team"),
            "bye": None,
            "fpts": None,
            "auction": None,
            "vbd": None,
            "tier": None,
            "pos_rank": None,
            "overall_rank": rank,
            "adp": entry.get("adp"),
            "adp_stdev": entry.get("stdev"),
            "adp_stdev_source": entry.get("stdev_source"),
            "adp_sample_size": entry.get("times_drafted"),
            "adp_high": entry.get("high"),
            "adp_low": entry.get("low"),
        })
    return rows


def _candidate_years(seasons: int) -> List[str]:
    """Recent NFL seasons to probe, newest first (e.g. 2026, 2025, ...)."""
    now = _dt.datetime.now().year
    return [str(now - i) for i in range(seasons + 1)]


# ---------------------------------------------------------------------------
# Per-manager aggregation
# ---------------------------------------------------------------------------
class _ManagerAcc:
    __slots__ = ("snake_lists", "auction_lists", "reach_entries", "inflation_rows")

    def __init__(self) -> None:
        self.snake_lists: List[List[NormalizedPick]] = []
        self.auction_lists: List[List[NormalizedPick]] = []
        self.reach_entries: List[Dict[str, Any]] = []
        self.inflation_rows: List[Dict[str, Any]] = []


def _group_by_user(draft: NormalizedDraft) -> Dict[str, List[NormalizedPick]]:
    out: Dict[str, List[NormalizedPick]] = defaultdict(list)
    for p in draft.picks:
        if p.user_id:
            out[p.user_id].append(p)
    return out


def _accumulate(
    accs: Dict[str, _ManagerAcc],
    draft: NormalizedDraft,
    vmap: Dict[str, RankingPlayer],
    only_user: Optional[str] = None,
) -> None:
    if draft.is_dynasty:
        return  # Draft Help ignores dynasty/rookie drafts entirely.
    for uid, picks in _group_by_user(draft).items():
        if only_user and uid != only_user:
            continue
        acc = accs.setdefault(uid, _ManagerAcc())
        if draft.is_auction:
            acc.auction_lists.append(picks)
            acc.inflation_rows.extend(habits.auction_inflation_curve(picks, vmap))
        else:
            acc.snake_lists.append(picks)
            acc.reach_entries.extend(habits.reach_value_entries(picks, vmap))


def _manager_summary(acc: _ManagerAcc, budget: int) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    if acc.snake_lists:
        flat = [p for lst in acc.snake_lists for p in lst]
        out["snake"] = {
            "drafts_counted": len(acc.snake_lists),
            "position_by_round": habits.position_by_round(flat),
            "early_round_mix": habits.first_n_round_position_mix(flat, 3),
            "archetypes": habits._archetype_counts(acc.snake_lists),
            "reach": habits.summarize_reach_entries(acc.reach_entries),
        }
    if acc.auction_lists:
        spend_summaries = [habits.auction_spend_summary(lst, budget) for lst in acc.auction_lists if lst]
        out["auction"] = {
            "drafts_counted": len(acc.auction_lists),
            "avg_spend_by_position": habits._avg_spend_by_position(spend_summaries),
            "avg_stars_and_scrubs_index": habits._avg_metric(spend_summaries, "stars_and_scrubs_index"),
            "avg_max_bid_pct_budget": habits._avg_metric(spend_summaries, "max_bid_pct_budget"),
            "inflation_curve": acc.inflation_rows,
        }
    favs = habits.favorite_players(acc.snake_lists + acc.auction_lists, min_count=2)
    if favs:
        out["favorites"] = favs
    return out


# ---------------------------------------------------------------------------
# Feature 2: a league's draft habits (this league, across its season chain)
# ---------------------------------------------------------------------------
def league_habits(
    league_id: str,
    seasons: int = DEFAULT_SEASONS,
    *,
    season_chain: Callable[[str], List[Dict[str, Any]]] = get_league_season_chain,
    fetch_league: Callable[[str], Optional[Dict[str, Any]]] = df.fetch_league,
    fetch_users: Callable[[str], Dict[str, str]] = df.fetch_league_users,
    load_drafts: Callable[[str], List[NormalizedDraft]] = df.load_league_drafts,
    blob_loader: Optional[Callable[[str], Any]] = None,
) -> Dict[str, Any]:
    chain = season_chain(league_id) or [{"league_id": league_id, "season": None}]
    chain = chain[:seasons]

    accs: Dict[str, _ManagerAcc] = {}
    usernames: Dict[str, str] = {}
    seasons_used: List[Dict[str, Any]] = []
    newest_ctx: Optional[DraftCtx] = None
    auction_ctxs: List[DraftCtx] = []
    budget = DEFAULT_AUCTION_BUDGET

    for entry in chain:
        lid = entry["league_id"]
        league = fetch_league(lid)
        if not league:
            continue
        if df.is_dynasty_league(league):
            continue  # Draft Help ignores dynasty leagues entirely.
        cfg = infer_league_config(league)
        season = entry.get("season") or league.get("season")
        usernames.update(fetch_users(lid))
        repo = load_rankings_repo(season, blob_loader=blob_loader)
        vmap = value_map_for(repo, cfg)
        drafts = load_drafts(lid)
        seasons_used.append({"season": str(season), "league_id": lid,
                             "config": cfg, "drafts": len(drafts)})
        for d in drafts:
            _accumulate(accs, d, vmap)
            if newest_ctx is None:
                newest_ctx = (d, vmap)
            if d.is_auction:
                auction_ctxs.append((d, vmap))

    managers = {
        uid: {"username": usernames.get(uid, uid), **_manager_summary(acc, budget)}
        for uid, acc in accs.items()
    }
    return {
        "feature": "league_habits",
        "league_id": league_id,
        "seasons": seasons_used,
        "managers": managers,
        "league_wide": _league_wide_patterns(newest_ctx, auction_ctxs),
    }


def _aggregate_market_status(auction_ctxs: Sequence[DraftCtx]) -> Dict[str, Any]:
    """Per-position market read across every auction draft (newest first).

    WR -- and the other ranked positions -- are *always* present so the UI can
    always show the WR-market verdict. Each entry: ``{drafts_analyzed,
    crashed_in, crashed, latest}`` where ``latest`` is the newest draft's full
    :func:`habits.market_status`.
    """
    from app.services.draft_help.rankings_source import RANKED_POSITIONS
    out: Dict[str, Any] = {}
    for pos in RANKED_POSITIONS:
        statuses = [habits.market_status(d.picks, vmap, pos) for d, vmap in auction_ctxs]
        statuses = [s for s in statuses if s["buys_analyzed"] > 0]
        crashed_in = sum(1 for s in statuses if s["crashed"])
        out[pos] = {
            "drafts_analyzed": len(statuses),
            "crashed_in": crashed_in,
            "crashed": crashed_in > 0,
            "latest": statuses[0] if statuses else None,
        }
    return out


def _aggregate_elite_market(auction_ctxs: Sequence[DraftCtx]) -> Optional[Dict[str, Any]]:
    """Average the elite hot/cold-start read across the league's auctions."""
    curves = [habits.elite_market_curve(d.picks, vmap) for d, vmap in auction_ctxs]
    curves = [c for c in curves if c]
    if not curves:
        return None
    early = sum(c["early_inflation"] for c in curves) / len(curves)
    late = sum(c["late_inflation"] for c in curves) / len(curves)
    diff = early - late
    pattern = "hot_start" if diff >= 15 else ("cold_start" if -diff >= 15 else "flat")
    return {
        "drafts_analyzed": len(curves),
        "early_inflation": round(early, 1),
        "late_inflation": round(late, 1),
        "diff": round(diff, 1),
        "pattern": pattern,
        "hot_starts": sum(1 for c in curves if c["pattern"] == "hot_start"),
        "cold_starts": sum(1 for c in curves if c["pattern"] == "cold_start"),
    }


def _league_wide_patterns(
    newest_ctx: Optional[DraftCtx],
    auction_ctxs: Sequence[DraftCtx] = (),
) -> Dict[str, Any]:
    """League-wide read: position runs (snake, newest draft) or the market
    crash + elite hot/cold start (auction, across the league's auctions)."""
    if newest_ctx is None:
        return {}
    draft, vmap = newest_ctx
    from app.services.draft_help.rankings_source import RANKED_POSITIONS
    if draft.is_auction:
        ctxs = list(auction_ctxs) or [newest_ctx]
        return {
            "draft_type": "auction",
            "drafts_analyzed": len(ctxs),
            "market_crash": _aggregate_market_status(ctxs),
            "elite_market": _aggregate_elite_market(ctxs),
        }
    runs = {pos: habits.detect_runs(draft.picks, pos) for pos in RANKED_POSITIONS}
    runs = {pos: r for pos, r in runs.items() if r}
    off_board = {
        pos: habits.position_off_board(draft.picks).get(pos, [])[:5]
        for pos in RANKED_POSITIONS
    }
    return {"draft_type": "snake", "runs": runs, "first_five_off_board": off_board}


# ---------------------------------------------------------------------------
# Feature 1: your habits across all your leagues
# ---------------------------------------------------------------------------
def user_habits(
    username: str,
    seasons: int = DEFAULT_SEASONS,
    *,
    resolve_user_id: Callable[[str], Optional[str]] = None,  # type: ignore[assignment]
    fetch_user_leagues: Callable[[str, str], List[Dict[str, Any]]] = df.fetch_user_leagues,
    fetch_league: Callable[[str], Optional[Dict[str, Any]]] = df.fetch_league,
    load_drafts: Callable[[str], List[NormalizedDraft]] = df.load_league_drafts,
    blob_loader: Optional[Callable[[str], Any]] = None,
) -> Dict[str, Any]:
    user_id = (resolve_user_id or df.resolve_user_id)(username)
    if not user_id:
        return {"feature": "user_habits", "username": username, "error": "user_not_found"}

    accs: Dict[str, _ManagerAcc] = {}
    seen_drafts: set = set()
    leagues_scanned = 0
    for year in _candidate_years(seasons):
        for lg in fetch_user_leagues(user_id, year):
            lid = lg.get("league_id")
            if not lid:
                continue
            league = fetch_league(lid) or lg
            if df.is_dynasty_league(league):
                continue  # Draft Help ignores dynasty leagues entirely.
            cfg = infer_league_config(league)
            repo = load_rankings_repo(lg.get("season") or year, blob_loader=blob_loader)
            vmap = value_map_for(repo, cfg)
            had_draft = False
            for d in load_drafts(lid):
                if d.draft_id in seen_drafts:
                    continue
                seen_drafts.add(d.draft_id)
                _accumulate(accs, d, vmap, only_user=user_id)
                had_draft = True
            if had_draft:
                leagues_scanned += 1

    summary = _manager_summary(accs.get(user_id, _ManagerAcc()), DEFAULT_AUCTION_BUDGET)
    return {
        "feature": "user_habits",
        "username": username,
        "user_id": user_id,
        "leagues_scanned": leagues_scanned,
        "summary": summary,
    }


# ---------------------------------------------------------------------------
# Feature 3: opponents' habits in their OTHER leagues
# ---------------------------------------------------------------------------
def opponents_habits(
    league_id: str,
    seasons: int = DEFAULT_SEASONS,
    max_leagues: int = DEFAULT_MAX_OPP_LEAGUES,
    *,
    fetch_users: Callable[[str], Dict[str, str]] = df.fetch_league_users,
    fetch_user_leagues: Callable[[str, str], List[Dict[str, Any]]] = df.fetch_user_leagues,
    fetch_league: Callable[[str], Optional[Dict[str, Any]]] = df.fetch_league,
    load_drafts: Callable[[str], List[NormalizedDraft]] = df.load_league_drafts,
    season_chain: Callable[[str], List[Dict[str, Any]]] = get_league_season_chain,
    blob_loader: Optional[Callable[[str], Any]] = None,
) -> Dict[str, Any]:
    users = fetch_users(league_id)
    # Exclude this league + its whole season chain from each opponent's crawl.
    own_league_ids = {league_id} | {e["league_id"] for e in (season_chain(league_id) or [])}

    opponents: Dict[str, Dict[str, Any]] = {}
    for uid, uname in users.items():
        accs: Dict[str, _ManagerAcc] = {}
        scanned = 0
        seen_drafts: set = set()
        for year in _candidate_years(seasons):
            if scanned >= max_leagues:
                break
            for lg in fetch_user_leagues(uid, year):
                if scanned >= max_leagues:
                    break
                lid = lg.get("league_id")
                if not lid or lid in own_league_ids:
                    continue
                league = fetch_league(lid) or lg
                if df.is_dynasty_league(league):
                    continue  # Draft Help ignores dynasty leagues entirely.
                cfg = infer_league_config(league)
                repo = load_rankings_repo(lg.get("season") or year, blob_loader=blob_loader)
                vmap = value_map_for(repo, cfg)
                had_draft = False
                for d in load_drafts(lid):
                    if d.draft_id in seen_drafts:
                        continue
                    seen_drafts.add(d.draft_id)
                    _accumulate(accs, d, vmap, only_user=uid)
                    had_draft = True
                if had_draft:
                    scanned += 1
        summary = _manager_summary(accs.get(uid, _ManagerAcc()), DEFAULT_AUCTION_BUDGET)
        opponents[uid] = {
            "username": uname,
            "leagues_scanned": scanned,
            "summary": summary,
        }
    return {
        "feature": "opponents_habits",
        "league_id": league_id,
        "warning": "This crawls each league-mate's other leagues and can be slow.",
        "caps": {"max_leagues_per_opponent": max_leagues, "seasons": seasons},
        "opponents": opponents,
    }
