import os
from datetime import date
from typing import cast
from typing import get_args

from arcipe.models import Season


def get_season(date: date) -> Season:
    season_override = os.environ.get("ARCIPE_SEASON")
    if season_override and season_override in get_args(Season):
        return cast(Season, season_override)
    m = date.month
    x = m % 12 // 3 + 1

    assert x in (1, 2, 3, 4), (
        "Great, you broke the international date system. Please do not use a computer again."
    )
    if x == 1:
        return "winter"
    if x == 2:
        return "spring"
    if x == 3:
        return "summer"
    else:
        return "autumn"
