"""Boris Chen tier loading + league-settings -> tier-page-name resolution."""
from __future__ import annotations

from collections import defaultdict
from typing import Dict, Tuple

from app.services.blob_store import load_blob


def prepare_boris_chen_tier_dict() -> Dict[str, Dict[str, str]]:
    """Return ``{player_name: {position_page: tier_number}}``.

    Adds a shortened-name alias (first two tokens) for players whose names
    include a generational suffix (Jr./Sr./II/III) so name lookups still work
    when projection sources drop the suffix.
    """
    data = load_blob("borischen_tiers.json")
    player_tiers: Dict[str, Dict[str, str]] = defaultdict(dict)
    for pos_ranking, tiers in data.items():
        for tier_num, names in tiers.items():
            for name in names:
                if len(name.split()) >= 3:
                    if any(suffix in name for suffix in ("Sr.", "Jr.", "III", "II")):
                        shortened_name = " ".join(name.split()[:2])
                        player_tiers[shortened_name][pos_ranking] = tier_num
                player_tiers[name][pos_ranking] = tier_num
    return player_tiers


def get_tier_page_names_from_league_settings(settings: Dict[str, float]) -> Tuple[str, str]:
    """Map league scoring settings to the right Boris Chen page prefixes.

    Non-standard TE Premium settings get rounded to either 0.5 PPR or full PPR.
    Returns ``(rb_wr_flex_prefix, te_prefix)``.
    """
    ppr = settings["rec"]
    te_ppr = ppr + settings["bonus_rec_te"] if "bonus_rec_te" in settings else ppr

    if ppr == 0:
        rb_wr_flex_prefix = ""
    elif ppr == 0.5:
        rb_wr_flex_prefix = "0.5 PPR "
    else:  # ppr >= 1
        rb_wr_flex_prefix = "PPR "

    if te_ppr < 0.25:
        te_prefix = ""
    elif te_ppr < 0.75:
        te_prefix = "0.5 PPR "
    else:
        te_prefix = "PPR "

    return rb_wr_flex_prefix, te_prefix
