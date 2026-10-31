# SmurfBot

SmurfBot is a small Discord bot for a private Discord group.

## Libraries used

- [discord.py](https://discordpy.readthedocs.io/en/stable/#)
- [Pydantic](https://docs.pydantic.dev/)

SmurfBot requires Python 3.15 and [uv](https://docs.astral.sh/uv/). Older uv releases, such as 0.11, can only download a pre-release of 3.15, so update uv if `uv python install 3.15` does not give you a final release.

## Project layout

```text
src/smurfbot/
  __main__.py     process entrypoint: settings, logging, signals, shutdown
  gateway.py      Discord adapter, and the only module that imports discord.py at runtime
  app.py          composition root that wires the services together
  router.py       prefix parsing, activation gating, and failure containment
  activation.py   per-group activation state
  registry.py     opens one service instance per guild
  storage.py      atomic JSON files and the data directory lock
  core.py         transport-neutral types and CommandError
  commands.py     stateless commands (ping, git, mock, help)
  tags/           TagBook holds the rules and state; TagCommand parses and words replies
  reminders/      ReminderBook holds timers and delivery; RemindCommand parses and words replies
```

Everything except `gateway.py` works with plain Python types, so the bot's behavior can be tested end to end without Discord. `make full-check` runs formatting, linting, type checks, and tests.

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

The container sets `SMURFBOT_DATA_DIR=/home/smurfbot/data`, and the `data/tags` and `data/reminders` directories keep bot data persistent across container restarts.

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

The `help` command lists these, and `activate` and `deactivate` turn the bot on and off for a server. Only members with the Administrator or Manage Server permission can use those two in a server. `deactive` still works as an alias for `deactivate`.

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

### Reminder delivery

Pending reminders are saved before they are confirmed, so they survive restarts. A reminder that came due while the bot was offline is delivered when the bot returns, with a note saying when it was due.

If sending fails, the bot retries up to 10 times, doubling the wait from 1 minute up to 1 hour, which covers several hours of outage. It gives up immediately only when retrying cannot help, such as when the channel was deleted or the bot lost access. Delivery is at-least-once: a crash at the wrong moment can repeat a reminder, but it will not lose one.

### Reminder JSON structure

Reminder files are stored as `<data_dir>/reminders/<guild_id>.json`. By default, local runs use `data/reminders/<guild_id>.json`. The bot creates one reminder file per Discord guild and removes each reminder once it has been delivered.

```json
{
  "id": 224644073795878913,
  "name": "guild name",
  "reminders": [
    {
      "user_id": 112784387862450176,
      "name": "username",
      "message": "check the oven",
      "created_at": "2026-06-03T14:16:47",
      "execution_time": "2026-06-03T14:26:47",
      "timezone": "utc",
      "guild_id": 224644073795878913,
      "channel_id": 224644073795878913
    }
  ]
}
```

Field notes:

- top-level `id`: Discord guild ID for this reminder file.
- top-level `name`: Discord guild name.
- `reminders`: active reminders for the guild.
- `user_id`: Discord user ID to mention when the reminder fires.
- reminder item `name`: Discord username captured when the reminder was created.
- `message`: reminder text.
- `created_at`: UTC creation time formatted as `YYYY-MM-DDTHH:MM:SS`.
- `execution_time`: UTC time when the reminder should fire, formatted as `YYYY-MM-DDTHH:MM:SS`.
- `timezone`: currently saved as `utc`.
- `guild_id`: Discord guild ID stored on the reminder record.
- `channel_id`: Discord channel ID where the reminder should be sent.

## Tag System

| command | argument(s) | example |
| --- | --- | --- |
| create | `[tag_name] [stuff]` | `$tag create cooltag this is a cool tag` |
| edit | `[tag_name] [stuff]` | `$tag edit cooltag this is a new edit` |
| delete | `[tag_name]` | `$tag delete cooltag` |
| none | `[tag_name]` | `$tag cooltag` |
| list | none | `$tag list` |
| owner | `[tag_name]` | `$tag owner cooltag` |
| gift | `[tag_name] @mention` | `$tag gift cooltag @new_owner` |
| rename | `[tag_name] [new_tag_name]` | `$tag rename cooltag coolertag` |
| help | none | `$tag help` |

`[stuff]` can be a string, a link, or an uploaded image. If `[stuff]` is an uploaded image, the message text should only be:

```text
$tag create [tag_name]
```

The `create`, `edit`, `delete`, `gift`, and `rename` commands can only be used by the owner of the tag. The owner is whoever created the tag.

The `owner` command returns the owner's Discord username.

### Tag JSON structure

Tag files are stored as `<data_dir>/tags/<guild_id>.json`. By default, local runs use `data/tags/<guild_id>.json`.

```json
{
  "id": "guild id goes here",
  "name": "guild name goes here",
  "tags": {
    "tag1": {
      "content": "tag1 content",
      "owner": "owner id"
    },
    "tag2": {
      "content": "tag2 content",
      "owner": "owner id"
    }
  }
}
```
