import time
import traceback

from praw.exceptions import RedditAPIException

from settings import Settings


class RedditActionsHandler:
    max_retries = 3
    retry_delay_secs = 10

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

    def reddit_call(self, callback, reddit_throttle_secs=5):
        if Settings.is_dry_run:
            print("\tDRY RUN!!!")
            return
        # throttle reddit calls to prevent reddit throttling
        elapsed_time = time.time() - self.last_call_time
        if elapsed_time < reddit_throttle_secs:
            time.sleep(reddit_throttle_secs - elapsed_time)
        # retry reddit exceptions, such as throttling or reddit issues
        for i in range(self.max_retries):
            try:
                result = callback()
                self.last_call_time = time.time()
                return result
            except RedditAPIException as e:
                message = f"Exception in RedditRetry: {e}\n```{traceback.format_exc()}```"
                self.discord_client.send_error_msg(message)
                print(message)
                if i < self.max_retries - 1:
                    print(f"Retrying in {self.retry_delay_secs} seconds...")
                    time.sleep(self.retry_delay_secs)
                else:
                    raise e
