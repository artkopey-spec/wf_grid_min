from __future__ import annotations

from collections import Counter, defaultdict
from datetime import time

from analyze_st_movements_roll_adjusted import read_bars, read_flips, read_rollovers


THRESHOLD_PCT = 0.5


def bucket(value: time) -> str:
    if value < time(12, 0):
        return "Утро до 12"
    if value < time(16, 0):
        return "День 12–16"
    if value < time(19, 0):
        return "Вечер 16–19"
    return "После 19"


def main() -> None:
    bars = read_bars()
    flips = read_flips()
    rollovers = read_rollovers(bars)

    offset = 0.0
    rollover_cursor = 0
    for bar in bars:
        while (
            rollover_cursor < len(rollovers)
            and rollovers[rollover_cursor]["effective_index"] == bar["index"]
        ):
            offset += rollovers[rollover_cursor]["offset_delta"]
            rollover_cursor += 1
        bar["offset"] = offset
        bar["adjusted_high"] = bar["high"] + offset
        bar["adjusted_low"] = bar["low"] + offset
        bar["adjusted_close"] = bar["close"] + offset

    events = []
    for start, end in zip(flips, flips[1:]):
        start_bar = bars[start["bar"]]
        adjusted_start = start_bar["adjusted_close"]
        first_cross = None
        for current in bars[start["bar"] + 1 : end["bar"] + 1]:
            if start["after"] == "GREEN":
                movement_pct = (
                    current["adjusted_high"] - adjusted_start
                ) / start["price"] * 100
            else:
                movement_pct = (
                    adjusted_start - current["adjusted_low"]
                ) / start["price"] * 100
            if movement_pct > THRESHOLD_PCT:
                first_cross = current["time"]
                break
        if first_cross is not None:
            events.append(first_cross)

    trading_days = {bar["time"].date() for bar in bars}
    trading_days_by_weekday = Counter(day.weekday() for day in trading_days)
    bucket_events = Counter(bucket(event.time()) for event in events)
    bucket_days = defaultdict(set)
    weekday_events = Counter()
    for event in events:
        bucket_days[bucket(event.time())].add(event.date())
        weekday_events[event.weekday()] += 1

    bar_exposure = Counter(bucket(bar["time"].time()) for bar in bars)
    print("events", len(events))
    print("TIME_BUCKETS")
    for name in ("Утро до 12", "День 12–16", "Вечер 16–19", "После 19"):
        count = bucket_events[name]
        print(
            name,
            "events", count,
            "share_all_pct", count / len(events) * 100,
            "days", len(bucket_days[name]),
            "days_share_pct", len(bucket_days[name]) / len(trading_days) * 100,
            "per_100_trading_hours", count / (bar_exposure[name] / 60) * 100,
        )
    print("WEEKDAYS")
    names = ["Понедельник", "Вторник", "Среда", "Четверг", "Пятница"]
    for index, name in enumerate(names):
        print(
            name,
            "events", weekday_events[index],
            "trading_days", trading_days_by_weekday[index],
            "per_day", weekday_events[index] / trading_days_by_weekday[index],
            "share_all_pct", weekday_events[index] / len(events) * 100,
        )

    requested = Counter()
    requested_days = defaultdict(set)
    requested_exposure = Counter()
    for event in events:
        event_time = event.time()
        name = (
            "09–13"
            if time(9, 0) <= event_time < time(13, 0)
            else "13–19"
            if time(13, 0) <= event_time < time(19, 0)
            else "Вне 09–19"
        )
        requested[name] += 1
        requested_days[name].add(event.date())
    for bar in bars:
        bar_time = bar["time"].time()
        name = (
            "09–13"
            if time(9, 0) <= bar_time < time(13, 0)
            else "13–19"
            if time(13, 0) <= bar_time < time(19, 0)
            else "Вне 09–19"
        )
        requested_exposure[name] += 1
    print("REQUESTED_WINDOWS")
    in_window_total = requested["09–13"] + requested["13–19"]
    for name in ("09–13", "13–19", "Вне 09–19"):
        print(
            name,
            "events", requested[name],
            "share_all_pct", requested[name] / len(events) * 100,
            "share_in_09_19_pct", (
                requested[name] / in_window_total * 100 if name != "Вне 09–19" else None
            ),
            "event_days", len(requested_days[name]),
            "per_100_trading_hours", requested[name] / (requested_exposure[name] / 60) * 100,
        )


if __name__ == "__main__":
    main()
