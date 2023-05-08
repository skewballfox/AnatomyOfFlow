from typing import Dict, List, Optional, Tuple
import os
import hashlib
from datetime import datetime
from subprocess import run

atuin_env: Dict[str, str] = os.environ | {
    "ATUIN_SESSION": str(hashlib.sha1(os.urandom(40)).hexdigest())
}
command_format = "{time}@@@{command}@@@{directory}"


def get_history_interval(start: datetime, end: datetime):
    """runs atuin search for the given interval

    Parameters
    ----------
    start : datetime
        start of the interval
    end : datetime
        end of the interval
    Returns
    -------
    List[Tuple[datetime, str, str]]
        list of tuples containing the time, command and directory of the command
    """
    result: List[Tuple[datetime, str, str]] = []
    if command_interval := run(
        f"atuin search --after {start.isoformat()} --before {end.isoformat()} --format '{command_format}'",
        env=atuin_env,
        capture_output=True,
        text=True,
        shell=True,
    ).stdout:
        for command in command_interval.split("\n"):
            if command:
                # print(f"command: {command}")
                (command_time, command, directory) = command.split("@@@")
                result.append(
                    (datetime.fromisoformat(command_time), command, directory)
                )
    return result
