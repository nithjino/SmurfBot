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
uv run python src/start.py -c config.ini
```

## Config and Credentials

The bot reads credentials from `config.ini` at the repo root. The Discord token must be under the `keys` section:

```ini
[keys]
discord = your_discord_bot_token
```

## Docker

The Compose service mounts `config.ini`, tag files, and reminder files from the repo:

```text
./config.ini -> /home/smurfbot/app/config.ini
./src/tags/files -> /home/smurfbot/app/src/tags/files
./src/reminders/files -> /home/smurfbot/app/src/reminders/files
```

Create `config.ini` before starting the container. The `src/tags/files` and `src/reminders/files` directories keep bot data persistent across container restarts.

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

Reminder files are stored as `src/reminders/files/<guild_id>.json`. The bot creates one reminder file per Discord guild and removes expired reminders when it loads or lists them.

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

Tag files are stored as `src/tags/files/<guild_id>.json`.

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
