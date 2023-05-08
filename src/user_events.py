from datetime import datetime, timedelta
from enum import Enum
from typing import Dict, List, Optional, Tuple
from aw_core import Event
from aw_client.client import ActivityWatchClient
from src.atuin_handler import get_history_interval

event_schema = {"Category": [], "Start": [], "End": [], "Data": []}

import polars as pl


class WindowSession(Enum):
    """Enum for user activity"""

    AFK = 0
    CODING = 1
    BROWSER = 2
    TERMINAL = 3
    OTHER = 4


terminals = {
    "kitty",
    "alacritty",
    "gnome-terminal",
    "konsole",
    "terminator",
    "tilix",
    "xfce4-terminal",
    "xterm",
}


def parse_terminal_history_interval(start: datetime, end: datetime):
    res = {}
    if commands := get_history_interval(start, end):
        res |= event_schema
        res["Category"].extend(["Terminal"] * len(commands))
        for i in range(len(commands)):
            res["Start"].append(commands[i][0])
            res["End"].append(commands[i][0])
            res["Data"].append(commands[i][1])
    return res


def parse_vscode_event(event: Event):
    res = {} | event_schema
    res["Category"].append("Coding")
    start = event.timestamp
    end = start + event.duration
    res["Start"].append(start)
    res["End"].append(end)

    # title is the file in focus, has a ● in front of it if it's unsaved
    file_being_edited = event.data["title"].removeprefix("●")
    res["Data"].append(file_being_edited)

    if commands := parse_terminal_history_interval(start, end):
        res |= commands
    return res


def parse_chrome_session(aw_client: ActivityWatchClient, web_event: Event):
    res = {} | event_schema

    start = web_event.timestamp
    end = start + web_event.duration
    aw_web_events = aw_client.get_events("aw-watcher-web-chrome", start=start, end=end)[
        ::-1
    ]
    for web_event in aw_web_events:
        if web_event.data["title"].endswith(" - Google Search"):
            query = web_event.data["title"].removesuffix(" - Google Search")
            categorize_general(res, "Search", web_event, query)
        else:
            page_title = web_event.data["title"]
            categorize_general(res, "Browsing", web_event, page_title)
    return res


def build_event_df(
    aw_client: ActivityWatchClient,
    hostname: str,
    start_date: datetime = datetime.now() - timedelta(days=14),
    end_date: datetime = datetime.now(),
) -> Dict[str, pl.DataFrame]:
    """Builds a dataframe of user events from the given interval
    Args:
        aw_client (ActivityWatchClient):
            the ActivityWatch client to use
        hostname (str):
            hostname of the machine
        start_date (datetime):
            start of the interval, defaults to 2 weeks ago
        end (datetime):
            end of the interval, defaults to now
    Returns:
        pl.DataFrame: dataframe of user events

    Note:
        the default arguments for start and end are defined *once* on the
        initial function call
    """
    res = {} | event_schema
    terminals = {"kitty"}
    browsers = {"google-chrome"}
    social_apps = {"Ripcord", "Signal"}

    # each day starts at 6am, though the first couple of hours
    # will likely be afk
    dates = [
        (start_date + timedelta(n))
        .replace(hour=6, minute=0, second=0, microsecond=0)
        .astimezone()
        for n in range(int((end_date - start_date).days))
    ]
    workday_data = {}
    for date in dates:
        res = {} | event_schema
        end_of_day = date.replace(hour=17).astimezone()

        # get the events between 6am and 5pm
        events = aw_client.get_events(
            f"aw-watcher-window_{hostname}", start=date, end=end_of_day
        )[
            ::-1
        ]  # the returned events are in reverse order

        for event in events:
            if (app_id := event.data["app"]) == "code-url-handler":
                res |= parse_vscode_event(event)
            elif app_id in browsers:
                res.update(parse_chrome_session(aw_client, event))
            elif app_id in terminals:
                res.update(
                    parse_terminal_history_interval(
                        event.timestamp, event.timestamp + event.duration
                    )
                )
            elif app_id in social_apps:
                categorize_general(res, "Social", event, app_id)
            else:
                categorize_general(res, "Uncategorized", event, app_id)
        if not (data := pl.DataFrame(res)).is_empty():
            data.select(
                pl.col(["Start", "End"]).cast(pl.Datetime).dt.replace_time_zone(None)
            )
            workday_data[date.strftime("%a %Y-%m-%d")] = pl.DataFrame(res)
    return workday_data


def get_bounds(
    client: ActivityWatchClient, hostname: str, start: datetime, end: datetime
) -> Tuple[datetime, datetime]:
    afk_events = client.get_events(f"aw-watcher-afk_{hostname}", start=start, end=end)
    new_start = start
    new_end = end
    for event in afk_events[::-1]:
        if event.data["status"] == "not-afk":
            new_start = event.timestamp
            break
    for event in afk_events:
        if event.data["status"] == "not-afk":
            new_end = event.timestamp
            break
    return new_start, new_end


# TODO Rename this here and in `build_event_df`
def categorize_general(res, arg1, event, app_id):
    res["Category"].append(arg1)
    res["Start"].append(event.timestamp)
    res["End"].append(event.timestamp + event.duration)
    res["Data"].append(app_id)
