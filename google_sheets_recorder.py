from __future__ import print_function

import base64
import gc
import json
from datetime import datetime
import os.path
import time

from google.auth.transport.requests import Request
from google.oauth2 import service_account
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from settings import Settings


class GoogleSheetsRecorder:
    SCOPES = ['https://www.googleapis.com/auth/spreadsheets']

    def __init__(self, sheet_id, sheet_name):
        self.sheet_id = sheet_id
        self.sheet_name = sheet_name
        self.creds = None
        self.creds = self.get_credentials()
        self.last_timestamp = self.find_last_timestamp()
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
                formatted_datetime = result[0]
                actual_datetime = datetime.fromisoformat(formatted_datetime.replace(' ', 'T'))
                return actual_datetime.timestamp()
        except (HttpError, ValueError) as err:
            print(err)
            print("Error during google sheets setup. Initiating with current time. Potentially missed mod actions.")
            return time.time()

    def append_to_sheet(self, values):
        self.append_to_sheet_helper(values)
        gc.collect()

    def append_to_sheet_helper(self, values):
        if Settings.is_dry_run:
            print("\tDRY RUN!!!")
            return

        creds = self.get_credentials()
        service = build('sheets', 'v4', credentials=creds)
        request_range = f'{self.sheet_name}!A:E'
        request_body = {
            'range': request_range,
            'values': values,
            'majorDimension': 'ROWS'
        }
        try:
            result = service.spreadsheets().values().append(
                spreadsheetId=self.sheet_id,
                range=request_range,
                valueInputOption='USER_ENTERED',
                body=request_body).execute()
            print(f'{result.get("updates").get("updatedCells")} cells appended.')
        except HttpError as error:
            print(f'The API returned an error: {error}')
            # invalidate credentials just in case re-initing will fix things
            self.creds = None

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
