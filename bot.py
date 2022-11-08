import calendar
import config
from datetime import datetime, timedelta
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
        self.target_wilds_subreddit_name = os.environ["TARGET_SUBREDDIT_WILDS"] \
            if "TARGET_SUBREDDIT_WILDS" in os.environ else config.TARGET_SUBREDDIT_WILDS
        self.target_removals_subreddit_name = os.environ["TARGET_SUBREDDIT_REMOVALS"] \
            if "TARGET_SUBREDDIT_REMOVALS" in os.environ else config.TARGET_SUBREDDIT_REMOVALS

        print("CONFIG: client_id=" + client_id + " client_secret=" + "*********" +
              " bot_username=" + bot_username + " bot_password=" + "*********" +
              " source_subreddit=" + self.source_subreddit_name +
              " target_subreddit_wilds=" + self.target_wilds_subreddit_name +
              " target_removals_subreddit_name=" + self.target_removals_subreddit_name)

        self.reddit = praw.Reddit(
            client_id=client_id,
            client_secret=client_secret,
            user_agent="my user agent",
            redirect_uri="http://localhost:8080",  # unused for script applications
            username=bot_username,
            password=bot_password
        )

        self.comment_mods_last_check = datetime.utcfromtimestamp(0)
        self.cached_comment_mods = self.get_comment_mods(self.reddit.subreddit(self.source_subreddit_name))

        # initialize with the last submission time in target subs (assume no non-bot posts)
        last_checked_wilds = next(self.reddit.subreddit(self.target_wilds_subreddit_name).new()).created_utc
        last_checked_removals = next(self.reddit.subreddit(self.target_removals_subreddit_name).new()).created_utc
        self.time_last_checked = max(last_checked_wilds, last_checked_removals)

    @staticmethod
    def get_id(fullname):
        split = fullname.split("_")
        return split[1] if len(split) > 0 else split[0]

    def get_comment_mods(self, subreddit):
        # refresh comment mods every day
        if datetime.utcnow() - self.comment_mods_last_check < timedelta(days=1):
            return self.cached_comment_mods

        mods = list()
        comment_mod_perms = set(Settings.comment_mod_permissions)
        comment_mod_whitelist = set(Settings.comment_mod_whitelist)
        for moderator in subreddit.moderator():
            if moderator.name in comment_mod_whitelist:
                continue
            if set(moderator.mod_permissions) == comment_mod_perms:
                mods.append(moderator.name)
        self.comment_mods_last_check = datetime.utcnow()
        self.cached_comment_mods = mods
        print(f"Refreshed comment mods: {mods}")
        return mods

    def handle_posts(self):
        print("Checking posts")
        subreddit = self.reddit.subreddit(self.source_subreddit_name)
        source_subreddit_acts = subreddit.mod.log(limit=15, action="removelink")
        comment_mods = self.get_comment_mods(subreddit)

        for action in source_subreddit_acts:
            if action.mod == "AutoModerator":
                continue
            if action.details == "confirm_spam":
                continue
            # actions are provided in time-order, break when found action older than last checked
            if action.created_utc < self.time_last_checked:
                break
            submission_id = self.get_id(action.target_fullname)
            submission = self.reddit.submission(id=submission_id)

            wilds_sub = self.target_wilds_subreddit_name
            print(f"Adding post to {wilds_sub}: {submission.title}")
            title = f"[{submission.score}] {submission.title}"
            url = f"https://np.reddit.com{submission.permalink}"
            if Settings.is_dry_run:
                print("\tDRY RUN!!!")
                continue
            else:
                self.reddit.subreddit(wilds_sub).submit(title, url=url, send_replies=False)
                time.sleep(5)

            # if post was removed by comment mod, also post to removals sub
            if action.mod.name in comment_mods:
                removals_sub = self.target_removals_subreddit_name
                print(f"Adding post to {removals_sub}: {submission.title}")
                if Settings.is_dry_run:
                    print("\tDRY RUN!!!")
                    continue
                else:
                    self.reddit.subreddit(removals_sub).submit(title, url=url, send_replies=False)
                    time.sleep(5)

        self.time_last_checked = calendar.timegm(datetime.utcnow().utctimetuple())


def run_forever():
    while True:
        try:
            janitor = Janitor()
            while True:
                try:
                    print("____________________")
                    janitor.handle_posts()
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
            janitor.handle_posts()
        except Exception as e:
            print(e)


if __name__ == "__main__":
    # run_once()
    run_forever()
