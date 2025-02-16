import traceback
import calendar
from datetime import datetime, timedelta
from threading import Thread

import requests

import config
import os
import praw

from discord_client import DiscordClient
from google_sheets_recorder import GoogleSheetsRecorder
from reddit_actions_handler import RedditActionsHandler
from resilient_thread import ResilientThread
from settings import *
import time

from subreddit_tracker import SubredditTracker


def get_id(fullname):
    split = fullname.split("_")
    return split[1] if len(split) > 0 else split[0]


def handle_mod_removal(subreddit_tracker, discord_client, action, reddit_handler):
    if action.action not in ["removelink", "approvelink"]:
        return
    # Automod exempt
    if action.mod == "AutoModerator":
        return
    if action.details == "confirm_spam":
        return
    # this is required on startup to prevent re-actioning startup stream
    if action.created_utc < subreddit_tracker.time_last_checked:
        return

    submission_id = get_id(action.target_fullname)
    submission = subreddit_tracker.reddit.submission(id=submission_id)
    title_untruc = f"[{submission.score}] {submission.title}"
    title = (title_untruc[:275] + '...') if len(title_untruc) > 275 else title_untruc
    url = f"https://np.reddit.com{submission.permalink}"

    post_removals_sub = subreddit_tracker.subreddit_wilds
    if action.action == "removelink" and post_removals_sub:
        reddit_handler.add_post(post_removals_sub, url, title)

    # if post was removed by comment mod, also post to removals sub and discord if supported
    if action.mod.name in subreddit_tracker.get_comment_mods():
        cm_post_actions_sub = subreddit_tracker.subreddit_removals
        if cm_post_actions_sub:
            reddit_handler.add_post(cm_post_actions_sub, url, title)
        cm_actions_discord = subreddit_tracker.discord_removals_server
        cm_actions_channel = subreddit_tracker.discord_removals_channel
        if cm_actions_discord and cm_actions_channel:
            detail = "removed" if action.action == "removelink" else "approved"
            message = f"A comment moderator has {detail} a post. Please follow-up with this mod.\n" \
                      f"Comment Mod: {action.mod.name}\n" \
                      f"Post: {url}\n" \
                      f"Title: {title}"
            discord_client.send_msg(subreddit_tracker.discord_removals_server,
                                    subreddit_tracker.discord_removals_channel,
                                    message)


def handle_mod_action(google_sheets_recorder, reddit, action):
    if action.mod in ["StatementBot", "toolboxnotesxfer"]:
        return

    automod_report = find_automod_report(reddit, action)
    if automod_report:
        # if automod reported this content, the report is the automod rule
        automod_rule = automod_report
    elif action.mod == 'AutoModerator':
        # if automod fitered this content, the action details is the automod rule
        automod_rule = action.details if hasattr(action, 'details') else ''
    else:
        # otherwise there's no automod rule to persist
        automod_rule = ''
    link = action.target_permalink if hasattr(action, 'target_permalink') else ''
    google_sheets_recorder.append_to_sheet(action.subreddit, action.created_utc,
                                           action.mod.name, action.action, link, automod_rule)


def find_automod_report(reddit, action):
    if action.action in ["approvecomment", "removecomment"]:
        content = reddit.comment(action.target_fullname)
    elif action.action in ["approvelink", "removelink"]:
        content = reddit.submission(action.target_fullname)
    else:
        content = None
    if not content:
        return ''
    try:
        if hasattr(content, 'mod_reports_dismissed'):
            reports = content.mod_reports_dismissed
            for report in reports:
                # let's hope reports are always in [report, reporter] format?
                if report[1] == 'AutoModerator':
                    return report[0]
            return ''
    except Exception as e:
        # hasattr can fail, handle this by not recording anything
        return ''


def handle_bans(discord_client, subreddit_tracker, action):
    if action.action not in ["banuser"]:
        return

    link_portion = f"\nURL: {action.target_permalink}" if hasattr(action, 'target_permalink') else ''
    message = f"Banned user: u/{action.target_author} for {action.details}\n" \
              f"Moderator: {action.mod.name}{link_portion}"
    discord_client.send_msg(subreddit_tracker.discord_removals_server, subreddit_tracker.discord_bans_channel, message)


def handle_mod_actions(discord_client, google_sheets_recorder, reddit_handler, reddit, subreddit_trackers):
    subreddits = "+".join(list(subreddit_trackers.keys()))
    for action in reddit.subreddit(subreddits).mod.stream.log():
        subreddit_tracker = subreddit_trackers[action.subreddit.lower()]
        try:
            handle_mod_action(google_sheets_recorder, reddit, action)
            handle_mod_removal(subreddit_tracker, discord_client, action, reddit_handler)
            handle_bans(discord_client, subreddit_tracker, action)
        except Exception as e:
            message = f"Exception when handling action {action.id} for {action.subreddit}: {e}\n" \
                      f"```{traceback.format_exc()}```"
            discord_client.send_error_msg(message)
            print(message)


def handle_comments(discord_client, subreddit, reddit_handler, toxicity_api_key, subreddit_trackers):
    for comment in subreddit.stream.comments():
        try:
            handle_shadowbanned_users(discord_client, reddit_handler, comment, subreddit_trackers)
            handle_toxic_comments(discord_client, reddit_handler, toxicity_api_key, comment)
        except Exception as e:
            message = f"Exception when handling comment {comment.id}: {e}\n```{traceback.format_exc()}```"
            discord_client.send_error_msg(message)
            print(message)


def handle_shadowbanned_users(discord_client, reddit_handler, comment, subreddit_trackers):
    try:
        if ((not hasattr(comment, 'author'))
                or (not hasattr(comment.author, 'created'))
                or (hasattr(comment.author, 'is_suspended') and comment.author.is_suspended)):
            respond_to_shadowban(discord_client, reddit_handler, comment, subreddit_trackers)
    except Exception as e:
        # Sometimes they also just return 404s so this handles that too
        respond_to_shadowban(discord_client, reddit_handler, comment, subreddit_trackers)


def respond_to_shadowban(discord_client, reddit_handler, comment, subreddit_trackers):
    subreddit_tracker = subreddit_trackers[comment.subreddit.display_name.lower()]
    discord_channel = subreddit_trackers.discord_shadowbans_channel
    if discord_channel:
        message = f"Shadowbanned user comment: https://www.reddit.com{comment.permalink}"
        discord_client.send_msg(subreddit_tracker.discord_removals_server, discord_channel, message)
    if subreddit_tracker.should_message_shadowbans:
        message = (f"Hi, you appear to be shadow banned by reddit. "
                   f"A shadow ban is a form of ban when reddit silently removes your content without your "
                   f"knowledge. Only reddit admins and moderators of the community you're commenting in can see"
                   f" the content, unless they manually approve it.\n\nThis is not a ban by "
                   f"{comment.subreddit_name_prefixed}, and the mod team cannot help you reverse the ban. "
                   f"We recommend visiting r/ShadowBan to confirm you're banned and how to appeal.\n\n"
                   f"We hope knowing this can help you.\n\n"
                   f"This is a bot - responses and messages are not monitored. "
                   f"If it appears to be wrong, [please modmail us]"
                   f"(https://www.reddit.com/message/compose?to=/r/collapse&subject=Shadowban Bot Error).")
        reddit_handler.write_removal_reason_custom(comment, message)


def handle_toxic_comments(discord_client, reddit_handler, toxicity_api_key, comment):
    try:
        result = determine_toxicity(comment.body, toxicity_api_key)
        if result > 0.85:
            percent = round(result * 100)
            print(f'Comment ({comment.permalink}) reported @ {percent}% confidence')
            reddit_handler.report_content(f"Automatic report for toxicity @ {percent}% confidence", comment)
    except Exception as e:
        message = f"Exception when handling toxic comment {comment.id}: {e}\n```{traceback.format_exc()}```"
        discord_client.send_error_msg(message)
        print(message)


def determine_toxicity(text, toxicity_api_key):
    # don't even try to error handle this, the API sends back weird stuff all the time
    try:
        """ Call API and return response list with boolean & confidence score """
        text = re.sub(r'>[^\n]+', "", text)  # strip out quotes
        if re.match(r'^\s*$', text) is not None:
            # the comment was just quotes and/or whitespace
            return 0

        response = requests.post("https://api.moderatehatespeech.com/api/v1/moderate/",
                                 json={"token": toxicity_api_key, "text": text}).json()

        return float(response['confidence']) if response['class'] == "flag" else 0
    except Exception as e:
        return 0


def get_adjusted_utc_timestamp(time_difference_mins):
    adjusted_utc_dt = datetime.utcnow() - timedelta(minutes=time_difference_mins)
    return calendar.timegm(adjusted_utc_dt.utctimetuple())


def create_mod_actions_thread(discord_client, recorder, reddit_handler, reddit, subreddit_trackers):
    subreddits = "+".join(list(subreddit_trackers.keys()))
    name = f"{subreddits}-ModActions"
    thread = ResilientThread(discord_client, name, target=handle_mod_actions,
                             args=(discord_client, recorder, reddit_handler, reddit, subreddit_trackers))
    thread.start()
    print(f"Created {name} thread")


def create_comment_thread(client_id, client_secret, bot_username, bot_password, discord_client, reddit_handler,
                          subreddit_name, toxicity_api_key, subreddit_trackers):
    reddit = create_reddit(bot_password, bot_username, client_id, client_secret, "comment")
    subreddit = reddit.subreddit(subreddit_name)

    name = f"{subreddit_name}-Comment"
    thread = ResilientThread(discord_client, name,
                             target=handle_comments,
                             args=(discord_client, subreddit, reddit_handler, toxicity_api_key,
                                   subreddit_trackers))
    thread.start()
    print(f"Created {name} thread")


def create_reddit(bot_password, bot_username, client_id, client_secret, script_type):
    return praw.Reddit(
        client_id=client_id,
        client_secret=client_secret,
        user_agent=f"flyio:com.subredditwilds.{script_type}",
        redirect_uri="http://localhost:8080",  # unused for script applications
        username=bot_username,
        password=bot_password
    )


def run_forever():
    # get config from env vars if set, otherwise from config file
    client_id = os.environ.get("CLIENT_ID", config.CLIENT_ID)
    client_secret = os.environ.get("CLIENT_SECRET", config.CLIENT_SECRET)
    bot_username = os.environ.get("BOT_USERNAME", config.BOT_USERNAME)
    bot_password = os.environ.get("BOT_PASSWORD", config.BOT_PASSWORD)
    discord_token = os.environ.get("DISCORD_TOKEN", config.DISCORD_TOKEN)
    discord_error_guild_name = os.environ.get("DISCORD_ERROR_GUILD", config.DISCORD_ERROR_GUILD)
    discord_error_channel_name = os.environ.get("DISCORD_ERROR_CHANNEL", config.DISCORD_ERROR_CHANNEL)
    toxicity_api_key = os.environ.get("TOXICITY_API_KEY", config.TOXICITY_API_KEY)
    subreddits_config = os.environ.get("SUBREDDITS", config.SUBREDDITS)
    subreddit_names = [subreddit.strip() for subreddit in subreddits_config.split(",")]
    print("CONFIG: subreddit_names=" + str(subreddit_names))

    # discord stuff
    discord_client = DiscordClient(discord_error_guild_name, discord_error_channel_name)
    discord_client.add_commands()
    Thread(target=discord_client.run, args=(discord_token,)).start()
    while not discord_client.is_ready:
        time.sleep(1)

    try:
        recorder = GoogleSheetsRecorder(discord_client)
        reddit_handler = RedditActionsHandler(discord_client)
        toxicity_interested = list()
        reddit = create_reddit(bot_password, bot_username, client_id, client_secret, "modactions")
        subreddit_trackers = dict()
        for subreddit_name in subreddit_names:
            settings = SettingsFactory.get_settings(subreddit_name)
            print(f"Creating {subreddit_name} subreddit with {type(settings).__name__} settings")

            subreddit_base = reddit.subreddit(subreddit_name)
            subreddit_wilds = reddit.subreddit(settings.subreddit_wilds) if settings.subreddit_wilds else None
            subreddit_removals = reddit.subreddit(settings.subreddit_removals) if settings.subreddit_removals else None
            subreddit_tracker = SubredditTracker(reddit, subreddit_base, subreddit_wilds, subreddit_removals,
                                                 settings.comment_mod_permissions, settings.comment_mod_whitelist,
                                                 settings.discord_removals_server, settings.discord_removals_channel,
                                                 settings.discord_bans_channel, settings.discord_shadowbans_channel,
                                                 settings.should_message_shadowbans)
            subreddit_trackers[subreddit_name.lower()] = subreddit_tracker

            if settings.google_sheet_id and settings.google_sheet_name:
                recorder.add_sheet_for_sub(subreddit_name, settings.google_sheet_id, settings.google_sheet_name)
            if settings.check_comment_toxicity:
                toxicity_interested.append(subreddit_name.lower())

        create_mod_actions_thread(discord_client, recorder, reddit_handler, reddit, subreddit_trackers)
        create_comment_thread(client_id, client_secret, bot_username, bot_password, discord_client, reddit_handler,
                              "+".join(toxicity_interested), toxicity_api_key, subreddit_trackers)
    except Exception as e:
        message = f"Exception in main processing: {e}\n```{traceback.format_exc()}```"
        discord_client.send_error_msg(message)
        print(message)

    # this is required as otherwise discord fails when main thread is done
    while True:
        time.sleep(5)


if __name__ == "__main__":
    run_forever()
