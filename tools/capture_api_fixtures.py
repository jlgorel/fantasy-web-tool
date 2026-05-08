"""Capture sanitized API response fixtures for tests.

Hits the live Sleeper + Fleaflicker APIs for a given user and writes the
JSON responses into ``tests/fixtures/api/{sleeper,fleaflicker}/``. All
identifying values (user IDs, usernames, emails, league IDs, owner IDs,
team display names, avatars) are replaced with deterministic placeholders
so the fixtures are safe to commit.

Usage (from repo root)::

    python tools/capture_api_fixtures.py \
        --sleeper-username jlgorel \
        --fleaflicker-email jlgorel@example.com

Either flag can be omitted to skip that platform. Pass ``--raw`` to also
write the unsanitized responses next to the sanitized ones (helpful while
debugging the sanitizer; the raw files are gitignored).

The script is idempotent — re-running it overwrites the fixtures with the
latest live responses. The placeholder mapping is deterministic per-run so
``user_abc123`` always becomes ``USER_001`` etc.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

import requests

REPO_ROOT = Path(__file__).resolve().parent.parent
FIXTURE_ROOT = REPO_ROOT / "tests" / "fixtures" / "api"
SLEEPER_DIR = FIXTURE_ROOT / "sleeper"
FF_DIR = FIXTURE_ROOT / "fleaflicker"
RAW_SUFFIX = ".raw.json"

REQUEST_TIMEOUT = 15
PAUSE_BETWEEN_CALLS = 0.4  # be polite to the public APIs


# ---------------------------------------------------------------------------
# Sanitizer: deterministic id rewriting
# ---------------------------------------------------------------------------
@dataclass
class Sanitizer:
    """Builds a stable, deterministic real-id -> placeholder mapping.

    Preserving determinism within a run means a sanitized fixture file can
    reference another sanitized fixture (e.g. ``leagues.json`` -> the
    individual ``league_<id>.json``) and the IDs still match across files.
    """

    user_ids: Dict[str, str] = field(default_factory=dict)
    league_ids: Dict[str, str] = field(default_factory=dict)
    owner_ids: Dict[str, str] = field(default_factory=dict)
    roster_ids: Dict[str, str] = field(default_factory=dict)
    team_ids: Dict[str, str] = field(default_factory=dict)
    avatars: Dict[str, str] = field(default_factory=dict)

    # Mapping of patterns we'll always blank out (replaced wholesale).
    real_username: Optional[str] = None
    real_email: Optional[str] = None

    def _alloc(self, table: Dict[str, str], real: str, prefix: str) -> str:
        if real in table:
            return table[real]
        token = f"{prefix}_{len(table) + 1:03d}"
        table[real] = token
        return token

    def user(self, real: str) -> str:
        return self._alloc(self.user_ids, real, "USER")

    def league(self, real: str) -> str:
        return self._alloc(self.league_ids, real, "LEAGUE")

    def owner(self, real: str) -> str:
        return self._alloc(self.owner_ids, real, "OWNER")

    def roster(self, real: str) -> str:
        return self._alloc(self.roster_ids, real, "ROSTER")

    def team(self, real: str) -> str:
        return self._alloc(self.team_ids, real, "TEAM")

    def avatar(self, real: str) -> str:
        # Avatars are usually long hex hashes; blank them for size + privacy.
        return self._alloc(self.avatars, real, "AVATAR")

    # -- visitor over JSON ---------------------------------------------------
    SLEEPER_USER_KEYS = {"user_id", "owner_id", "co_owners"}
    SLEEPER_LEAGUE_KEYS = {"league_id", "previous_league_id"}
    SLEEPER_ROSTER_KEYS = {"roster_id"}
    DISPLAY_NAME_KEYS = {"display_name", "username"}
    EMAIL_KEYS = {"email", "emailAddress"}
    AVATAR_KEYS = {"avatar", "metadata_avatar"}

    # Keys for Fleaflicker (camelCase).
    FF_LEAGUE_KEYS = {"id"}  # only inside an obvious league context
    FF_OWNER_KEYS = {"ownedTeam", "team", "user"}

    def sanitize_sleeper(self, data: Any, *, ctx: str = "") -> Any:
        if isinstance(data, dict):
            cleaned: Dict[str, Any] = {}
            for k, v in data.items():
                if k in self.SLEEPER_USER_KEYS:
                    cleaned[k] = self._scalar(v, self.user) if k != "co_owners" else (
                        [self.user(x) for x in v] if isinstance(v, list) else v
                    )
                elif k in self.SLEEPER_LEAGUE_KEYS:
                    cleaned[k] = self._scalar(v, self.league)
                elif k in self.SLEEPER_ROSTER_KEYS:
                    cleaned[k] = self._scalar(v, self.roster)
                elif k in self.DISPLAY_NAME_KEYS and isinstance(v, str):
                    cleaned[k] = "testuser" if self._is_real_username(v) else f"user_{_short_hash(v)}"
                elif k == "name" and ctx == "league" and isinstance(v, str):
                    # League display name — keep a generic placeholder so
                    # tests can assert "name" exists without leaking yours.
                    cleaned[k] = f"Test League {self.league_ids.get(_parent_league(data), '???')}"
                elif k == "email" and isinstance(v, str):
                    cleaned[k] = "testuser@example.com"
                elif k == "metadata" and isinstance(v, dict):
                    cleaned[k] = self._sanitize_metadata(v)
                elif k in self.AVATAR_KEYS:
                    cleaned[k] = self._scalar(v, self.avatar) if v else v
                else:
                    cleaned[k] = self.sanitize_sleeper(v, ctx=k)
            return cleaned
        if isinstance(data, list):
            return [self.sanitize_sleeper(item, ctx=ctx) for item in data]
        return data

    def sanitize_fleaflicker(self, data: Any, *, ctx: str = "") -> Any:
        """Walk a Fleaflicker JSON response and rewrite identifying scalars.

        ``ctx`` carries the *singular* form of the parent key so we can tell
        what kind of object we're inside. When we descend into a list under
        a plural key (``leagues``, ``rosters``, ``divisions`` ...) we map it
        to the singular (``league``, ``roster``, ``division``) before
        recursing so each child dict knows its role.
        """
        if isinstance(data, dict):
            cleaned: Dict[str, Any] = {}
            for k, v in data.items():
                child_ctx = _FF_PLURAL_TO_SINGULAR.get(k, k)
                # Heuristic: any key suffixed with "Team" or "Teams"
                # (eligibleTeams, opposingTeam, ...) is a team context.
                if child_ctx not in _FF_TEAM_LIKE_CTX:
                    lc = child_ctx.lower()
                    if lc.endswith("teams"):
                        child_ctx = "team"
                    elif lc.endswith("team"):
                        child_ctx = "team"
                    elif lc.endswith("leagues") or lc.endswith("league"):
                        child_ctx = "league"

                # Identifier rewriting based on the *current* dict's role.
                if k == "id" and ctx in _FF_LEAGUE_LIKE_CTX:
                    cleaned[k] = self._scalar_int(v, self.league)
                elif k == "id" and ctx in _FF_TEAM_LIKE_CTX:
                    cleaned[k] = self._scalar_int(v, self.team)
                elif k == "id" and ctx in _FF_USER_LIKE_CTX:
                    cleaned[k] = self._scalar_int(v, self.user)
                elif k == "name" and ctx in _FF_LEAGUE_LIKE_CTX:
                    cleaned[k] = "Test League"
                elif k == "name" and ctx in _FF_TEAM_LIKE_CTX:
                    cleaned[k] = "Test Team"
                elif k in ("displayName", "shortName") and isinstance(v, str):
                    cleaned[k] = "testuser"
                elif k == "email" and isinstance(v, str):
                    cleaned[k] = "testuser@example.com"
                elif k == "emailAddress" and isinstance(v, str):
                    cleaned[k] = "testuser@example.com"
                elif k == "logoUrl" and isinstance(v, str):
                    # The URL embeds the team / league id; drop it entirely
                    # rather than try to surgically rewrite.
                    cleaned[k] = ""
                elif k == "iconUrl" and isinstance(v, str):
                    cleaned[k] = ""
                elif k == "chatChannel" and isinstance(v, str):
                    # Embeds league id in the path, e.g. "/chats/NFL/leagues/140975/all".
                    cleaned[k] = ""
                else:
                    cleaned[k] = self.sanitize_fleaflicker(v, ctx=child_ctx)
            return cleaned
        if isinstance(data, list):
            return [self.sanitize_fleaflicker(item, ctx=ctx) for item in data]
        return data

    # -- helpers -------------------------------------------------------------
    def _scalar(self, v: Any, fn: Callable[[str], str]) -> Any:
        if v is None:
            return None
        return fn(str(v))

    def _scalar_int(self, v: Any, fn: Callable[[str], str]) -> Any:
        """Like _scalar but used for fields that are JSON ints rather than
        strings (Fleaflicker uses ints for IDs). The result is the string
        placeholder — tests don't care about the wire-type since the real
        backend stringifies them anyway."""
        if v is None:
            return None
        return fn(str(v))

    def _is_real_username(self, v: str) -> bool:
        return self.real_username is not None and v.lower() == self.real_username.lower()

    def _sanitize_metadata(self, meta: Dict[str, Any]) -> Dict[str, Any]:
        # Keep structural fields, blank free-text + images.
        scrub_keys = {
            "team_name", "team_name_update", "mascot_name", "mascot_message",
            "description", "user_message_pn", "trade_block",
            "avatar", "team_logo",
        }
        out: Dict[str, Any] = {}
        for k, v in meta.items():
            if k in scrub_keys and isinstance(v, str):
                out[k] = ""
            else:
                out[k] = v
        return out


def _short_hash(s: str) -> str:
    return hashlib.sha1(s.encode()).hexdigest()[:6]


# Map plural Fleaflicker keys to their singular form so list children inherit
# a useful `ctx` hint about what kind of object they are.
_FF_PLURAL_TO_SINGULAR: Dict[str, str] = {
    "leagues": "league",
    "rosters": "roster",
    "groups": "group",
    "divisions": "division",
    "teams": "team",
    "owners": "user",
    "users": "user",
    "players": "player",
    "slots": "slot",
}

_FF_LEAGUE_LIKE_CTX = {"league", "previousLeague", "originalLeague"}
_FF_TEAM_LIKE_CTX = {"team", "ownedTeam", "homeTeam", "awayTeam", "winner",
                     "loser", "opponent", "tradePartner",
                     # matchup wrappers use plain "home"/"away" keys whose
                     # children are full team objects.
                     "home", "away"}
_FF_USER_LIKE_CTX = {"user", "owner", "addedBy", "createdBy"}


def _parent_league(_data: Dict[str, Any]) -> str:
    # Used to derive a name for league objects we encounter while walking;
    # if the dict itself has a league_id, use it. Otherwise return the
    # already-allocated last id (best-effort, only affects display names).
    return _data.get("league_id") or "?"


# ---------------------------------------------------------------------------
# HTTP helper (verbose so user can see what's being captured)
# ---------------------------------------------------------------------------
def _get(url: str) -> Optional[Any]:
    print(f"  GET {url}")
    try:
        resp = requests.get(url, timeout=REQUEST_TIMEOUT)
    except requests.RequestException as e:
        print(f"    !! {type(e).__name__}: {e}")
        return None
    if resp.status_code != 200:
        print(f"    !! HTTP {resp.status_code}")
        return None
    try:
        return resp.json()
    except ValueError as e:
        print(f"    !! invalid JSON: {e}")
        return None
    finally:
        time.sleep(PAUSE_BETWEEN_CALLS)


def _write(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, sort_keys=False)
    print(f"  wrote {path.relative_to(REPO_ROOT)}")


# ---------------------------------------------------------------------------
# Sleeper capture
# ---------------------------------------------------------------------------
def capture_sleeper(username: str, year: str, sanitizer: Sanitizer, write_raw: bool) -> None:
    print(f"\n[sleeper] capturing for {username!r} year={year}")
    sanitizer.real_username = username

    user = _get(f"https://api.sleeper.app/v1/user/{username}")
    if not user:
        print("  !! could not fetch user; aborting sleeper capture")
        return
    user_id = user["user_id"]

    leagues = _get(f"https://api.sleeper.app/v1/user/{user_id}/leagues/nfl/{year}") or []

    # Save user + leagues index.
    _save_pair(SLEEPER_DIR / "user.json", user, sanitizer.sanitize_sleeper, write_raw, sanitizer)
    _save_pair(SLEEPER_DIR / "leagues.json", leagues, sanitizer.sanitize_sleeper, write_raw, sanitizer)

    # Per-league details: settings + rosters. Cap at first 3 leagues to keep
    # fixture size bounded (more would just bloat the repo).
    for i, league in enumerate(leagues[:3], start=1):
        lid = league["league_id"]
        league_settings = _get(f"https://api.sleeper.app/v1/league/{lid}")
        rosters = _get(f"https://api.sleeper.app/v1/league/{lid}/rosters") or []

        # Use sanitized league id as the filename for stable cross-references.
        san_lid = sanitizer.league(lid)
        if league_settings:
            _save_pair(
                SLEEPER_DIR / f"league_{san_lid}.json",
                league_settings,
                sanitizer.sanitize_sleeper,
                write_raw,
                sanitizer,
            )
        _save_pair(
            SLEEPER_DIR / f"rosters_{san_lid}.json",
            rosters,
            sanitizer.sanitize_sleeper,
            write_raw,
            sanitizer,
        )

    # Persist the placeholder map alongside the fixtures so future debugging
    # can correlate (the real-id side is left out of the committed file).
    _write(
        SLEEPER_DIR / "_sanitizer_summary.json",
        {
            "captured_at_utc": datetime.utcnow().isoformat() + "Z",
            "year": year,
            "counts": {
                "users": len(sanitizer.user_ids),
                "leagues": len(sanitizer.league_ids),
                "rosters": len(sanitizer.roster_ids),
            },
        },
    )


# ---------------------------------------------------------------------------
# Fleaflicker capture
# ---------------------------------------------------------------------------
def capture_fleaflicker(email: str, year: str, sanitizer: Sanitizer, write_raw: bool) -> None:
    print(f"\n[fleaflicker] capturing for {email!r} year={year}")
    sanitizer.real_email = email

    base = "https://www.fleaflicker.com/api"
    user_leagues = _get(
        f"{base}/FetchUserLeagues?sport=NFL&season={year}&email={email}"
    )
    if not user_leagues:
        print("  !! could not fetch user leagues; aborting fleaflicker capture")
        return

    _save_pair(
        FF_DIR / "user_leagues.json",
        user_leagues,
        sanitizer.sanitize_fleaflicker,
        write_raw,
        sanitizer,
    )

    leagues = (user_leagues or {}).get("leagues") or []
    for league in leagues[:3]:
        lid = league["id"]
        team_id = league.get("ownedTeam", {}).get("id")
        san_lid = sanitizer.league(str(lid))

        rules = _get(f"{base}/FetchLeagueRules?sport=NFL&league_id={lid}")
        rosters = _get(f"{base}/FetchLeagueRosters?sport=NFL&league_id={lid}")
        if rules:
            _save_pair(
                FF_DIR / f"league_rules_{san_lid}.json",
                rules,
                sanitizer.sanitize_fleaflicker,
                write_raw,
                sanitizer,
            )
        if rosters:
            _save_pair(
                FF_DIR / f"league_rosters_{san_lid}.json",
                rosters,
                sanitizer.sanitize_fleaflicker,
                write_raw,
                sanitizer,
            )
        if team_id is not None:
            user_roster = _get(
                f"{base}/FetchRoster?sport=NFL&league_id={lid}&team_id={team_id}&season={year}"
            )
            if user_roster:
                _save_pair(
                    FF_DIR / f"roster_{san_lid}.json",
                    user_roster,
                    sanitizer.sanitize_fleaflicker,
                    write_raw,
                    sanitizer,
                )

    _write(
        FF_DIR / "_sanitizer_summary.json",
        {
            "captured_at_utc": datetime.utcnow().isoformat() + "Z",
            "year": year,
            "counts": {
                "leagues": len(sanitizer.league_ids),
                "teams": len(sanitizer.team_ids),
                "users": len(sanitizer.user_ids),
            },
        },
    )


# ---------------------------------------------------------------------------
# Save helpers
# ---------------------------------------------------------------------------
def _save_pair(
    path: Path,
    data: Any,
    sanitize_fn: Callable[[Any], Any],
    write_raw: bool,
    sanitizer: Optional["Sanitizer"] = None,
) -> None:
    if write_raw:
        raw_path = path.with_suffix(RAW_SUFFIX)
        _write(raw_path, data)
    cleaned = sanitize_fn(data)
    _write(path, cleaned)
    # Belt-and-suspenders: refuse to write the sanitized file if it still
    # contains the real username/email anywhere, OR any of the numeric ids
    # we already minted placeholders for (catches stuff embedded in URLs /
    # free-text fields the recursive pass missed).
    _assert_clean(path, sanitizer)


_REAL_LEAK_PATTERNS = (
    re.compile(r"jlgorel", re.IGNORECASE),
    re.compile(r"@smcm\.edu", re.IGNORECASE),
)


def _assert_clean(path: Path, sanitizer: Optional["Sanitizer"]) -> None:
    text = path.read_text(encoding="utf-8")
    for pat in _REAL_LEAK_PATTERNS:
        if pat.search(text):
            raise RuntimeError(
                f"Sanitizer left real identifier matching {pat.pattern!r} in {path}; "
                "refusing to commit. Inspect and update Sanitizer."
            )
    if sanitizer is None:
        return
    # Look for any real numeric id we've already allocated a placeholder for
    # — anything still appearing literally is a missed substitution (often
    # embedded in a logoUrl or free-text field). Fail loudly so the
    # sanitizer logic gets fixed instead of silently shipping the leak.
    leaked: List[str] = []
    seen_ids = set(sanitizer.league_ids) | set(sanitizer.team_ids) | set(sanitizer.user_ids)
    for real_id in seen_ids:
        if not real_id or len(real_id) < 5:
            continue  # too short to be a meaningful unique id
        if real_id in text:
            leaked.append(real_id)
    if leaked:
        raise RuntimeError(
            f"Sanitizer left real numeric ids {leaked} in {path}. "
            "Tighten the sanitizer rules (likely a URL or nested field)."
        )


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sleeper-username", default=None)
    parser.add_argument("--fleaflicker-email", default=None)
    parser.add_argument(
        "--year",
        default=None,
        help="NFL season year (defaults to current fantasy year)",
    )
    parser.add_argument(
        "--raw",
        action="store_true",
        help="Also save the un-sanitized responses (gitignored).",
    )
    args = parser.parse_args(argv)

    if not args.sleeper_username and not args.fleaflicker_email:
        parser.error("Must specify at least one of --sleeper-username / --fleaflicker-email")

    year = args.year or _default_year()
    sanitizer = Sanitizer()

    if args.sleeper_username:
        capture_sleeper(args.sleeper_username, year, sanitizer, args.raw)

    if args.fleaflicker_email:
        capture_fleaflicker(args.fleaflicker_email, year, sanitizer, args.raw)

    print("\nDone.")
    print(
        "Review the JSON under tests/fixtures/api/ before committing — the "
        "sanitizer is conservative but not omniscient."
    )
    return 0


def _default_year() -> str:
    """Match shared.fantasy_common.get_current_fantasy_year minus the import dance."""
    today = datetime.utcnow()
    # Fantasy year flips after the Super Bowl (~mid-Feb). Before that the
    # season "belongs" to the previous calendar year.
    return str(today.year if today.month >= 3 else today.year - 1)


if __name__ == "__main__":
    sys.exit(main())
