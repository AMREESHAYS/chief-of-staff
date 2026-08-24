"""Gmail OAuth. Read + draft only.

There is no draft-only Gmail scope: gmail.compose permits sending. The
send-prevention guarantee is enforced in application code (no send path exists)
and every action is written to the `action` audit table. Do not add
gmail.send here.
"""
import os.path

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.compose",
]

# seeding the demo account writes messages directly without sending them
SEED_SCOPES = SCOPES + ["https://www.googleapis.com/auth/gmail.insert"]

CREDENTIALS = "credentials.json"
TOKEN = "token.json"


def gmail(scopes=SCOPES, token_path=TOKEN):
    creds = None
    if os.path.exists(token_path):
        creds = Credentials.from_authorized_user_file(token_path, scopes)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            creds = InstalledAppFlow.from_client_secrets_file(
                CREDENTIALS, scopes
            ).run_local_server(port=0)
        with open(token_path, "w") as f:
            f.write(creds.to_json())
    return build("gmail", "v1", credentials=creds)


if __name__ == "__main__":
    profile = gmail().users().getProfile(userId="me").execute()
    print(f"authorized as {profile['emailAddress']}")
