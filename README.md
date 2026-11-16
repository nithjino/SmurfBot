# SmurfBot

SmurfBot is a small Discord bot for a private Discord group.

## Libraries used

- [discord.py](https://discordpy.readthedocs.io/en/stable/#)
- [Pydantic](https://docs.pydantic.dev/)

SmurfBot requires Python 3.15 and [uv](https://docs.astral.sh/uv/). Older uv releases, such as 0.11, can only download a pre-release of 3.15, so update uv if `uv python install 3.15` does not give you a final release.

## Project layout

```text
src/smurfbot/
  __main__.py    process entrypoint: settings, logging, data directory lock, state, client
  config.py      settings read from environment variables
  core.py        the Request type every command receives
  store.py       state models, loading and atomic saving of state.json, the data directory lock
  commands.py    prefix parsing, activation, and the stateless commands (ping, git, help, mock)
  tags.py        the tag command
  reminders.py   the remind command and reminder delivery
  bot.py         the Discord client: message translation, replies, and the reminder loop
  migrate.py     one-off importer for the 0.2 data files
```

All state lives in one file, `<data_dir>/state.json`, which is loaded once at startup and rewritten after every change. Command handling is plain synchronous Python, and only `bot.py` and `__main__.py` use discord.py, so the commands are tested without Discord. `make full-check` runs formatting, linting, type checks, and tests.

## Instructions

Before the bot can listen to a Discord server, it needs to be added to that server.

The default command delimiter is `$`.

To run locally:

```sh
uv sync
cp .env.example .env # then set SMURFBOT_DISCORD_TOKEN in .env
uv run --env-file .env smurfbot
```

By default, mutable bot data is stored under `data/` in the working directory. Set `SMURFBOT_DATA_DIR` to use a different state directory.

Only one bot process can use a data directory at a time. A second process exits with an error instead of overwriting the first one's state.

## Config and Credentials

The bot loads runtime settings from environment variables:

```sh
SMURFBOT_DELIM='$'
SMURFBOT_DATA_DIR=data
SMURFBOT_LOG_LEVEL=INFO
SMURFBOT_DISCORD_TOKEN=your_discord_bot_token
```

Keys and tokens are provided through a `.env` file, which is ignored by git. Copy `.env.example` to `.env` and fill it in. `SMURFBOT_DISCORD_TOKEN` is required; the bot will not start without it.

## Docker

The Compose service mounts a state directory from the repo:

```text
./data -> /home/smurfbot/data
```

The container sets `SMURFBOT_DATA_DIR=/home/smurfbot/data`, so `data/state.json` keeps bot data persistent across container restarts.

Docker Compose automatically reads `.env` for variable interpolation when it is in the same directory as `docker-compose.yml`, so the same `.env` file supplies `SMURFBOT_DISCORD_TOKEN` to the container.

You can use the Makefile wrappers:

```sh
make build
make up
make up-build
make down
make down-rm
```

Or use Docker Compose directly:

```sh
docker compose build
docker compose up
docker compose up --build
docker compose down
docker compose down --rmi all --volumes --remove-orphans
```

## Commands

Every command starts with the delimiter. The default delimiter is `$`.

| command | argument(s) | example |
| --- | --- | --- |
| ping | none | `$ping` |
| git | none | `$git` |
| mock | `[message]` | `$mock this is a test` |
| remind | See Reminder Command | `$remind 1h check laundry` |
| tag | See Tag System | `$tag list` |

The `help` command lists these, and `activate` and `deactivate` turn the bot on and off for a server or a DM. A deactivated server ignores every command except `activate`. Only members with the Administrator or Manage Server permission can use those two in a server. `deactive` still works as an alias for `deactivate`.

## Reminder Command

| command | argument(s) | example |
| --- | --- | --- |
| remind help | none | `$remind help` |
| remind list | none | `$remind list` |
| remind | `[duration] [message]` | `$remind 10m check the oven` |

Reminder parameters:

- `duration`: an integer followed by a unit with no space. Supported units are `s` for seconds, `m` for minutes, `h` for hours, and `d` for days.
- `message`: the reminder text. Everything after the duration is saved as the reminder message.

The bot rejects reminders longer than `31557600` seconds, which is about one year. Reminders are sent back to the channel or thread where they were created.

Reminder times are shown as Discord timestamps, so each reader sees them in their own timezone. Users are shown as mentions that do not ping anyone, except for the reminder itself, which pings the person who set it. A server can have up to 100 pending reminders.

### Reminder delivery

Pending reminders are saved before they are confirmed, so they survive restarts. The bot checks for due reminders about once a second. A reminder that came due while the bot was offline is delivered when the bot returns, with a note saying when it was due.

If sending fails, the bot retries every minute until the reminder is sent. It gives up immediately only when retrying cannot help, such as when the channel was deleted or the bot lost access. Delivery is at-least-once: a crash at the wrong moment can repeat a reminder, but it will not lose one.

### Reminder JSON structure

Reminders are stored in the `reminders` list of `<data_dir>/state.json`. Each one is removed once it has been delivered.

```json
{
  "reminders": [
    {
      "guild_id": 728428225268547616,
      "channel_id": 728428225268547619,
      "user_id": 112784387862450176,
      "message": "check the oven",
      "due": "2026-06-03T14:26:47Z"
    }
  ]
}
```

Field notes:

- `guild_id`: Discord guild ID the reminder was created in.
- `channel_id`: Discord channel or thread ID where the reminder is sent.
- `user_id`: Discord user ID to mention when the reminder fires.
- `message`: reminder text.
- `due`: UTC time when the reminder should fire.

## Tag System

| command | argument(s) | example |
| --- | --- | --- |
| create | `[tag_name] [stuff]` | `$tag create cooltag this is a cool tag` |
| edit | `[tag_name] [stuff]` | `$tag edit cooltag this is a new edit` |
| delete | `[tag_name]` | `$tag delete cooltag` |
| none | `[tag_name]` | `$tag cooltag` |
| list | none | `$tag list` |
| owner | `[tag_name]` | `$tag owner cooltag` |
| filter | `[keyword]` | `$tag filter cool` |
| gift | `[tag_name] @mention or user id` | `$tag gift cooltag @new_owner` |
| rename | `[tag_name] [new_tag_name]` | `$tag rename cooltag coolertag` |
| help | none | `$tag help` |

`[stuff]` can be a string, a link, or an uploaded image. If `[stuff]` is an uploaded image, the message text should only be:

```text
$tag create [tag_name]
```

An uploaded image also works with `edit`, and it takes the place of any text after the name.

The owner of a tag is whoever created it, or whoever it was gifted to. Only the owner can use `edit`, `delete`, `gift`, and `rename` on it. Tag names are case-sensitive, and each server can have up to 500 tags.

The `owner` command shows the owner as a mention that does not ping them. Long replies, such as `tag list` in a big server, are split across several messages.

### Tag JSON structure

Tags are stored in the `tags` map of `<data_dir>/state.json`, keyed by guild ID and then by tag name. Tags are listed in the order they were created.

```json
{
  "tags": {
    "728428225268547616": {
      "cooltag": { "owner": 112784387862450176, "content": "this is a cool tag" }
    }
  }
}
```

The other top-level key, `deactivated`, lists the IDs of servers and DM channels where the bot is turned off.

If `state.json` is not valid, the bot refuses to start and leaves the file as it is.

## Migrating from 0.2

Version 0.2 stored data in `groups.json`, `tags/<guild_id>.json`, and `reminders/<guild_id>.json`. Version 0.3 reads only `state.json`. Stop the bot, then convert the old files once:

```sh
uv run python -m smurfbot.migrate data
```

The importer writes `data/state.json` and logs how many deactivated groups, tags, and reminders it imported. It refuses to run if `state.json` already exists, and it never changes or deletes the old files, so you can remove them yourself once the bot is running on the new file. Files such as `groups_dev.json` are ignored.
