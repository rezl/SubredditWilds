from datetime import datetime, timedelta


class SubredditTracker:
    def __init__(self, subreddit, subreddit_wilds, subreddit_removals,
                 comment_mod_permissions, comment_mod_whitelist,
                 discord_removals_server, discord_removals_channel,
                 check_modmail):
        # wilds, removals, and the discord fields may be None
        self.subreddit_name = subreddit.display_name
        self.subreddit = subreddit
        self.subreddit_wilds = subreddit_wilds
        self.subreddit_removals = subreddit_removals

        self.comment_mod_permissions = comment_mod_permissions
        self.comment_mod_whitelist = comment_mod_whitelist

        self.discord_removals_server = discord_removals_server
        self.discord_removals_channel = discord_removals_channel

        self.check_modmail = check_modmail

        # detect last bot action in preference: wilds last post > removals last post > none
        self.time_last_checked = next(subreddit_wilds.new()).created_utc if subreddit_wilds else \
            next(subreddit_removals.new()).created_utc if subreddit_removals \
            else 0
        self.comment_mods_last_check = datetime.utcfromtimestamp(0)
        self.cached_comment_mods = self.get_comment_mods()

    def get_comment_mods(self):
        # refresh comment mods every day
        if datetime.utcnow() - self.comment_mods_last_check < timedelta(days=1):
            return self.cached_comment_mods

        mods = list()
        comment_mod_perms = set(self.comment_mod_permissions)
        comment_mod_whitelist = set(self.comment_mod_whitelist)
        for moderator in self.subreddit.moderator():
            if moderator.name in comment_mod_whitelist:
                continue
            if set(moderator.mod_permissions) == comment_mod_perms:
                mods.append(moderator.name)
        self.comment_mods_last_check = datetime.utcnow()
        self.cached_comment_mods = mods
        print(f"Refreshed comment mods for {self.subreddit_name}: {mods}")
        return mods
