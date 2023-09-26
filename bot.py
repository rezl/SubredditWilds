import traceback
from datetime import timedelta
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

    # if post was removed by comment mod, also post to removals sub and discord
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


def handle_post_flair_action(subreddit_tracker, action, reddit_handler):
    if not action.target_fullname:
        return
    submission = subreddit_tracker.reddit.submission(id=get_id(action.target_fullname))
    # assume normal removal methods (ie toolbox) always remove first then edit flair, so flair_helper shouldn't act
    if hasattr(submission, 'removed') and submission.removed:
        return
    flair = submission.link_flair_text
    if not flair or "Rule" not in flair:
        return
    response = f"Hi, thanks for contributing. " \
               f"However, your submission was removed from r/{subreddit_tracker.subreddit.display_name}.\n\n" \
               f"{flair}\n\n" \
               f"You can message the mods if you feel this was in error," \
               f" please include a link to the comment or post in question."
    reddit_handler.remove_content("Flair helper", submission)
    reddit_handler.write_removal_reason_custom(submission, response)


def handle_mod_action(google_sheets_recorder, reddit, action):
    if action.mod in ["StatementBot"]:
        return

    automod_report = find_automod_report(reddit, action)
    link = action.target_permalink if hasattr(action, 'target_permalink') else ''
    details = action.details if hasattr(action, 'details') else ''
    google_sheets_recorder.append_to_sheet(action.subreddit, action.created_utc,
                                           action.mod.name, action.action, link, details, automod_report)


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


def handle_mod_actions(discord_client, google_sheets_recorder, reddit_handler, reddit, subreddit_trackers):
    subreddits = "+".join(list(subreddit_trackers.keys()))
    for action in reddit.subreddit(subreddits).mod.stream.log():
        subreddit_tracker = subreddit_trackers[action.subreddit.lower()]
        try:
            handle_mod_action(google_sheets_recorder, reddit, action)
            handle_mod_removal(subreddit_tracker, discord_client, action, reddit_handler)
            if action.action == "editflair":
                handle_post_flair_action(subreddit_tracker, action, reddit_handler)
        except Exception as e:
            message = f"Exception when handling action {action.id} for {action.subreddit}: {e}\n" \
                      f"```{traceback.format_exc()}```"
            discord_client.send_error_msg(message)
            print(message)


def handle_toxic_comments(discord_client, subreddit, reddit_handler, toxicity_api_key):
    for comment in subreddit.stream.comments():
        try:
            result = determine_toxicity(comment.body, toxicity_api_key)
            if result > 0.85:
                percent = round(result * 100)
                print(f'Comment ({comment.permalink}) reported @ {percent}% confidence')
                reddit_handler.report_content(f"Automatic report for toxicity @ {percent}% confidence", comment)
        except Exception as e:
            message = f"Exception when handling comment {comment.id}: {e}\n```{traceback.format_exc()}```"
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
        print(f"determine_toxicity failed for text, ignoring: {text}")
        return 0


def handle_modmail(discord_client, subreddits, reddit_handler):
    for conversation in subreddits.mod.stream.modmail_conversations(state="new", sort="unread"):
        subreddit = conversation.owner
        print(f"Handling modmail: {str(subreddit)}: {str(conversation)}")
        try:
            if should_respond(conversation):
                message = f"Hi, thanks for messaging the r/{subreddit.display_name} mods. " \
                          "If this message is about removed content, " \
                          "please respond with a link to the content in question.\n\n" \
                          "This is an automated bot response. " \
                          "An organic mod will respond to you soon, please allow 2 days as our team is across the world"
                reddit_handler.reply_to_modmail(conversation, message)
        except Exception as e:
            message = f"Exception when handling modmail {conversation.id}: {e}\n```{traceback.format_exc()}```"
            discord_client.send_error_msg(message)
            print(message)


def modmail_contains(conversation, keyword):
    for message in conversation.messages:
        if keyword in message.body_markdown:
            return True
    return False


def should_respond(conversation):
    # whitelist of initiators to not respond to
    first_author = conversation.messages[0].author
    if first_author.name in ["ModSupportBot"]:
        return False
    if hasattr(first_author, 'is_admin') and first_author.is_admin:
        return False
    # already read - shouldn't occur, just extra protection
    if not conversation.last_unread:
        return False
    # mod has already responded to the conversation - shouldn't occur, just extra protection
    if len(conversation.authors) > 1 or conversation.last_mod_update:
        return False

    # message already contains a link to some reddit content
    subreddit = conversation.owner
    if modmail_contains(conversation, f"{subreddit.display_name_prefixed}/comments/"):
        return False

    # modmail asking about removed content, should respond asking for a link
    if modmail_contains(conversation, "remov") or modmail_contains(conversation, "delet"):
        return True
    actions = subreddit.mod.notes.redditors(conversation.user)
    for action in actions:
        if action.operator == "AutoModerator":
            continue
        time_diff_secs = time.time() - action.created_at
        # no action in the last week
        if time_diff_secs > timedelta(weeks=1).total_seconds():
            return False
        # person with recently removed content, should respond asking for a link
        if action.action in ["removecomment", "removelink", "banuser"]:
            return True
    return False


def create_mod_actions_thread(discord_client, recorder, reddit_handler, reddit, subreddit_trackers):
    subreddits = "+".join(list(subreddit_trackers.keys()))
    name = f"{subreddits}-ModActions"
    thread = ResilientThread(discord_client, name, target=handle_mod_actions,
                             args=(discord_client, recorder, reddit_handler, reddit, subreddit_trackers))
    thread.start()
    print(f"Created {name} thread")


def create_modmail_thread(client_id, client_secret, bot_username, bot_password, discord_client, reddit_handler,
                          subreddit_name):
    reddit = create_reddit(bot_password, bot_username, client_id, client_secret, "modmail")
    subreddit = reddit.subreddit(subreddit_name)

    name = f"{subreddit_name}-Modmail"
    thread = ResilientThread(discord_client, name,
                             target=handle_modmail, args=(discord_client, subreddit, reddit_handler))
    thread.start()
    print(f"Created {name} thread")


def create_comment_thread(client_id, client_secret, bot_username, bot_password, discord_client, reddit_handler,
                          subreddit_name, toxicity_api_key):
    reddit = create_reddit(bot_password, bot_username, client_id, client_secret, "comment")
    subreddit = reddit.subreddit(subreddit_name)

    name = f"{subreddit_name}-Comment"
    thread = ResilientThread(discord_client, name,
                             target=handle_toxic_comments,
                             args=(discord_client, subreddit, reddit_handler, toxicity_api_key))
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
        modmail_interested = list()
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
                                                 settings.discord_removals_server, settings.discord_removals_channel)
            subreddit_trackers[subreddit_name.lower()] = subreddit_tracker

            if settings.google_sheet_id and settings.google_sheet_name:
                recorder.add_sheet_for_sub(subreddit_name, settings.google_sheet_id, settings.google_sheet_name)
            if settings.check_modmail:
                modmail_interested.append(subreddit_name.lower())
            if settings.check_comment_toxicity:
                toxicity_interested.append(subreddit_name.lower())

        create_mod_actions_thread(discord_client, recorder, reddit_handler, reddit, subreddit_trackers)
        create_modmail_thread(client_id, client_secret, bot_username, bot_password, discord_client, reddit_handler,
                              "+".join(modmail_interested))
        create_comment_thread(client_id, client_secret, bot_username, bot_password, discord_client, reddit_handler,
                              "+".join(toxicity_interested), toxicity_api_key)
    except Exception as e:
        message = f"Exception in main processing: {e}\n```{traceback.format_exc()}```"
        discord_client.send_error_msg(message)
        print(message)

    # this is required as otherwise discord fails when main thread is done
    while True:
        time.sleep(5)


if __name__ == "__main__":
    run_forever()
