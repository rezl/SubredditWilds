from __future__ import print_function

import gc
import traceback
import os.path
import time
from datetime import datetime, timezone

from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from settings import Settings


class MonitoredSubreddit:
    def __init__(self, subreddit_name, sheet_id, sheet_name):
        self.subreddit_name = subreddit_name
        self.sheet_id = sheet_id
        self.sheet_name = sheet_name


class GoogleSheetsRecorder:
    SCOPES = ['https://www.googleapis.com/auth/spreadsheets']

    def __init__(self, discord_client):
        self.discord_client = discord_client
        self.creds = self.get_credentials()
        self.service = build('sheets', 'v4', credentials=self.creds)
        self.startup_timestamp = datetime.now(timezone.utc).timestamp()
        self.monitored_subs = {}
        
        # force gc to clean up response objects
        gc.collect()

    def add_sheet_for_sub(self, subreddit_name, sheet_id, sheet_name):
        print(f"Adding google sheet recording for {subreddit_name}")
        monitored_sub = MonitoredSubreddit(subreddit_name, sheet_id, sheet_name)
        self.monitored_subs[subreddit_name.lower()] = monitored_sub

    def append_to_sheet(self, subreddit_name, created_utc, mod_name, action, link, details, automod_report):
        subreddit_name = subreddit_name.lower()
        if subreddit_name not in self.monitored_subs:
            print(f"Ignoring mod action as unmonitored sub: {subreddit_name}")
            return
        monitored_sub = self.monitored_subs[subreddit_name]
        sheet_id = monitored_sub.sheet_id
        sheet_name = monitored_sub.sheet_name

        # this is required on startup to prevent re-actioning startup stream
        if created_utc <= self.startup_timestamp:
            return

        dt_utc = datetime.utcfromtimestamp(created_utc)
        formatted_dt = dt_utc.isoformat().replace('T', ' ')
        values = [[formatted_dt, mod_name, action, link, details, automod_report]]

        self.append_to_sheet_helper(sheet_id, sheet_name, values)
        
        # force gc to clean up response objects
        gc.collect()

    def append_to_sheet_helper(self, sheet_id, sheet_name, values):
        print(f'Adding to google sheet for {str(values)}')
        if Settings.is_dry_run:
            print("\tDRY RUN!!!")
            return

        if self.creds.expired:
            self.creds.refresh(Request())

        message = ""
        max_retries = 4
        initial_backoff_time_secs = 5
        for i in range(max_retries):
            try:
                request_range = f'{sheet_name}!A:E'
                request_body = {
                    'range': request_range,
                    'values': values,
                    'majorDimension': 'ROWS'
                }
                self.service.spreadsheets().values().append(
                    spreadsheetId=sheet_id,
                    range=request_range,
                    valueInputOption='USER_ENTERED',
                    body=request_body).execute()
                return
            except HttpError as error:
                message = f'Google API exception for {str(values)}: {str(error)}\n```{traceback.format_exc()}```'
                print(message)

                if error.resp.status == 401:
                    print(f'The credentials have been revoked or expired, refreshing again?')
                    self.creds.refresh(Request())

                backoff_time_secs = initial_backoff_time_secs ** i
                print(f'Retrying in {backoff_time_secs} seconds...')
                time.sleep(backoff_time_secs)

        self.discord_client.send_error_msg(message)
        print(f'Failed to update google sheets after {max_retries} retries.')

    def get_credentials(self):
        # if env var set, assume this is a bot, otherwise authenticate user
        if 'GOOGLE_APPLICATION_CREDENTIALS' in os.environ:
            from google.oauth2 import service_account
            import base64
            import json
            # bot credentials is stored as base64 string so it can be provided to fly as a secret
            credentials_base64 = os.environ['GOOGLE_APPLICATION_CREDENTIALS']
            # decode the base64 string to bytes
            credentials_bytes = base64.b64decode(credentials_base64)
            # convert the bytes to a JSON string
            credentials_json = credentials_bytes.decode('utf-8')
            # parse the JSON string into a Python dictionary
            credentials_dict = json.loads(credentials_json)
            creds = service_account.Credentials.from_service_account_info(credentials_dict, scopes=self.SCOPES)
        else:
            from google_auth_oauthlib.flow import InstalledAppFlow
            flow = InstalledAppFlow.from_client_secrets_file('credentials-user.json', self.SCOPES)
            creds = flow.run_local_server(port=0)

        creds.refresh(Request())
        return creds
