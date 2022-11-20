import asyncio
import calendar
from threading import Thread

import config
from datetime import datetime, timedelta
from discord.ext import commands
import discord
import os
import praw
from settings import *
import time


class Janitor:
    def __init__(self, discord_client):
        self.discord_client = discord_client

        # get config from env vars if set, otherwise from config file
        client_id = os.environ.get("CLIENT_ID", config.CLIENT_ID)
        client_secret = os.environ.get("CLIENT_SECRET", config.CLIENT_SECRET)
        bot_username = os.environ.get("BOT_USERNAME", config.BOT_USERNAME)
        bot_password = os.environ.get("BOT_PASSWORD", config.BOT_PASSWORD)
        self.source_subreddit_name = os.environ.get("SOURCE_SUBREDDIT", config.SOURCE_SUBREDDIT)
        self.target_wilds_subreddit_name = os.environ.get("TARGET_SUBREDDIT_WILDS", config.SOURCE_SUBREDDIT)
        self.target_removals_subreddit_name = os.environ.get("TARGET_SUBREDDIT_REMOVALS", config.SOURCE_SUBREDDIT)

        print("CONFIG: client_id=" + client_id + " client_secret=" + "*********" + "discord_token=" + "*********" +
              " bot_username=" + bot_username + " bot_password=" + "*********" +
              " source_subreddit=" + self.source_subreddit_name +
              " target_subreddit_wilds=" + self.target_wilds_subreddit_name +
              " target_removals_subreddit_name=" + self.target_removals_subreddit_name)

        self.reddit = praw.Reddit(
            client_id=client_id,
            client_secret=client_secret,
            user_agent="flyio:com.collapse.collapsewilds:v1.1",
            redirect_uri="http://localhost:8080",  # unused for script applications
            username=bot_username,
            password=bot_password
        )

        self.comment_mods_last_check = datetime.utcfromtimestamp(0)
        self.cached_comment_mods = self.get_comment_mods(self.reddit.subreddit(self.source_subreddit_name))

        # initialize with the last submission time in wilds
        self.time_last_checked = next(self.reddit.subreddit(self.target_wilds_subreddit_name).new()).created_utc

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
            try:
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
                title_untruc = f"[{submission.score}] {submission.title}"
                title = (title_untruc[:275] + '...') if len(title_untruc) > 275 else title_untruc
                print(f"Adding post to {wilds_sub}: {title}")
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
            except Exception as e:
                submission_id = self.get_id(action.target_fullname)
                message = f"Exception in action loop for {submission_id}: {e}"
                self.discord_client.send_msg(message)
                print(message)

        self.time_last_checked = calendar.timegm(datetime.utcnow().utctimetuple())


class DiscordClient(commands.Bot):
    def __init__(self, guild_name, bot_channel):
        super().__init__('!', intents=discord.Intents.all())
        self.guild_name = guild_name
        self.bot_channel = bot_channel
        self.guild = None
        self.channel = None
        self.is_ready = False

    async def on_ready(self):
        print(f'{self.user} has connected to Discord!')
        self.guild = discord.utils.get(self.guilds, name=self.guild_name)
        self.channel = discord.utils.get(self.guild.channels, name=self.bot_channel)
        self.is_ready = True
        print(
            f'{self.user} is connected to the following guild:\n'
            f'{self.guild.name}(id: {self.guild.id})'
        )

    def send_msg(self, message):
        full_message = f"Collapsewilds script has had an exception. Please check on it.\n{message}"
        if self.channel:
            asyncio.run_coroutine_threadsafe(self.channel.send(full_message), self.loop)


def run_forever():
    discord_token = os.environ.get("DISCORD_TOKEN", config.DISCORD_TOKEN)
    guild_name = os.environ.get("DISCORD_GUILD", config.DISCORD_GUILD)
    guild_channel = os.environ.get("DISCORD_CHANNEL", config.DISCORD_CHANNEL)
    client = DiscordClient(guild_name, guild_channel)
    Thread(target=client.run, args=(discord_token,)).start()

    while not client.is_ready:
        time.sleep(1)

    while True:
        try:
            janitor = Janitor(client)
            while True:
                print("____________________")
                janitor.handle_posts()
                time.sleep(Settings.post_check_frequency_mins * 60)
        except Exception as e:
            message = f"Exception in main loop: {e}"
            client.send_msg(message)
            print(message)
            time.sleep(Settings.post_check_frequency_mins * 60)


if __name__ == "__main__":
    run_forever()
