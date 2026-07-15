# SmurfBot

SmurfBot is a small Discord bot for a private Discord group.

## Libraries used

- [discord.py](https://discordpy.readthedocs.io/en/stable/#)
- [python-atomicwrites](https://github.com/untitaker/python-atomicwrites)
- [Pydantic](https://docs.pydantic.dev/)

## Instructions

Before the bot can listen to a Discord server, it needs to be added to that server.

The default command delimiter is `$`.

To run locally:

```sh
uv sync
SMURFBOT_DISCORD_TOKEN=your_discord_bot_token uv run python src/start.py
```

By default, mutable bot data is stored under `data/`. Set `SMURFBOT_DATA_DIR` to use a different state directory.

## Config and Credentials

The bot loads runtime settings from environment variables:

```sh
SMURFBOT_DELIM='$'
SMURFBOT_CONSUME_TIME=.1
SMURFBOT_REFRESH_GROUP_INTERVAL=600
SMURFBOT_DISCORD_TOKEN=your_discord_bot_token
```

If `SMURFBOT_DISCORD_TOKEN` is not set, the bot reads the Discord token from Vault. Set `HASHICORP_VAULT_URL` or `SMURFBOT_VAULT_URL`, plus one Vault auth token variable: `HASHICORP_VAULT_TOKEN`, `SMURFBOT_VAULT_TOKEN`, or `VAULT_TOKEN`.
Remote Vault URLs must use HTTPS. Plain HTTP is accepted for literal private-network and loopback IP addresses,
such as `http://192.168.2.10:8200`, to support local Vault installations.

## Docker

The Compose service mounts a state directory from the repo:

```text
./data -> /home/smurfbot/data
```

The container sets `SMURFBOT_DATA_DIR=/home/smurfbot/data`, and the `data/tags` and `data/reminders` directories keep bot data persistent across container restarts.

For Docker Compose, export the hosted Vault URL and token, or set `SMURFBOT_DISCORD_TOKEN` directly:

```sh
export HASHICORP_VAULT_URL=https://vault.example.com
export HASHICORP_VAULT_TOKEN=your_vault_token
```

You can also copy `.env.example` to `.env`; Docker Compose automatically reads `.env` for variable interpolation when it is in the same directory as `docker-compose.yml`.

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

## Reminder Command

| command | argument(s) | example |
| --- | --- | --- |
| remind help | none | `$remind help` |
| remind list | none | `$remind list` |
| remind | `[duration] [message]` | `$remind 10m check the oven` |

Reminder parameters:

- `duration`: an integer followed by a unit with no space. Supported units are `s` for seconds, `m` for minutes, `h` for hours, and `d` for days.
- `message`: the reminder text. Everything after the duration is saved as the reminder message.

The bot rejects reminders longer than `31557600` seconds, which is about one year. Reminders are sent back to the channel where they were created.

### Reminder JSON structure

Reminder files are stored as `<data_dir>/reminders/<guild_id>.json`. By default, local runs use `data/reminders/<guild_id>.json`. The bot creates one reminder file per Discord guild and removes expired reminders when it loads or lists them.

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

The `owner` command returns the owner user ID and attempts to match it to the owner's Discord username.

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
