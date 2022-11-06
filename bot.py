import calendar
import config
from datetime import datetime
import os
import praw
from settings import *
import time


class Janitor:
    def __init__(self):
        # get config from env vars if set, otherwise from config file
        client_id = os.environ["CLIENT_ID"] if "CLIENT_ID" in os.environ else config.CLIENT_ID
        client_secret = os.environ["CLIENT_SECRET"] if "CLIENT_SECRET" in os.environ else config.CLIENT_SECRET
        bot_username = os.environ["BOT_USERNAME"] if "BOT_USERNAME" in os.environ else config.BOT_USERNAME
        bot_password = os.environ["BOT_PASSWORD"] if "BOT_PASSWORD" in os.environ else config.BOT_PASSWORD

        self.source_subreddit_name = os.environ["SOURCE_SUBREDDIT"] \
            if "SOURCE_SUBREDDIT" in os.environ else config.SOURCE_SUBREDDIT
        self.target_subreddit_name = os.environ["TARGET_SUBREDDIT"] \
            if "TARGET_SUBREDDIT" in os.environ else config.TARGET_SUBREDDIT

        print("CONFIG: client_id=" + client_id + " client_secret=" + "*********" +
              " bot_username=" + bot_username + " bot_password=" + "*********" +
              " source_subreddit=" + self.source_subreddit_name + " target_subreddit=" + self.target_subreddit_name)

        self.reddit = praw.Reddit(
            client_id=client_id,
            client_secret=client_secret,
            user_agent="my user agent",
            redirect_uri="http://localhost:8080",  # unused for script applications
            username=bot_username,
            password=bot_password
        )

        # initialize with the last submission time in target sub (assume no non-bot posts)
        self.time_last_checked = next(self.reddit.subreddit(self.target_subreddit_name).new()).created_utc

    @staticmethod
    def get_id(fullname):
        split = fullname.split("_")
        return split[1] if len(split) > 0 else split[0]

    def handle_recent_posts(self):
        print("Checking recent posts")
        source_subreddit_acts = self.reddit.subreddit(self.source_subreddit_name).mod.log(limit=15, action="removelink")

        removed_set = set()
        for action in source_subreddit_acts:
            if action.mod == "AutoModerator":
                continue
            if action.details == "confirm_spam":
                continue
            # actions are provided in time-order, break when found action older than last checked
            if action.created_utc < self.time_last_checked:
                break
            submission_id = self.get_id(action.target_fullname)
            if submission_id in removed_set:
                continue
            submission = self.reddit.submission(id=submission_id)
            print(f"Adding post to {self.target_subreddit_name}: {submission.title}")
            removed_set.add(id)
            title = f"[{submission.score}] {submission.title}"
            url = f"https://np.reddit.com{submission.permalink}"
            if Settings.is_dry_run:
                print("\tDRY RUN!!!")
                continue
            else:
                self.reddit.subreddit(self.target_subreddit_name).submit(title, url=url, send_replies=False)
                time.sleep(5)

        self.time_last_checked = calendar.timegm(datetime.utcnow().utctimetuple())


def run_forever():
    while True:
        try:
            janitor = Janitor()
            while True:
                try:
                    print("____________________")
                    janitor.handle_recent_posts()
                except Exception as e:
                    print(e)
                time.sleep(Settings.post_check_frequency_mins * 60)
        except Exception as e:
            print(e)
        time.sleep(Settings.post_check_frequency_mins * 60)


def run_once():
    janitor = Janitor()
    while True:
        try:
            print("____________________")
            janitor.handle_recent_posts()
        except Exception as e:
            print(e)


if __name__ == "__main__":
    # run_once()
    run_forever()
