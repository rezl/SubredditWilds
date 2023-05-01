from __future__ import print_function

import base64
import gc
import json
import traceback
import os.path
import time
from datetime import datetime, timezone

from google.auth.transport.requests import Request
from google.oauth2 import service_account
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from settings import Settings


class GoogleSheetsRecorder:
    SCOPES = ['https://www.googleapis.com/auth/spreadsheets']

    def __init__(self, discord_client, sheet_id, sheet_name):
        self.discord_client = discord_client
        self.sheet_id = sheet_id
        self.sheet_name = sheet_name
        self.creds = None
        self.creds = self.get_credentials()
        self.last_timestamp = self.find_last_timestamp()
        # force gc to clean up response objects
        gc.collect()

    def find_last_timestamp(self):
        try:
            service = build('sheets', 'v4', credentials=self.creds)

            sheet_metadatas = service.spreadsheets().get(spreadsheetId=self.sheet_id).execute()
            end = 40000
            for sheet_metadata in sheet_metadatas['sheets']:
                if sheet_metadata['properties']['title'] == self.sheet_name:
                    end = sheet_metadata['properties']['gridProperties']['rowCount']
                    break

            # Use binary search to find last row with info in it, google sheets holds 40k entries
            # this prevents up to 40k entries returned on init
            start = 1
            result = None
            sheet_values = service.spreadsheets().values()
            while start <= end:
                mid = (start + end) // 2
                range_name = f'{self.sheet_name}!A{mid + 1}:A{mid + 1}'
                row = sheet_values.get(spreadsheetId=self.sheet_id, range=range_name).execute().get('values', [[]])[0]
                if row and row[0]:
                    start = mid + 1
                    result = row
                else:
                    end = mid - 1

            if result:
                # assume google sheet in utc iso format (with ' ' instead of 'T')
                formatted_utc_datetime = result[0]
                iso_formatted_utc_dt = datetime.fromisoformat(formatted_utc_datetime.replace(' ', 'T'))
                utc_datetime = iso_formatted_utc_dt.replace(tzinfo=timezone.utc)
                return utc_datetime.timestamp()
            return 0
        except (HttpError, ValueError) as error:
            message = f'Google exception in setup: {str(error)}. ' \
                      f'Initiating with current time. Potentially missed mod actions.' \
                      f'\n```{traceback.format_exc()}```'
            self.discord_client.send_error_msg(message)
            print(message)
            return time.time()

    def append_to_sheet(self, values):
        print(f'Adding to google sheet for {str(values)}')
        self.append_to_sheet_helper(values)
        # force gc to clean up response objects
        gc.collect()

    def append_to_sheet_helper(self, values):
        if Settings.is_dry_run:
            print("\tDRY RUN!!!")
            return

        max_retries = 3
        initial_backoff_time_secs = 5

        for i in range(max_retries):
            try:
                creds = self.get_credentials()
                service = build('sheets', 'v4', credentials=creds)
                request_range = f'{self.sheet_name}!A:E'
                request_body = {
                    'range': request_range,
                    'values': values,
                    'majorDimension': 'ROWS'
                }
                result = service.spreadsheets().values().append(
                    spreadsheetId=self.sheet_id,
                    range=request_range,
                    valueInputOption='USER_ENTERED',
                    body=request_body).execute()
                print(f'{result.get("updates").get("updatedCells")} cells appended for {str(values)}')
                return
            except HttpError as error:
                message = f'Google API exception for {str(values)}: {str(error)}\n```{traceback.format_exc()}```'
                self.discord_client.send_error_msg(message)
                print(message)

                if error.resp.status == 401:
                    # The credentials have been revoked or expired
                    print(f'The credentials have been revoked or expired, invalidating')
                    self.creds = None

                backoff_time_secs = initial_backoff_time_secs ** i
                print(f'Retrying in {backoff_time_secs} seconds...')
                time.sleep(backoff_time_secs)

        print(f'Failed to update google sheets after {max_retries} retries.')

    def get_credentials(self):
        # if creds already exists, refresh if needed
        if self.creds:
            if self.creds.valid:
                return self.creds
            else:
                self.creds.refresh(Request())
                return self.creds

        # first time initialization - if env var set, assume this is a bot, otherwise authenticate user
        if 'GOOGLE_APPLICATION_CREDENTIALS' in os.environ:
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
            flow = InstalledAppFlow.from_client_secrets_file('credentials-user.json', self.SCOPES)
            creds = flow.run_local_server(port=0)

        creds.refresh(Request())
        self.creds = creds
        return creds
