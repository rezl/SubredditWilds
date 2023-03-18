import asyncio
import traceback
import typing
from threading import Thread

import config
from discord.ext import commands
import discord
import os
import praw
from settings import *
import time

from subreddit_tracker import SubredditTracker


def get_id(fullname):
    split = fullname.split("_")
    return split[1] if len(split) > 0 else split[0]


def handle_mod_actions(discord_client, subreddit_tracker):
    for action in subreddit_tracker.subreddit.mod.stream.log(action="removelink"):
        comment_mods = subreddit_tracker.get_comment_mods()
        submission_id = get_id(action.target_fullname)
        try:
            if action.mod == "AutoModerator":
                continue
            if action.details == "confirm_spam":
                continue
            # this is required on startup to prevent re-actioning startup stream
            if action.created_utc < subreddit_tracker.time_last_checked:
                continue

            submission = subreddit_tracker.reddit.submission(id=submission_id)
            title_untruc = f"[{submission.score}] {submission.title}"
            title = (title_untruc[:275] + '...') if len(title_untruc) > 275 else title_untruc
            url = f"https://np.reddit.com{submission.permalink}"

            wilds_sub = subreddit_tracker.subreddit_wilds
            if wilds_sub:
                print(f"Adding post to {wilds_sub}: {title}")
                if Settings.is_dry_run:
                    print("\tDRY RUN!!!")
                    continue
                else:
                    wilds_sub.submit(title, url=url, send_replies=False)
                    time.sleep(5)

            # if post was removed by comment mod, also post to removals sub and discord
            if action.mod.name in comment_mods:
                removals_sub = subreddit_tracker.subreddit_removals
                if removals_sub:
                    print(f"Adding post to {removals_sub}: {submission.title}")
                    if Settings.is_dry_run:
                        print("\tDRY RUN!!!")
                        continue
                    else:
                        removals_sub.submit(title, url=url, send_replies=False)
                        time.sleep(5)
                removals_discord = subreddit_tracker.discord_removals_server
                removals_channel = subreddit_tracker.discord_removals_channel
                if removals_discord and removals_channel:
                    print(f"Adding post to {removals_discord}/{removals_channel}: {submission.title}")
                    if Settings.is_dry_run:
                        print("\tDRY RUN!!!")
                        continue
                    else:
                        message = f"A comment moderator has removed a post. Please follow-up with this mod.\n" \
                                  f"Comment Mod: {action.mod.name}\n" \
                                  f"Post: {url}\n" \
                                  f"Title: {title}"
                        print(message)
                        discord_client.send_msg(subreddit_tracker.discord_removals_server,
                                                subreddit_tracker.discord_removals_channel,
                                                message)

        except Exception as e:
            message = f"Exception when handling action {submission_id}: {e}\n```{traceback.format_exc()}```"
            discord_client.send_error_msg(message)
            print(message)


def handle_modmail(discord_client, subreddit):
    for conversation in subreddit.mod.stream.modmail_conversations(state="new", sort="unread"):
        try:
            if should_respond(conversation, subreddit):
                message = f"Hi, thanks for messaging the r/{subreddit.display_name} mods. " \
                          "If this message is about removed content, " \
                          "please respond with a link to the content in question.\n\n" \
                          "This is an automated bot response. " \
                          "An organic mod will respond to you soon, please allow 2 days as our team is across the world"
                print(f"Responding to modmail {conversation.id}: {message}")
                if Settings.is_dry_run:
                    print("\tDRY RUN!!!")
                    continue
                conversation.reply(body=message, author_hidden=False)
                conversation.read()
        except Exception as e:
            message = f"Exception when handling modmail {conversation.id}: {e}\n```{traceback.format_exc()}```"
            discord_client.send_error_msg(message)
            print(message)


def modmail_contains(conversation, keyword):
    for message in conversation.messages:
        if keyword in message.body_markdown:
            return True
    return False


def should_respond(conversation, subreddit):
    # already read - shouldn't occur, just extra protection
    if not conversation.last_unread:
        return False
    # mod has already responded to the conversation - shouldn't occur, just extra protection
    if len(conversation.authors) > 1 or conversation.last_mod_update:
        return False

    # message already contains a link to some reddit content
    if modmail_contains(conversation, f"{subreddit.display_name_prefixed}/comments/"):
        return False

    # modmail asking about removed content, should respond asking for a link
    if modmail_contains(conversation, "remov") or modmail_contains(conversation, "delet"):
        return True
    actions = subreddit.mod.notes.redditors(conversation.user)
    for action in actions:
        time_diff_secs = time.time() - action.created_at
        # no action in the last week
        if time_diff_secs > 7 * 24 * 3600:
            return False
        # person with recently removed content, should respond asking for a link
        if action.action in ["removecomment", "removelink", "banuser"]:
            return True
    return False


class DiscordClient(commands.Bot):
    def __init__(self, error_guild_name, error_guild_channel):
        super().__init__('!', intents=discord.Intents.all())
        self.error_guild_name = error_guild_name
        self.error_guild_channel = error_guild_channel
        self.error_guild = None
        self.error_channel = None
        self.is_ready = False

    async def on_ready(self):
        print(f'{self.user} has connected to Discord!')
        self.error_guild = discord.utils.get(self.guilds, name=self.error_guild_name)
        self.error_channel = discord.utils.get(self.error_guild.channels, name=self.error_guild_channel)
        self.is_ready = True
        print(
            f'{self.user} is connected to the following guild:\n'
            f'{self.error_guild.name}(id: {self.error_guild.id})'
        )

    def send_error_msg(self, message):
        full_message = f"Collapsewilds script has had an exception. This can normally be ignored, " \
                       f"but if it's occurring frequently, may indicate a script error.\n{message}"
        if self.error_channel:
            asyncio.run_coroutine_threadsafe(self.error_channel.send(full_message), self.loop)

    def send_msg(self, guild_name, channel_name, message):
        guild = discord.utils.get(self.guilds, name=guild_name)
        channel = discord.utils.get(guild.channels, name=channel_name)
        if channel:
            asyncio.run_coroutine_threadsafe(channel.send(message), self.loop)


def create_mod_actions_thread(client_id, client_secret, bot_username, bot_password,
                              discord_client, settings, subreddit_name):
    reddit = praw.Reddit(
        client_id=client_id,
        client_secret=client_secret,
        user_agent=f"flyio:com.subredditwilds.modactions.{subreddit_name}",
        redirect_uri="http://localhost:8080",  # unused for script applications
        username=bot_username,
        password=bot_password
    )

    subreddit_wilds = reddit.subreddit(settings.subreddit_wilds) if settings.subreddit_wilds else None
    subreddit_removals = reddit.subreddit(settings.subreddit_removals) if settings.subreddit_removals else None
    subreddit_tracker = SubredditTracker(reddit.subreddit(subreddit_name),
                                         subreddit_wilds, subreddit_removals,
                                         settings.comment_mod_permissions, settings.comment_mod_whitelist,
                                         settings.discord_removals_server, settings.discord_removals_channel)

    Thread(target=handle_mod_actions, args=(discord_client, subreddit_tracker)).start()
    print(f"Created {subreddit_name} modactions thead")


def create_modmail_thread(client_id, client_secret, bot_username, bot_password, discord_client, subreddit_name):
    reddit = praw.Reddit(
        client_id=client_id,
        client_secret=client_secret,
        user_agent=f"flyio:com.subredditwilds.modmail.{subreddit_name}",
        redirect_uri="http://localhost:8080",  # unused for script applications
        username=bot_username,
        password=bot_password
    )

    subreddit = reddit.subreddit(subreddit_name)
    Thread(target=handle_modmail, args=(discord_client, subreddit)).start()
    print(f"Created {subreddit_name} modmail thead")


def run_forever():
    # get config from env vars if set, otherwise from config file
    client_id = os.environ.get("CLIENT_ID", config.CLIENT_ID)
    client_secret = os.environ.get("CLIENT_SECRET", config.CLIENT_SECRET)
    bot_username = os.environ.get("BOT_USERNAME", config.BOT_USERNAME)
    bot_password = os.environ.get("BOT_PASSWORD", config.BOT_PASSWORD)
    discord_token = os.environ.get("DISCORD_TOKEN", config.DISCORD_TOKEN)
    error_guild_name = os.environ.get("DISCORD_ERROR_GUILD", config.DISCORD_ERROR_GUILD)
    error_guild_channel = os.environ.get("DISCORD_ERROR_CHANNEL", config.DISCORD_ERROR_CHANNEL)
    subreddits_config = os.environ.get("SUBREDDITS", config.SUBREDDITS)
    subreddit_names = [subreddit.strip() for subreddit in subreddits_config.split(",")]

    print("CONFIG: subreddit_names=" + str(subreddit_names))

    discord_client = DiscordClient(error_guild_name, error_guild_channel)
    Thread(target=discord_client.run, args=(discord_token,)).start()

    while not discord_client.is_ready:
        time.sleep(1)

    @discord_client.command(name="ping", description="lol")
    async def ping(ctx):
        dry_run = "I'm currently running in Dry Run mode" if Settings.is_dry_run else ""
        await ctx.channel.send(dry_run)

    @discord_client.command(name="set_dry_run", brief="Set whether bot can make permanent reddit actions (0/1)",
                            description="Change whether this bot can make reddit actions (usernotes, comments). "
                                        "When in dry_run, the bot will not make usernotes or reddit comments, "
                                        "however the full workflow otherwise is available on discord\n"
                                        "Include: \n"
                                        "  * 0 (not in dry run, makes actions)\n"
                                        "  * 1 (dry run, no reddit actions)",
                            usage=".set_dry_run 1")
    async def set_dry_run(ctx, dry_run: typing.Literal[0, 1] = 1):
        Settings.is_dry_run = dry_run
        if Settings.is_dry_run:
            await ctx.channel.send(f"I am now running in dry run mode")
        else:
            await ctx.channel.send(f"I am now NOT running in dry run mode")

    try:
        for subreddit_name in subreddit_names:
            settings = get_subreddit_settings(subreddit_name)
            print(f"Creating {subreddit_name} subreddit with {type(settings).__name__} settings")

            create_mod_actions_thread(client_id, client_secret, bot_username, bot_password,
                                      discord_client, settings, subreddit_name)
            if settings.check_modmail:
                create_modmail_thread(client_id, client_secret, bot_username, bot_password,
                                      discord_client, subreddit_name)

            print(f"Created {subreddit_name} subreddit")
    except Exception as e:
        message = f"Exception in main processing: {e}\n```{traceback.format_exc()}```"
        discord_client.send_error_msg(message)
        print(message)


def get_subreddit_settings(subreddit_name):
    # use <SubredditName>Settings if exists, default to Settings
    settings_name = subreddit_name + "Settings"
    try:
        constructor = globals()[settings_name]
        return constructor()
    except KeyError:
        return Settings()


if __name__ == "__main__":
    run_forever()
