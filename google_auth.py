import base64
import json
import os

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials

SCOPES = [
    "https://www.googleapis.com/auth/calendar",
    "https://www.googleapis.com/auth/tasks",
    "https://www.googleapis.com/auth/drive.readonly",
    "https://www.googleapis.com/auth/drive.file",
]


def get_credentials() -> Credentials:
    raw = os.environ.get("GOOGLE_TOKEN_JSON", "")
    if not raw:
        raise EnvironmentError("GOOGLE_TOKEN_JSON environment variable is not set")
    token_data = json.loads(base64.b64decode(raw).decode("utf-8"))
    creds = Credentials(
        token=None,
        refresh_token=token_data["refresh_token"],
        token_uri="https://oauth2.googleapis.com/token",
        client_id=token_data["client_id"],
        client_secret=token_data["client_secret"],
        scopes=SCOPES,
    )
    creds.refresh(Request())
    return creds
