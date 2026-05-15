"""Pull Bijan Robinson value history from the saved KTC page (single player).

Server-rendered JS vars in the HTML:
  var player           bio only (no history)
  var playerOneQB      .overallValue + rank histories for THIS player (1QB)
  var playerSuperflex  same shape for superflex
"""
from __future__ import annotations
import datetime as _dt, json, re
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
PROBE = REPO / "tools" / "tests" / "fixtures" / "scraper" / "ktc_player_probe"
PAGE = PROBE / "page.html"
NET = PROBE / "network_ranked.json"
OUT = PROBE / "extracted"; OUT.mkdir(parents=True, exist_ok=True)

SLUG = "bijan-robinson-1414"
PID = 1414


def slice_var_object(html: str, name: str) -> str:
    needle = f"var {name} = "
    i = html.find(needle)
    if i < 0:
        raise ValueError(f"{name!r} not found")
    i += len(needle)
    if html[i] != "{":
        raise ValueError(f"unexpected start {html[i]!r}")
    depth, j = 0, i
    in_str = esc = False
    while j < len(html):
        c = html[j]
        if in_str:
            if esc: esc = False
            elif c == "\\": esc = True
            elif c == '"': in_str = False
        else:
            if c == '"': in_str = True
            elif c == "{": depth += 1
            elif c == "}":
                depth -= 1
                if depth == 0:
                    return html[i:j+1]
        j += 1
    raise ValueError("unterminated")


def yymmdd(s): return _dt.date(2000+int(s[:2]), int(s[2:4]), int(s[4:6])).isoformat()
def series(arr): return [{"date": yymmdd(e["d"]), "value": e["v"]} for e in arr if isinstance(e, dict) and "d" in e and "v" in e]


def main() -> None:
    html = PAGE.read_text(encoding="utf-8")
    print(f"page.html = {len(html):,} bytes")

    bio = json.loads(slice_var_object(html, "player"))
    assert bio["slug"] == SLUG and bio["playerID"] == PID
    print(f"bio: {bio['playerName']} id={bio['playerID']} pos={bio['position']} team={bio['team']} age={bio['age']}")

    out = {"slug": SLUG, "playerID": PID, "playerName": bio["playerName"], "formats": {}}
    for fmt, var in [("oneQB", "playerOneQB"), ("superflex", "playerSuperflex")]:
        obj = json.loads(slice_var_object(html, var))
        ids = [p.get("playerID") for p in obj.get("adjacentOverallPlayers", [])]
        assert PID in ids, f"{var}: Bijan (1414) not in adjacent list {ids}"
        vh = series(obj["overallValue"])
        rh = series(obj["overallRankHistory"])
        ph = series(obj["positionalRankHistory"])
        out["formats"][fmt] = {
            "n": len(vh),
            "first_date": vh[0]["date"] if vh else None,
            "last_date": vh[-1]["date"] if vh else None,
            "current_value": vh[-1]["value"] if vh else None,
            "value_history": vh,
            "overall_rank_history": rh,
            "positional_rank_history": ph,
        }
        print(f"  {fmt:9s}: {len(vh):4d} days  {vh[0]['date']} -> {vh[-1]['date']}  current={vh[-1]['value']}")

    p = OUT / "bijan_robinson_history.json"
    p.write_text(json.dumps(out, indent=2), encoding="utf-8")
    print(f"Wrote {p} ({p.stat().st_size:,} bytes)")

    print("\n=== API check ===")
    if not NET.exists():
        print("  network_ranked.json missing"); return
    net = json.loads(NET.read_text(encoding="utf-8"))
    if isinstance(net, dict) and "responses" in net:
        net = net["responses"]
    calls = []
    for e in net:
        url = (e.get("url") or "") if isinstance(e, dict) else ""
        if "keeptradecut.com" not in url: continue
        if url.rstrip("/").endswith(SLUG): continue
        if any(url.endswith(x) for x in (".js", ".css", ".woff2", ".woff", ".png", ".jpg", ".svg", ".ico")): continue
        calls.append(url)
    print(f"  {len(calls)} non-asset KTC calls (page excluded):")
    for u in calls[:20]:
        print(f"    {u}")
    hist = [u for u in calls if re.search(r"(history|chart|value|graph|series)", u, re.I)]
    msg = "NONE - data is fully server-rendered into the HTML." if not hist else str(hist)
    print(f"  History-shaped API calls: {len(hist)}  => {msg}")


if __name__ == "__main__":
    main()