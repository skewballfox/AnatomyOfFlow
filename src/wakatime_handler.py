from rauth import OAuth2Service
from dotenv import load_dotenv

import hashlib
import os
from urllib.parse import parse_qsl
from pathlib import Path
import json


def __create_service(client_id: str, client_secret: str) -> OAuth2Service:
    """internal function to create the OAuth2Service
    the client_id and client_secret should be stored in the wakatime.json file inside the config folder
    """
    service = OAuth2Service(
        client_id=client_id,  # your App ID from https://wakatime.com/apps
        client_secret=client_secret,  # your App Secret from https://wakatime.com/apps
        name="wakatime",
        authorize_url="https://wakatime.com/oauth/authorize",
        access_token_url="https://wakatime.com/oauth/token",
        base_url="https://wakatime.com/api/v1/",
    )
    return service


def __get_initial_token(service: OAuth2Service, state: str, redirect_uri: str):
    """internal function to prompt the user to authorize the app and get the initial token"""
    params = {
        "scope": "read_logged_time,read_stats",
        "response_type": "code",
        "state": state,
        "redirect_uri": redirect_uri,
    }
    print(params)
    url = service.get_authorize_url(**params)

    print("**** Visit this url in your browser ****")
    print("*" * 80)
    print(url)
    print("*" * 80)
    print("**** After clicking Authorize, paste code here and press Enter ****")
    code = input("Enter code from url: ")
    return code


def __get_auth_session(
    service: OAuth2Service, redirect_uri: str, state: str, code: str
):
    """internal function to get the auth session"""
    headers = {"Accept": "application/x-www-form-urlencoded"}

    session = service.get_auth_session(
        headers=headers,
        data={
            "code": code,
            "grant_type": "authorization_code",
            "redirect_uri": redirect_uri,
        },
    )
    return session


def __refresh_token(
    service: OAuth2Service, client_id: str, client_secret: str, refresh_token: str
):
    """internal function to refresh the token if it has been initialized by the user"""
    data = {
        "grant_type": "refresh_token",
        "client_id": client_id,
        "client_secret": client_secret,
        "refresh_token": refresh_token,
    }
    return service.get_auth_session(data=data)


def initialize_session(waka_config_path: Path, state: str):
    """Initializes the wakatime session. On first run, it will ask the user to authorize the app.
    On subsequent runs, it will refresh the token which was created on the first run.
    Args:
        waka_config_path (Path): Path to the wakatime.json file
        state (str): state to be used for the OAuth2Service, can be any string, but should be random
    Returns:
        OAuth2Session: the session to be used for the wakatime api
    """
    waka_config = json.loads((waka_config_path).read_text())
    print(waka_config["redirect_uri"])
    service = __create_service(waka_config["app_id"], waka_config["app_secret"])
    # Check if refresh token exists
    if "refresh_token" in waka_config.keys():
        # create session with refresh token
        return __refresh_token(
            service,
            waka_config["app_id"],
            waka_config["app_secret"],
            waka_config["refresh_token"],
        )

    code = __get_initial_token(service, state, waka_config["redirect_uri"])
    session = __get_auth_session(service, waka_config["redirect_uri"], state, code)
    # Extract refresh token from session
    waka_config["refresh_token"] = dict(parse_qsl(session.access_token_response.text))[
        "refresh_token"
    ]
    # Save refresh token to config
    (waka_config_path).write_text(json.dumps(waka_config))
    # Return session
    return session
