import os
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta

import discord
from discord.ext import commands, tasks
from dotenv import load_dotenv

load_dotenv()

TOKEN = os.environ["DISCORD_TOKEN"]

IDLE_AFTER = timedelta(minutes=int(os.getenv("IDLE_MINUTES", "30")))

CHECK_EVERY_SECONDS = 30

WATCHED_CHANNELS = {
    int(cid) for cid in os.getenv("WATCHED_CHANNEL_IDS", "").split(",") if cid.strip()
}

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("chatkiller")

intents = discord.Intents.default()
bot = commands.Bot(command_prefix="!", intents=intents)


@dataclass
class ChannelState:
    last_author: str
    last_at: datetime
    fired: bool = False


state: dict[int, ChannelState] = {}


def is_watched(channel_id: int) -> bool:
    return not WATCHED_CHANNELS or channel_id in WATCHED_CHANNELS


@bot.event
async def on_message(message: discord.Message) -> None:
    if message.author.bot:
        return

    if message.guild is None:
        return

    if not is_watched(message.channel.id):
        return

    state[message.channel.id] = ChannelState(
        last_author=message.author.display_name,
        last_at=message.created_at,
    )

    await bot.process_commands(message)


@tasks.loop(seconds=CHECK_EVERY_SECONDS)
async def check_for_dead_chats() -> None:
    now = discord.utils.utcnow()

    for channel_id, st in list(state.items()):
        if st.fired:
            continue

        if now - st.last_at < IDLE_AFTER:
            continue

        channel = bot.get_channel(channel_id)
        if channel is None:
            continue

        perms = channel.permissions_for(channel.guild.me)
        if not perms.send_messages:
            log.warning("Cannot post in %s missing send_messages", channel_id)
            st.fired = True
            continue

        st.fired = True

        try:
            await channel.send(f"way to kill the chat, {st.last_author}")
        except discord.HTTPException:
            log.exception("Failed to post callout in %s", channel_id)


@check_for_dead_chats.before_loop
async def before_check() -> None:
    await bot.wait_until_ready()

@check_for_dead_chats.error
async def on_loop_error(error: BaseException) -> None:
    log.exception("Idle check loop crashed — restarting it", exc_info=error)
    check_for_dead_chats.restart()


@bot.event
async def on_ready() -> None:
    log.info("Logged in as %s (id=%s)", bot.user, bot.user.id)
    await backfill_state()

    if not check_for_dead_chats.is_running():
        check_for_dead_chats.start()


async def backfill_state() -> None:
    for channel_id in WATCHED_CHANNELS:
        channel = bot.get_channel(channel_id)
        if channel is None:
            continue

        try:
            async for message in channel.history(limit=25):
                if message.author.bot:
                    continue

                already_stale = discord.utils.utcnow() - message.created_at > IDLE_AFTER
                state[channel_id] = ChannelState(
                    last_author=message.author.display_name,
                    last_at=message.created_at,
                    fired=already_stale,
                )
                break
        except discord.HTTPException:
            log.exception("Could not read history for %s", channel_id)


if __name__ == "__main__":
    bot.run(TOKEN)
    print("success")