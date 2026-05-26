from datetime import date

from arcipe.models import Season


def get_season(date: date) -> Season:
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
