from datetime import datetime
import requests

url = "https://api.ouraring.com/v2/usercollection/heartrate"


def get_heartrate_data(access_token: str, start: datetime, end: datetime):
    headers = {"Authorization": f"Bearer {access_token}"}
    params = {
        "start_datetime": start.isoformat(),
        "end_datetime": end.isoformat(),
    }
    response = requests.request("GET", url, headers=headers, params=params)
    return response.json() if response.ok else None
