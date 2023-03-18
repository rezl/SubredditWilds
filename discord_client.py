import asyncio
import discord
from discord.ext import commands

from settings import Settings


class DiscordClient(commands.Bot):
    def __init__(self, error_guild_name, error_guild_channel):
        super().__init__('!', intents=discord.Intents.all())
        self.error_guild_name = error_guild_name
        self.error_channel_name = error_guild_channel
        self.error_guild = None
        self.error_channel = None
        self.is_ready = False

    async def on_ready(self):
        print(f'{self.user} has connected to Discord!')
        self.error_guild = discord.utils.get(self.guilds, name=self.error_guild_name)
        self.error_channel = discord.utils.get(self.error_guild.channels, name=self.error_channel_name)
        self.is_ready = True
        guilds_msg = "\n".join([f"\t{guild.name}" for guild in self.guilds])
        startup_message = f"{self.user} is in the following guilds:\n" \
                          f"{guilds_msg}"
        print(startup_message)
        await self.error_channel.send(f"I am online for SubredditWilds script, is_dry_run={Settings.is_dry_run}")

    def send_error_msg(self, message):
        full_message = f"SubredditWilds script has had an exception. This can normally be ignored, " \
                       f"but if it's occurring frequently, may indicate a script error.\n{message}"
        if self.error_channel:
            asyncio.run_coroutine_threadsafe(self.error_channel.send(full_message), self.loop)
