import re


class Settings:
    # set to True to prevent any bot actions (report, remove, comments)
    is_dry_run = False
    post_check_frequency_mins = 1

    check_modmail = False

    # comment mods are mods with, and only with, these permissions
    comment_mod_permissions = ["posts", "mail", "wiki"]
    # comment mods who should not have their posts checked
    comment_mod_whitelist = ["StatementBot", "CollapseBot", "CollapseTesting"]

    subreddit_wilds = None
    subreddit_removals = None
    discord_removals_server = None
    discord_removals_channel = None


class CollapseSettings(Settings):
    check_modmail = True

    subreddit_wilds = 'collapse_wilds'
    subreddit_removals = 'collapseremovals'
    discord_removals_server = 'Collapse Moderators'
    discord_removals_channel = 'fm-general'


class UFOsSettings(Settings):
    subreddit_wilds = None
    subreddit_removals = 'ufosremovals'
    discord_removals_server = 'UFO Moderators'
    discord_removals_channel = 'cm-post-removals'


class SettingsFactory:
    settings_classes = {
        'collapse': CollapseSettings,
        'ufos': UFOsSettings,
    }

    @staticmethod
    def get_settings(subreddit_name):
        # ensure only contains valid characters
        if not re.match(r'^\w+$', subreddit_name):
            raise ValueError("subreddit_name contains invalid characters")

        settings_class = SettingsFactory.settings_classes.get(subreddit_name.lower(), Settings)
        return settings_class()


