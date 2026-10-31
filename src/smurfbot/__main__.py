"""Process entrypoint: configuration, logging, signals, and shutdown."""

import asyncio
import logging
import signal
import sys

from pydantic import ValidationError

# Bad configuration should fail fast, without loading discord.py.
lazy import discord

from smurfbot.config import LogLevel, Settings, load_settings
from smurfbot.storage import DataDirLockedError, StateFileError, exclusive_data_dir
lazy from smurfbot.gateway import DiscordGateway

_logger = logging.getLogger(__name__)


def configure_logging(level: LogLevel) -> None:
    """Send application logs to stderr."""
    logging.basicConfig(
        level=level,
        format="%(asctime)s - %(levelname)s - %(name)s - %(message)s",
        datefmt="%m/%d/%Y %H:%M:%S",
    )


def describe_settings_error(error: ValidationError) -> str:
    """Summarize invalid settings, never echoing the values, which may include the token."""
    problems = (f"{'.'.join(map(str, item['loc']))}: {item['msg']}" for item in error.errors(include_input=False))
    return f"Invalid configuration: {'; '.join(problems)}"


async def serve(settings: Settings) -> None:
    """Run the bot until Discord disconnects it for good or a stop signal arrives."""
    stop_requested = asyncio.Event()
    loop = asyncio.get_running_loop()
    for stop_signal in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(stop_signal, stop_requested.set)

    async with DiscordGateway(settings) as gateway:
        connection = asyncio.create_task(gateway.start(settings.discord_token.get_secret_value()))
        stop_waiter = asyncio.create_task(stop_requested.wait())
        await asyncio.wait({connection, stop_waiter}, return_when=asyncio.FIRST_COMPLETED)
        stop_waiter.cancel()
        if stop_requested.is_set():
            _logger.info("Stop requested, shutting down")
        await gateway.close()
        await connection


def main() -> None:
    """Run the bot, turning expected startup failures into a clean exit message."""
    try:
        settings = load_settings()
    except ValidationError as error:
        sys.exit(describe_settings_error(error))

    configure_logging(settings.log_level)
    try:
        with exclusive_data_dir(settings.data_dir):
            asyncio.run(serve(settings))
    except (DataDirLockedError, StateFileError, discord.LoginFailure) as error:
        sys.exit(str(error))


if __name__ == "__main__":
    main()
