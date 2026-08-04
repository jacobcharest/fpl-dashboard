SEASONS = [
    {"id": "2016-17", "label": "2016/17", "start_date": "2016-08-01", "end_date": "2017-05-31"},
    {"id": "2017-18", "label": "2017/18", "start_date": "2017-08-01", "end_date": "2018-05-31"},
    {"id": "2018-19", "label": "2018/19", "start_date": "2018-08-01", "end_date": "2019-05-31"},
    {"id": "2019-20", "label": "2019/20", "start_date": "2019-08-01", "end_date": "2020-07-31"},
    {"id": "2020-21", "label": "2020/21", "start_date": "2020-09-01", "end_date": "2021-05-31"},
    {"id": "2021-22", "label": "2021/22", "start_date": "2021-08-01", "end_date": "2022-05-31"},
    {"id": "2022-23", "label": "2022/23", "start_date": "2022-08-01", "end_date": "2023-05-31"},
    {"id": "2023-24", "label": "2023/24", "start_date": "2023-08-01", "end_date": "2024-05-31"},
    {"id": "2024-25", "label": "2024/25", "start_date": "2024-08-01", "end_date": "2025-05-31"},
    {"id": "2025-26", "label": "2025/26", "start_date": "2025-08-01", "end_date": "2026-05-31"},
]

# Stat families that don't exist for every season (see DESIGN.md for source verification).
XG_FAMILY_FROM = "2022-23"
DEFENSIVE_CONTRIBUTION_FROM = "2025-26"

POSITION_BY_ELEMENT_TYPE = {1: "GK", 2: "DEF", 3: "MID", 4: "FWD"}

# Lowest possible starting price per position, used by the "value added" derived stat.
BASE_PRICE = {"GK": 4.0, "DEF": 4.0, "MID": 4.5, "FWD": 4.5}
