"""Normalized draft ranking values (auction $, snake ADP, VBD, tiers).

The dynamic ranking spreadsheets (``tests/fixtures/drafthelp/{year}Rankings.xlsm``)
generate per-league values onto position tabs once you fill in the
``LeagueInfo`` sheet (team count, scoring, superflex, auction budget).
``tools/build_draft_rankings.py`` drives Excel via ``xlwings`` and writes a
normalized ``draft_rankings_{year}.json`` blob; this module defines the schema,
the pure parsing/normalization helpers that the tool reuses, and the read-side
``RankingsRepository`` consumed by the Draft Help endpoints.

Nothing here imports ``xlwings``/Excel, so it is safe to import in the Flask app
and to unit test without Office installed.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

# ---------------------------------------------------------------------------
# Config grid (must match tools/build_draft_rankings.py)
# ---------------------------------------------------------------------------
SCHEMA_VERSION = 1

SUPPORTED_TEAM_SIZES: Tuple[int, ...] = (8, 10, 12, 14)
SUPPORTED_PPR: Tuple[float, ...] = (0.0, 0.5, 1.0)
SUPPORTED_SUPERFLEX: Tuple[bool, ...] = (False, True)
DEFAULT_AUCTION_BUDGET = 200

# Positions we extract / rank. K and DEF use a different (noisier) value
# curve and are excluded from draft-habit analysis, matching the Wrapped
# draft accolades convention.
RANKED_POSITIONS: Tuple[str, ...] = ("QB", "RB", "WR", "TE")

# 0-based column indices on each position sheet (QB/RB/WR/TE). Header is on
# sheet row 2; data starts on row 3. Confirmed against 2024Rankings.xlsm.
COL_PLAYER = 1   # B
COL_POS = 2      # C
COL_BYE = 4      # E
COL_TEAM = 5     # F
COL_FPTS = 15    # P  (projected fantasy points)
COL_DOLLAR = 19  # T  (auction $)
COL_VBD = 25     # Z  (AvgVBD value)
COL_TIER = 26    # AA (positional tier, e.g. "QB1")

_NAME_SUFFIXES = {"jr", "sr", "ii", "iii", "iv", "v"}
_PLACEHOLDER_NAMES = {"duplicate player", "player invalid"}

# Curated aliases for names the spreadsheet spells differently from Sleeper
# (keyed and valued in normalized form). Extend as ``unmatched_names`` surfaces
# new ones; most names match via punctuation/suffix normalization alone.
_NAME_ALIASES = {
    "bam knight": "zonovan knight",
    "chigoziem okonkwo": "chig okonkwo",
    "nyheim hines": "nyheim miller hines",
    "gabriel davis": "gabe davis",
    "hollywood brown": "marquise brown",
    "ken walker": "kenneth walker",
    "kenny gainwell": "kenneth gainwell",
    "mike strachan": "michael strachan",
    "mitch trubisky": "mitchell trubisky",
    "robbie anderson": "robbie chosen",
    "scott miller": "scotty miller",
    "mitchell tinsley": "mitch tinsley",
}


# ---------------------------------------------------------------------------
# Name normalization + player-id resolution
# ---------------------------------------------------------------------------
def normalize_player_name(name: Any) -> str:
    """Normalize a player name for fuzzy matching across sources.

    Lower-cases, drops ``. ' ` ,`` punctuation, turns ``-`` into a space and
    strips generational suffix tokens (Jr/Sr/II/III/IV/V). e.g. both
    ``"D.J. Moore"`` and ``"DJ Moore"`` -> ``"dj moore"``.
    """
    if name is None:
        return ""
    s = str(name).strip().lower()
    for ch in ".'`,":
        s = s.replace(ch, "")
    s = s.replace("-", " ")
    tokens = [t for t in s.split() if t and t not in _NAME_SUFFIXES]
    norm = " ".join(tokens)
    return _NAME_ALIASES.get(norm, norm)


def rankings_blob_name(year: Any) -> str:
    """Blob/fixture file name for a season's normalized rankings."""
    return f"draft_rankings_{year}.json"


def value_profile_id(
    starters: Dict[str, Any], bench_size: int, passing_td: int,
) -> str:
    """Canonical exact-profile id used by registry entries and blob names."""
    return "-".join([
        f"qb{int(starters.get('QB') or 0)}",
        f"rb{int(starters.get('RB') or 0)}",
        f"wr{int(starters.get('WR') or 0)}",
        f"te{int(starters.get('TE') or 0)}",
        f"flex{int(starters.get('FLEX') or 0)}",
        f"bn{int(bench_size)}",
        f"ptd{int(passing_td)}",
    ])


def profile_rankings_blob_name(year: Any, profile_id: str) -> str:
    """Independent provider-value blob for one exact league profile."""
    safe = str(profile_id).strip().lower()
    if not safe or any(ch not in "abcdefghijklmnopqrstuvwxyz0123456789-" for ch in safe):
        raise ValueError(f"Invalid value profile id: {profile_id!r}")
    return f"draft_rankings_{year}_elboberto_{safe}.json"


def profile_registry_blob_name(year: Any) -> str:
    return f"draft_value_profiles_{year}.json"


def adp_blob_name(year: Any) -> str:
    """Blob/fixture file name for a season's ADP (FantasyFootballCalculator)."""
    return f"draft_adp_{year}.json"


def config_key(teams: int, ppr: float, superflex: bool) -> str:
    """Stable key for a league configuration, e.g. ``"12|0.5|sf"``."""
    return f"{int(teams)}|{_ppr_label(ppr)}|{'sf' if superflex else '1qb'}"


def parse_config_key(key: str) -> Tuple[int, float, bool]:
    """Inverse of :func:`config_key`."""
    teams_s, ppr_s, sf_s = key.split("|")
    return int(teams_s), float(ppr_s), (sf_s == "sf")


def _ppr_label(ppr: float) -> str:
    f = float(ppr)
    return str(int(f)) if f.is_integer() else str(f)


class NameResolver:
    """Resolve spreadsheet player names to Sleeper player ids.

    Built from a ``players.json``-style mapping (``{player_id: {full_name,
    fantasy_positions}}``). Resolution prefers an exact ``(normalized_name,
    position)`` match and falls back to a unique name-only match.
    """

    def __init__(self, players: Dict[str, Dict[str, Any]]):
        self._by_name_pos: Dict[Tuple[str, str], str] = {}
        self._by_name: Dict[str, List[str]] = {}
        for pid, meta in (players or {}).items():
            if not isinstance(meta, dict):
                continue
            full_name = meta.get("full_name")
            if not full_name:
                continue
            norm = normalize_player_name(full_name)
            if not norm or norm in _PLACEHOLDER_NAMES:
                continue
            positions = meta.get("fantasy_positions") or []
            self._by_name.setdefault(norm, [])
            if pid not in self._by_name[norm]:
                self._by_name[norm].append(pid)
            for pos in positions:
                # Keep the first id seen for a (name, pos) pair; placeholder
                # and historical dupes generally sort later in players.json.
                self._by_name_pos.setdefault((norm, str(pos).upper()), pid)

    def resolve(self, name: Any, position: Any = None) -> Optional[str]:
        norm = normalize_player_name(name)
        if not norm:
            return None
        if position:
            pid = self._by_name_pos.get((norm, str(position).upper()))
            if pid:
                return pid
        candidates = self._by_name.get(norm)
        if candidates and len(candidates) == 1:
            return candidates[0]
        return None


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------
@dataclass
class RankingPlayer:
    """A single player's value within one league configuration."""

    player_id: str
    name: str
    pos: str
    team: Optional[str] = None
    bye: Optional[int] = None
    fpts: Optional[float] = None
    auction: Optional[float] = None
    vbd: Optional[float] = None
    tier: Optional[str] = None
    pos_rank: Optional[int] = None
    overall_rank: Optional[int] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "player_id": self.player_id,
            "name": self.name,
            "pos": self.pos,
            "team": self.team,
            "bye": self.bye,
            "fpts": self.fpts,
            "auction": self.auction,
            "vbd": self.vbd,
            "tier": self.tier,
            "pos_rank": self.pos_rank,
            "overall_rank": self.overall_rank,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "RankingPlayer":
        return cls(
            player_id=str(d["player_id"]),
            name=d.get("name", ""),
            pos=d.get("pos", ""),
            team=d.get("team"),
            bye=d.get("bye"),
            fpts=d.get("fpts"),
            auction=d.get("auction"),
            vbd=d.get("vbd"),
            tier=d.get("tier"),
            pos_rank=d.get("pos_rank"),
            overall_rank=d.get("overall_rank"),
        )


@dataclass
class RankingsConfig:
    """All ranked players for one league configuration."""

    teams: int
    ppr: float
    superflex: bool
    budget: int = DEFAULT_AUCTION_BUDGET
    players: List[RankingPlayer] = field(default_factory=list)

    @property
    def key(self) -> str:
        return config_key(self.teams, self.ppr, self.superflex)

    def by_player_id(self) -> Dict[str, RankingPlayer]:
        return {p.player_id: p for p in self.players}

    def to_dict(self) -> Dict[str, Any]:
        return {
            "teams": self.teams,
            "ppr": self.ppr,
            "superflex": self.superflex,
            "budget": self.budget,
            "players": [p.to_dict() for p in self.players],
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "RankingsConfig":
        return cls(
            teams=int(d["teams"]),
            ppr=float(d["ppr"]),
            superflex=bool(d["superflex"]),
            budget=int(d.get("budget", DEFAULT_AUCTION_BUDGET)),
            players=[RankingPlayer.from_dict(p) for p in d.get("players", [])],
        )


# ---------------------------------------------------------------------------
# Pure parsing helpers (reused by tools/build_draft_rankings.py)
# ---------------------------------------------------------------------------
def _to_float(value: Any) -> Optional[float]:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _to_int(value: Any) -> Optional[int]:
    f = _to_float(value)
    return int(round(f)) if f is not None else None


def parse_position_sheet(
    sheet_pos: str,
    rows: Sequence[Sequence[Any]],
    resolver: NameResolver,
) -> Tuple[List[RankingPlayer], List[str]]:
    """Parse raw data rows from one position sheet into ranked players.

    ``rows`` are the sheet's *data* rows (row 3 onward), each a positional
    sequence of cell values aligned to the ``COL_*`` indices. Stops at the
    first blank player cell. Returns ``(players, unmatched_names)`` where
    ``pos_rank`` reflects the sheet's existing value-sorted order.
    """
    players: List[RankingPlayer] = []
    unmatched: List[str] = []
    rank = 0
    for row in rows:
        if row is None or len(row) <= COL_PLAYER:
            break
        name = row[COL_PLAYER]
        if name is None or str(name).strip() == "":
            break
        pos = row[COL_POS] if len(row) > COL_POS and row[COL_POS] else sheet_pos
        pos = str(pos).upper()
        pid = resolver.resolve(name, pos)
        if not pid:
            unmatched.append(str(name))
            continue
        rank += 1
        players.append(
            RankingPlayer(
                player_id=pid,
                name=str(name),
                pos=pos,
                team=(str(row[COL_TEAM]) if len(row) > COL_TEAM and row[COL_TEAM] else None),
                bye=_to_int(row[COL_BYE]) if len(row) > COL_BYE else None,
                fpts=_to_float(row[COL_FPTS]) if len(row) > COL_FPTS else None,
                auction=_to_float(row[COL_DOLLAR]) if len(row) > COL_DOLLAR else None,
                vbd=_to_float(row[COL_VBD]) if len(row) > COL_VBD else None,
                tier=(str(row[COL_TIER]) if len(row) > COL_TIER and row[COL_TIER] else None),
                pos_rank=rank,
            )
        )
    return players, unmatched


def assign_overall_ranks(players: Iterable[RankingPlayer]) -> List[RankingPlayer]:
    """Assign ``overall_rank`` (1-based) by descending VBD across positions.

    Players missing a VBD sort to the bottom. Mutates and returns the list
    sorted by overall rank.
    """
    ordered = sorted(
        players,
        key=lambda p: (p.vbd is None, -(p.vbd or 0.0), p.pos_rank or 9999),
    )
    for i, p in enumerate(ordered, start=1):
        p.overall_rank = i
    return ordered


# ---------------------------------------------------------------------------
# Read-side repository
# ---------------------------------------------------------------------------
class RankingsRepository:
    """Query a ``draft_rankings_{year}.json`` blob for one season."""

    def __init__(self, blob: Dict[str, Any]):
        self.year = str(blob.get("year", ""))
        self.budget = int(blob.get("budget", DEFAULT_AUCTION_BUDGET))
        self.source_file = blob.get("source_file")
        self.provider = blob.get("provider")
        self.source = blob.get("source")
        self.source_url = blob.get("source_url")
        self.source_version = blob.get("source_version")
        self.generated_at_utc = blob.get("generated_at_utc")
        self.retrieved_at_utc = blob.get("retrieved_at_utc")
        self.attribution = blob.get("attribution")
        self.profile = blob.get("profile") if isinstance(blob.get("profile"), dict) else None
        self._configs: Dict[str, RankingsConfig] = {
            key: RankingsConfig.from_dict(cfg)
            for key, cfg in (blob.get("configs") or {}).items()
        }

    @property
    def config_keys(self) -> List[str]:
        return sorted(self._configs.keys())

    def has_config(self, teams: int, ppr: float, superflex: bool) -> bool:
        return config_key(teams, ppr, superflex) in self._configs

    def get_config(
        self,
        teams: int,
        ppr: float,
        superflex: bool,
        fallback: bool = True,
    ) -> Optional[RankingsConfig]:
        """Return the requested config, or the nearest one when missing.

        Fallback order (only when ``fallback`` is True): exact -> same
        superflex+ppr, nearest team size -> same superflex, nearest team size
        then nearest ppr -> any with matching superflex -> any config at all.
        """
        exact = self._configs.get(config_key(teams, ppr, superflex))
        if exact is not None or not fallback:
            return exact
        candidates = list(self._configs.values())
        if not candidates:
            return None

        def distance(c: RankingsConfig) -> Tuple[int, int, int]:
            return (
                0 if c.superflex == superflex else 1,
                abs(c.teams - int(teams)),
                int(abs(c.ppr - float(ppr)) * 10),
            )

        return min(candidates, key=distance)


def repository_from_blob(blob: Dict[str, Any]) -> RankingsRepository:
    """Build a :class:`RankingsRepository` from a parsed blob dict."""
    return RankingsRepository(blob)
