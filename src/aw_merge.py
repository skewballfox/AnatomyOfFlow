from datetime import datetime, timedelta
import socket
from typing import Callable, Iterator, Dict, List, Optional
from aw_core.models import Event
from aw_client.client import ActivityWatchClient
from pytz import timezone


def afk_gen(
    client: ActivityWatchClient,
    hostname: str,
    start: Optional[datetime] = None,
    end: Optional[datetime] = None,
):
    """Generator for afk events. This generator merges consecutive afk events if they are within 5 minutes of each other
    and returns only the events where the status is "afk". This is done to suppress noise events
    (events created by another watcher while afk).

    Parameters
    ----------
    client : ActivityWatchClient
        ActivityWatchClient instance
    hostname : str
        hostname of the machine
    start : Optional[datetime]
        start of the interval
    end : Optional[datetime]
        end of the interval
    Returns
    -------
    Iterator[Event]
        Iterator over all afk events in the given interval
    """
    raw_afk_events = client.get_events(f"aw-watcher-afk_{hostname}", start=start, end=end)[  # type: ignore
        ::-1
    ]
    i = 1
    current_afk = raw_afk_events[0]
    raw_event_end: datetime
    afk_events: List[Event] = []
    # in order to suppress noise events(events created by another watcher while afk)
    # we merge any consecutive afk events if they are within 5 minutes of each other
    while i < len(raw_afk_events):
        if raw_afk_events[i].data["status"] == current_afk.data["status"]:
            # check if they are within 5 minutes of each other
            if (
                current_afk.timestamp
                + current_afk.duration
                - raw_afk_events[i].timestamp
            ) < timedelta(minutes=8):
                # if they are, merge them
                raw_event_end = raw_afk_events[i].timestamp + raw_afk_events[i].duration
                current_afk.duration = raw_event_end - current_afk.timestamp
            else:
                # otherwise move on
                afk_events.append(current_afk)
                current_afk = raw_afk_events[i]
        else:
            # if the status is different, push the current afk event to the list
            afk_events.append(current_afk)
            current_afk = raw_afk_events[i]
        i += 1

    if current_afk != afk_events[-1]:
        afk_events.append(current_afk)

    yield from [afk for afk in afk_events if afk.data["status"] == "afk"]


def window_gen(
    client: ActivityWatchClient,
    hostname: str,
    start: Optional[datetime] = None,
    end: Optional[datetime] = None,
):
    yield from client.get_events(f"aw-watcher-window_{hostname}", start=start, end=end)[::-1]  # type: ignore


def event_iter(
    client: ActivityWatchClient,
    hostname: str,
    app_map: Dict[str, str | Callable[[datetime, datetime], List[Event]]],
    start: Optional[datetime] = None,
    end: Optional[datetime] = None,
):
    """Returns an iterator over all events in the given interval

    Parameters
    ----------
    client : ActivityWatchClient
        ActivityWatchClient instance
    hostname : str
        hostname of the machine
    app_map : Dict[str,str|Callable]
        mapping of app names to event types

    Yields
    -------
    Iterator[Tuple[str,Event]]
        Iterator over all events in the given interval
    """
    app_id: str
    default_category: str = "window"
    for event in window_gen(client, hostname, start, end):
        if (app_id := event.data["app"]) in app_map:
            app_id = app_id if isinstance(app_id, str) else str(app_id)
            # check if the app is a string
            if isinstance(buckfunc := app_map[app_id], str):
                # if it is, assume it's a bucket name
                yield from [
                    (app_id, e)
                    for e in client.get_events(
                        buckfunc,
                        start=event.timestamp,
                        end=event.timestamp + event.duration,
                    )[::-1]
                ]
            elif callable(buckfunc):
                # otherwise, assume it's a function
                yield from [
                    (app_id, e)
                    for e in buckfunc(event.timestamp, event.timestamp + event.duration)
                ]
            else:
                yield (default_category, event)
        else:
            yield (default_category, event)


def bucket_merge(
    client: ActivityWatchClient,
    hostname: str,
    app_map: Dict[str, str | Callable],
    start: Optional[datetime] = None,
    end: Optional[datetime] = None,
):
    """Merges all buckets in the given interval

    Parameters
    ----------
    client : ActivityWatchClient
        ActivityWatchClient instance
    hostname : str
        hostname of the machine
    app_map : Dict[str,str|Callable]
        mapping of app names to event types
    start : datetime
        start of the interval
    end : datetime
        end of the interval

    Returns
    -------
    Tuple[List[Event],List[str]]
        the merged events and their corresponding categories
    """
    stop_event = Event(timestamp=datetime.now(timezone("UTC")), data={})

    merged_events: List[Event] = []
    merged_categories: List[str] = []
    afk_events = afk_gen(client, hostname, start, end)
    current_afk = next(afk_events, stop_event)

    # flag to indicate whether there are any afk events left
    no_afk = not current_afk.data

    for category, event in event_iter(client, hostname, app_map, start, end):
        if no_afk:
            # if there are no afk events left, we can just yield the current event
            merged_events.append(event)
            merged_categories.append(category)
            continue

        # there are four cases to consider for each window event with respect to the current afk event:
        # 1. event starts before, ends after the current afk event -> split(event) and update(afk)
        # 2. event starts before, ends during the current afk event -> shorten and keep
        # 3. event starts before, ends before the current afk event -> push and keep
        # 4. event starts after, ends after the current afk event -> move/shorten and update
        # 5. event starts after, ends during the current afk event -> ignore and keep
        # first check if the current window event ends after the current afk event
        if (event_end := event.timestamp + event.duration) > (
            afk_end := current_afk.timestamp + current_afk.duration
        ):
            # if the current window event starts before the current afk event
            if event.timestamp < current_afk.timestamp:
                # starts before, ends after
                # split the current window event into two events

                # first event is the part of the window event before the afk event
                event.duration = current_afk.timestamp - event.timestamp
                # second event is the part of the window event after the afk event
                new_event = Event(
                    timestamp=afk_end, duration=event_end - afk_end, data=event.data
                )
                merged_events.extend((event, current_afk, new_event))
                merged_categories.extend((category, "afk", category))

                # update the current afk event
            else:
                # starts after, ends after
                # move the event's timestamp to the end of the afk event
                event.timestamp = current_afk.timestamp + current_afk.duration
                # shorten the event's duration accordingly
                event.duration = event_end - event.timestamp
                # TODO: what if event is affected by the next afk event?
                merged_events.extend((current_afk, event))
                merged_categories.extend(("afk", category))

            # update the current afk event in both cases
            current_afk = next(afk_events, stop_event)
            # check if the current afk event is the stop event
            if not current_afk.data:
                no_afk = True
        # next check if the current event starts prior to the current afk event
        elif event.timestamp < current_afk.timestamp:
            # if it ends during the current afk event, shorten the event's duration
            # note we don't yet push the afk event because it will effect the next event
            if event_end >= current_afk.timestamp:
                # starts before, ends during
                # shorten the event's duration to end at the start of the afk event
                event.duration = current_afk.timestamp - event.timestamp

            # starts before, ends before
            # push the event
            merged_events.append(event)
            merged_categories.append(category)

    return (merged_events, merged_categories)


if __name__ == "__main__":
    from pprint import pprint

    start = (
        (datetime.today() - timedelta(days=1))
        .astimezone(tz=timezone("America/Chicago"))
        .replace(hour=0, minute=0, second=0, microsecond=0)
    )

    app_map: dict[str, str | Callable[[datetime, datetime], List[Event]]] = {
        "google-chrome": "aw-watcher-web-chrome"
    }
    client = ActivityWatchClient()
    hostname = socket.gethostname()
    merged_events, merged_categories = bucket_merge(
        client, hostname, app_map, start=start, end=start + timedelta(days=1)
    )
    # pprint(merged_categories)
    pprint(list(zip(merged_categories, merged_events)))
    print(len(merged_events))
