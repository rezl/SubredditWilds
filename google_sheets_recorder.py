from __future__ import print_function

import base64
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
    DATE_CONVERSION = '%Y-%m-%d %H:%M:%S'

    def __init__(self, sheet_id, sheet_name):
        self.sheet_id = sheet_id
        self.sheet_name = sheet_name
        self.creds = None
        self.creds = self.get_credentials()

        try:
            service = build('sheets', 'v4', credentials=self.creds)

            sheet = service.spreadsheets()
            first_column_range = f'{self.sheet_name}!A:A'
            result = sheet.values().get(spreadsheetId=self.sheet_id, range=first_column_range).execute()

            first_column_values = result.get('values', [])

            formatted_datetime = first_column_values[len(first_column_values) - 1][0]
            actual_datetime = datetime.strptime(formatted_datetime, GoogleSheetsRecorder.DATE_CONVERSION)
            self.time_last_checked = actual_datetime.timestamp()
        except (HttpError, ValueError) as err:
            print(err)
            print("Error during google sheets setup. Initiating with current time. Potentially missed mod actions.")
            self.time_last_checked = time.time()

    def append_to_sheet(self, values):
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

        # first time initialization
        if 'GOOGLE_APPLICATION_CREDENTIALS' in os.environ:
            # bot credentials is stored as base64 file so it can be provided to fly as a secret
            with open('credentials.base64', 'r') as f:
                credentials_base64 = f.read().strip()
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
