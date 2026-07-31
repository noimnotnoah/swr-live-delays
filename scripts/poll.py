#!/usr/bin/env python3
"""
Polls live South Western Railway departure data (via the public Huxley2
wrapper around National Rail's Darwin LDBWS feed) from a curated set of
major SWR hub stations, and maintains a running daily tally of on-time /
delayed / cancelled services in data/state.json.

Departure boards only show a rolling window of upcoming trains, not a
full-day log -- so this script runs on a schedule (see
.github/workflows/poll.yml), de-duplicates services by their unique
serviceID so each train is only counted once, and resets the tally at
UK midnight (Europe/London, so it follows BST/GMT correctly).
"""

import json
import urllib.request
import urllib.error
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

STATE_PATH = Path(__file__).resolve().parent.parent / "data" / "state.json"
HISTORY_PATH = Path(__file__).resolve().parent.parent / "data" / "history.json"
UK_TZ = ZoneInfo("Europe/London")
HUXLEY_BASE = "https://huxley2.azurewebsites.net"
NUM_ROWS = 50

# Major South Western Railway hub/interchange stations. Most SWR services
# pass through at least one of these, so polling this set gives broad
# (not literally exhaustive) coverage of the network -- there is no public
# "every train on the whole network today" endpoint to query instead.
STATIONS = [
    "WAT",  # London Waterloo
    "CLJ",  # Clapham Junction
    "WOK",  # Woking
    "BSK",  # Basingstoke
    "SOU",  # Southampton Central
    "PMS",  # Portsmouth Harbour
    "RDG",  # Reading
    "GLD",  # Guildford
    "WEY",  # Weymouth
    "EXD",  # Exeter St Davids
]

SW_OPERATOR_CODE = "SW"


def fetch_station(crs):
    url = f"{HUXLEY_BASE}/departures/{crs}/{NUM_ROWS}"
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=25) as resp:
            return json.load(resp)
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as e:
        print(f"  warning: failed to fetch {crs}: {e}")
        return None


def classify(service):
    if service.get("isCancelled"):
        return "cancelled"
    if service.get("etd") == "On time":
        return "onTime"
    return "delayed"


def load_state():
    if STATE_PATH.exists():
        with open(STATE_PATH) as f:
            return json.load(f)
    return None


def fresh_state(today):
    return {
        "date": today,
        "onTime": 0,
        "delayed": 0,
        "cancelled": 0,
        "seenServiceIds": [],
        "lastUpdated": None,
        "stationsMonitored": STATIONS,
    }


def archive_day(old_state):
    """Append the final tally of a finished day to the ever-growing history log."""
    total = old_state["onTime"] + old_state["delayed"] + old_state["cancelled"]
    if total == 0:
        return

    history = []
    if HISTORY_PATH.exists():
        with open(HISTORY_PATH) as f:
            history = json.load(f)

    disrupted = old_state["delayed"] + old_state["cancelled"]
    entry = {
        "date": old_state["date"],
        "onTime": old_state["onTime"],
        "delayed": old_state["delayed"],
        "cancelled": old_state["cancelled"],
        "total": total,
        "pctOnTime": round((old_state["onTime"] / total) * 100),
        "pctDisrupted": round((disrupted / total) * 100),
    }

    history = [h for h in history if h["date"] != entry["date"]]
    history.append(entry)

    HISTORY_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(HISTORY_PATH, "w") as f:
        json.dump(history, f, indent=2)

    print(f"Archived {entry['date']}: {entry['pctOnTime']}% on time, {entry['pctDisrupted']}% delayed/cancelled ({total} services).")


def main():
    now_uk = datetime.now(UK_TZ)
    today = now_uk.strftime("%Y-%m-%d")

    state = load_state()
    if state is None or state.get("date") != today:
        if state is not None:
            archive_day(state)
        print(f"New day ({today}) -- resetting tally.")
        state = fresh_state(today)

    seen = set(state["seenServiceIds"])
    new_count = 0

    for crs in STATIONS:
        data = fetch_station(crs)
        if not data:
            continue
        services = data.get("trainServices") or []
        for svc in services:
            if svc.get("operatorCode") != SW_OPERATOR_CODE:
                continue
            service_id = svc.get("serviceID")
            if not service_id or service_id in seen:
                continue
            seen.add(service_id)
            new_count += 1
            status = classify(svc)
            state[status] += 1

    state["seenServiceIds"] = sorted(seen)
    state["lastUpdated"] = now_uk.isoformat()

    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(STATE_PATH, "w") as f:
        json.dump(state, f, indent=2)

    print(
        f"Done. {new_count} new services this run. "
        f"Totals -- onTime: {state['onTime']}, delayed: {state['delayed']}, cancelled: {state['cancelled']}"
    )


if __name__ == "__main__":
    main()
