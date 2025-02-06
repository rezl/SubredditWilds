import re


class Settings:
    # set to True to prevent any bot actions (report, remove, comments)
    is_dry_run = True

    check_comment_toxicity = False

    # comment mods are mods with, and only with, these permissions
    comment_mod_permissions = ["posts", "mail", "wiki"]
    # comment mods who should not have their posts checked
    comment_mod_whitelist = ["StatementBot", "CollapseBot", "CollapseTesting"]

    subreddit_wilds = None
    subreddit_removals = None
    discord_removals_server = None
    discord_removals_channel = None

    google_sheet_id = None
    google_sheet_name = None


class CollapseSettings(Settings):
    check_comment_toxicity = True

    subreddit_wilds = 'collapse_wilds'
    subreddit_removals = 'collapseremovals'
    discord_removals_server = 'Collapse Moderators'
    discord_removals_channel = 'fm-general'
    discord_bans_channel = 'bans'

    google_sheet_id = '1cppV69sdHKbZG_65Z2JnDkjb7vxcC_238gFpgCqSoIU'
    google_sheet_name = 'Mod Actions'


class UFOsSettings(Settings):
    check_comment_toxicity = True

    subreddit_wilds = None
    subreddit_removals = 'ufosremovals'
    discord_removals_server = 'UFO Moderators'
    discord_removals_channel = 'post-moderation'
    discord_bans_channel = 'bot-notifications'

    google_sheet_id = '1H--XIuPwkBKad8hBTrn3oh4KFny6NJA0jXssB1IQ-Jw'
    google_sheet_name = 'Mod Actions'


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
