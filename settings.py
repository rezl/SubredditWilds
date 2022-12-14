class Settings:
    # set to True to prevent any bot actions (report, remove, comments)
    is_dry_run = False
    post_check_frequency_mins = 1
    # comment mods are mods with, and only with, these permissions
    comment_mod_permissions = ["posts", "mail", "wiki"]
    # comment mods who should not have their posts checked
    comment_mod_whitelist = ["StatementBot"]

    subreddit_wilds = None
    subreddit_removals = None
    discord_removals_server = None
    discord_removals_channel = None


class CollapseSettings(Settings):
    comment_mod_whitelist = ["CollapseBot", "StatementBot", "CollapseTesting"]

    subreddit_wilds = 'collapse_wilds'
    subreddit_removals = 'collapseremovals'
    discord_removals_server = 'Collapse Moderators'
    discord_removals_channel = 'fm-general'


class UFOsSettings(Settings):
    subreddit_wilds = None
    subreddit_removals = None
    discord_removals_server = None
    discord_removals_channel = None
