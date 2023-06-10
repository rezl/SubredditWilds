import time
import traceback

from praw.exceptions import RedditAPIException

from settings import Settings


class RedditActionsHandler:
    def __init__(self, discord_client):
        self.discord_client = discord_client
        self.last_call_time = 0

    def add_post(self, sub, url, title):
        print(f"Adding post to {sub}: {title}")
        self.reddit_call(lambda: sub.submit(title, url=url, send_replies=False))

    def reply_to_modmail(self, conversation, message):
        print(f"Responding to modmail {conversation.id}: {message}")
        self.reddit_call(lambda: conversation.reply(body=message, author_hidden=False))
        self.reddit_call(lambda: conversation.read(), reddit_throttle_secs=1)

    def write_removal_reason_custom(self, content, reason):
        print(f"Writing removal comment for {str(content)}: {reason}")
        comment = self.reddit_call(lambda: content.reply(reason))
        self.reddit_call(lambda: comment.mod.distinguish(sticky=True), reddit_throttle_secs=1)
        self.reddit_call(lambda: comment.mod.lock(), reddit_throttle_secs=1)

    def remove_content(self, removal_reason, content):
        print(f"Removing content, reason: {removal_reason}")
        self.reddit_call(lambda: content.mod.remove(mod_note=removal_reason))

    def reddit_call(self, callback, reddit_throttle_secs=5):
        if Settings.is_dry_run:
            print("\tDRY RUN!!!")
            return
        # throttle reddit calls to prevent reddit throttling
        elapsed_time = time.time() - self.last_call_time
        if elapsed_time < reddit_throttle_secs:
            time.sleep(reddit_throttle_secs - elapsed_time)

        # retry reddit exceptions, such as throttling or reddit issues
        max_retries = 3
        initial_backoff_time_secs = 5
        for i in range(max_retries):
            try:
                result = callback()
                self.last_call_time = time.time()
                return result
            except RedditAPIException as e:
                message = f"Reddit API exception: {e}\n```{traceback.format_exc()}```"
                self.discord_client.send_error_msg(message)
                print(message)

                backoff_time_secs = initial_backoff_time_secs ** i
                print(f'Retrying in {backoff_time_secs} seconds...')
                time.sleep(backoff_time_secs)
