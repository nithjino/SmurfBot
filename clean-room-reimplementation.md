# SmurfBot clean-room reimplementation plan

You are implementing SmurfBot from this document alone. It is a behavioral spec plus a target design. It was written by someone who read the existing code and tests so that you do not have to.

## 0. Ground rules

**Clean-room boundary. Do not read any of these, on any branch or in git history:**

- `src/smurfbot/**`
- `tests/**`
- `.venv/**/smurfbot*`
- `git show`, `git log -p`, `git diff` output that touches the paths above

Everything you need is here. If this document is silent or ambiguous, pick the simplest behavior and record the choice under a `## Decisions` heading at the bottom of this file. Do not go look at the old code to settle it.

**Design decisions are yours, not the user's.** The user has asked to stay out of design choices so their preferences do not shape this implementation. Do not stop to ask them which behavior, structure, or wording they prefer. Decide, record it under `## Decisions`, and continue. Only stop for something that is not a design choice, such as a missing credential or an action that would touch `data/`.

**You may read and keep:** `pyproject.toml`, `uv.lock`, `Makefile`, `Dockerfile`, `docker-compose.yml`, `Jenkinsfile`, `.env.example`, `.gitignore`, `.dockerignore`, `README.md`, and the JSON files under `data/` (read-only, they are real user data).

**Setup:**

1. Create and switch to a new branch `clean-room` from the current `HEAD`. The old implementation stays on `rewrite/python-3.15` for comparison.
2. Delete `src/smurfbot/` and `tests/` from the working tree before writing anything.
3. Never modify, move, or delete anything under `data/`. Test against copies in `tmp_path` or the scratch directory.
4. Do not commit or push unless the user asks.

**Context that drives every tradeoff:** this bot runs as one process for one friend group, in at most 5 Discord guilds plus a few DMs. Total state is a few hundred tags (about 60 KB of JSON) and a handful of reminders. Design for that. Anything that only pays off at larger scale is out.

**Toolchain (unchanged):** Python 3.15, `uv`, `discord.py==2.7.1`, `pydantic>=2.14`, ruff with `select = ["ALL"]`, pyright strict on `src`, pytest with `filterwarnings = ["error"]`. Do not weaken lint or type settings and do not add dependencies. `make full-check` is the gate. Ruff's docstring rules are on, so every public module, class and function needs a one-line docstring. Check discord.py API details against the installed 2.7.1 in `.venv`, not from memory.

## 1. What the bot does

A Discord bot that reacts to messages starting with a prefix (default `$`):

| Command | Purpose |
| --- | --- |
| `ping`, `git`, `help`, `mock <text>` | Stateless replies |
| `activate`, `deactivate` | Turn the bot on or off for one group |
| `tag ...` | Named, owned snippets of text per guild |
| `remind ...` | A message sent back to the same channel after a delay |

Vocabulary: a **group** is a guild or a private conversation (DM). **Activation** is per group. **Tags** and **reminders** exist only in guilds.

## 2. Target design

### 2.1 Principles

1. **One state file, held in memory.** Load `<data_dir>/state.json` once at startup. Every mutation changes the in-memory state and then rewrites the whole file.
2. **Command handling is synchronous.** `Commands.handle(request)` is a plain `def` that returns the reply text or `None`. It never awaits, so no two commands interleave and no locks are needed. A blocking write of about 60 KB per mutation is fine at this scale.
3. **Only `bot.py` and `__main__.py` import `discord`.** This falls out naturally because nothing else needs it. Do not add a test that enforces it.
4. **One reminder loop.** A single background task checks for due reminders about once a second. There are no per-reminder tasks or timers.
5. **Time is a parameter.** Functions that need the current time take `now: datetime` (timezone-aware UTC). Tests pass a fixed value. There is no clock abstraction and no fake sleep.

### 2.2 Layout

```text
src/smurfbot/
  __init__.py    docstring only
  __main__.py    main(): settings, logging, data-dir lock, load state, run the client
  config.py      Settings, ConfigError, load_settings(environ)
  core.py        Request (and CommandError if you want one)
  store.py       State models, Store (load/save), StateFileError, data_dir_lock
  commands.py    Commands: prefix parsing, activation gate, dispatch, stateless commands
  tags.py        the tag command
  reminders.py   the remind command, parse_delay, render, deliver_due
  bot.py         SmurfBot(discord.Client): message translation, sending, reminder loop
  migrate.py     one-off importer for the legacy data files (section 6)
tests/
  test_config.py  test_store.py  test_commands.py  test_tags.py
  test_reminders.py  test_bot.py  test_migrate.py
```

Size guide, not a gate: about 700 lines of `src` and 900 lines of tests. The previous implementation was 1,400 and 2,400. If you are far above the guide, something from section 7 has crept back in.

### 2.3 Contracts

Signatures below are fixed so modules and tests line up. Bodies are yours.

```python
# core.py
@dataclass(frozen=True, slots=True, kw_only=True)
class Request:
    text: str  # raw message content
    author_id: int
    guild_id: int | None  # None in a DM
    group_id: int  # guild id, or the DM channel id
    group_name: str  # guild name, or "This DM"
    channel_id: int
    is_admin: bool  # may toggle activation; always True in a DM
    attachment_url: str | None = None


# store.py
class Tag(BaseModel):  # extra="forbid"
    owner: int
    content: str


class Reminder(BaseModel):  # extra="forbid"
    guild_id: int
    channel_id: int
    user_id: int
    message: str
    due: AwareDatetime  # UTC


class State(BaseModel):  # extra="forbid"
    deactivated: set[int] = set()  # group ids; absent means active
    tags: dict[int, dict[str, Tag]] = {}  # guild id -> tag name -> tag
    reminders: list[Reminder] = []


class Store:
    state: State

    def __init__(self, path: Path) -> None: ...  # load; a missing file means an empty State
    def save(self) -> None: ...  # atomic whole-file replace


def data_dir_lock(data_dir: Path) -> ContextManager[None]: ...


# commands.py
class Commands:
    def __init__(self, store: Store, prefix: str, now: Callable[[], datetime] = utc_now) -> None: ...
    def handle(self, request: Request) -> str | None: ...


# tags.py
def tag_command(store: Store, request: Request, args: list[str]) -> str: ...


# reminders.py
def remind_command(store: Store, request: Request, args: list[str], now: datetime) -> str: ...
async def deliver_due(store: Store, send: Callable[[Reminder, str], Awaitable[None]], now: datetime) -> bool: ...


class PermanentDeliveryError(Exception): ...
```

A refusal is just a returned string. Use an internal exception for refusals only if it makes the code shorter.

### 2.4 State file

`<data_dir>/state.json`, written with `indent=2`:

```json
{
  "deactivated": [303],
  "tags": {
    "728428225268547616": {
      "cooltag": { "owner": 112784387862450176, "content": "this is a cool tag" }
    }
  },
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

Rules:

- **Load:** a missing file is an empty state and is not created until the first save. A file that is not valid JSON, is not valid UTF-8, or does not match the models raises `StateFileError` naming the path. The file is never overwritten or repaired. The bot exits.
- **Save:** write a temporary file in the same directory, flush and `fsync` it, then `os.replace` it over `state.json`. Create the data directory if needed. No directory fsync.
- **Failed save:** let the `OSError` propagate. Do not roll back memory. Memory may be ahead of disk until the next successful save. This is an accepted tradeoff.
- **Single process:** `data_dir_lock` takes a non-blocking `fcntl.flock` on `<data_dir>/.lock` for the life of the process. If it is held, raise an error whose message is `Another SmurfBot process is already using <data_dir>`. This stays because `make run` and `make up` both point at `./data`, and a second process holding stale state in memory would silently erase tags.

## 3. Behavior spec

Copy reply strings exactly. `{x}` marks interpolation.

### 3.1 Message handling (`Commands.handle`)

1. Text that does not start with the prefix returns `None`. The prefix may be any non-empty string, including a single space.
2. Strip the prefix and split on whitespace. The first token, lowercased, is the command name. The rest are `args`. No tokens at all (for example `$   `) means the command is `help`.
3. `activate` is handled first (3.3).
4. If `request.group_id` is in `deactivated`, return `None`. A deactivated group ignores everything except `activate`.
5. `deactivate` and its alias `deactive` (3.3).
6. Look up the command. An unknown name logs `Ignoring unknown command: {name}` at INFO and returns `None`. So `$500 is the cost` is silent.
7. Run the command and return its string.

Logging rule for the whole package: log command names and numeric ids only. Never log message text, arguments, tag content, or reminder text.

### 3.2 Stateless commands

| Input | Reply |
| --- | --- |
| `ping` | `pong` |
| `git` | `Here is the source code: https://github.com/nithjino/SmurfBot` |
| `help` | `The commands are: tag, git, mock, remind, activate, and deactivate. Tag and remind have their own help commands.` |
| `mock` with no args | `Usage: mock [message]` |
| `mock <words>` | Join args with single spaces, lowercase, then alternate case over non-whitespace characters starting with uppercase. `$mock  Ship IT  ` gives `ShIp It`. |

### 3.3 Activation

- `activate`: if not `request.is_admin`, reply `Only server admins can activate or deactivate this bot.` Otherwise remove the group from `deactivated`, save, and reply `{group_name} has been activated.` This works, and the denial is sent, even while the group is deactivated.
- `deactivate` / `deactive`: same permission check, add the group to `deactivated`, save, reply `{group_name} has been deactivated.`
- Groups are independent. A DM is its own group, keyed by its channel id.
- Nothing else about groups is stored. There is no group registry, no name tracking, and no handling of guild join, leave, or rename events.

### 3.4 Tags (`tag_command`)

Tags are per guild. Names are single whitespace-free tokens and are case-sensitive, as are subcommand names (`$tag CREATE x` looks up a tag called `CREATE`). Extra trailing arguments are ignored unless stated. Listing order is storage order; do not sort.

Constants: `MAX_TAGS_PER_GUILD = 500`; `RESERVED_NAMES` is exactly the set of subcommand names: `create delete edit filter gift help list owner rename`.

No args, or `help`, replies with the help text below, including in a DM. Every other form in a DM replies `Tags only work in a server.`

Help text:

```text
To create a tag: "tag create [name]" while uploading a picture or by providing a link or text after the name. To post an already created tag: "tag [name]". To see what tags are made: "tag list". To edit a tag: "tag edit [name] [new material]". To rename a tag: "tag rename [current name] [new name]".
```

"Content" for `create` and `edit` is `request.attachment_url` if it is non-empty, otherwise the remaining words joined with single spaces.

Checks run in the order listed. The first failure is the reply.

| Form | Checks, in order | Success |
| --- | --- | --- |
| `tag {name}` | exists | the tag's content |
| `tag create {name} [content]` | name given, else `Usage: tag create [name] [content]`; not reserved; not taken; under the limit; content non-empty | store with `owner = author_id`; `The tag "{name}" was created successfully` |
| `tag edit {name} [content]` | name and content given, else `Usage: tag edit [name] [new material]`; exists; owned | replace content; `The tag "{name}" has been edited` |
| `tag delete {name}` | name given, else `Usage: tag delete [name]`; exists; owned | `The tag "{name}" has been deleted` |
| `tag rename {name} {new}` | both given, else `Usage: tag rename [current name] [new name]`; exists; owned; `{new}` not reserved; `{new}` not taken | `The tag "{name}" has been renamed to "{new}"` |
| `tag gift {name} {user}` | both given, else `Usage: tag gift [name] [new owner mention or id]`; `{user}` parses; exists; owned | set owner; `The tag "{name}" has been gifted to <@{id}>` |
| `tag owner {name}` | name given, else `Usage: tag owner [name]`; exists | `The owner of "{name}" is <@{owner}>` |
| `tag list` | none | names joined with `, `, or `No tags exist` |
| `tag filter {keyword}` | keyword given, else `Usage: tag filter [keyword]` | names containing the keyword (case-sensitive substring) joined with `, `, or `No tags contain the word {keyword}` |

Failure replies:

| Check | Reply |
| --- | --- |
| exists | `The tag "{name}" does not exist` |
| owned (`owner == author_id`) | `You are not the owner of the tag "{name}"` |
| not reserved | `The tag name "{name}" is reserved for a tag command` |
| not taken | `The tag "{name}" already exists` |
| under the limit | `Tag limit reached. Delete a tag before creating a new one. Limit: 500` |
| content non-empty | `A tag needs either text content or an attachment` |
| user parses | `The new owner must be a Discord user mention or user id` |

A user parses if it is all decimal digits, or `<@digits>`, or `<@!digits>`. `<@&42>`, `-1`, and `alice` do not.

Users are always shown as `<@id>` mentions. Replies are sent with mentions disabled (3.6), so this renders a name and pings nobody. There is no username lookup.

### 3.5 Reminders

**Command (`remind_command`)**, checked in this order:

1. `args[0] == "list"`: in a DM reply `Reminders only work in a server.` Otherwise list this guild's reminders in stored order, one block each: `\nCreated by: <@{user_id}>\nReminder date: {when}\nReminder message: {message}\n`. With none, reply `No active reminders were found`.
2. Fewer than two args, or `args[0] == "help"`: reply the help text (works in a DM):
   `Format: $remind [amount of time] [message]. Example: $remind 1h check laundry\nSupport units: s (seconds), m (minutes), h (hours), or d (days).`
3. Otherwise create. In a DM reply `Reminders only work in a server.` Then `parse_delay(args[0])`. Then if the guild already has `MAX_REMINDERS_PER_GUILD = 100` reminders reply `Reminder limit reached. Wait for a reminder to complete before creating a new one. Limit: 100`. Then append a `Reminder` with `due = now + delay`, `channel_id = request.channel_id`, and `message` set to the remaining args joined with single spaces. Save, and reply ``Created reminder for <@{user_id}> to go off at {when} that says `{message}` ``.

`{when}` is a Discord timestamp: `<t:{unix_seconds}:f>`. Discord renders it in each reader's own timezone. There is no display timezone in the code.

**`parse_delay(text) -> int` seconds.** The last character, lowercased, is the unit: `s` = 1, `m` = 60, `h` = 3600, `d` = 86400. The rest must parse with `int()`. Failures, in order:

| Condition | Reply |
| --- | --- |
| unknown unit or non-integer amount (`2w`, `soon`, `xs`, `s`) | `Unsupported unit of time. Please use s (seconds), m (minutes), h (hours), or d (days)` |
| more than 31,557,600 seconds | `Why are you using this feature for a reminder that far in the future?` |
| zero or negative (`0s`, `-1h`) | `Why are you trying to set a reminder for the past?` |

A refused reminder writes nothing.

**Delivery (`deliver_due`).** One pass:

1. Snapshot the reminders with `due <= now`.
2. For each, call `await send(reminder, text)`. `text` is `<@{user_id}> reminder: {message}`, or `<@{user_id}> reminder (was due {when}): {message}` when `now - due` is at least 60 seconds.
3. Send returned: remove the reminder from memory, then save. If the save raises `OSError`, log it and carry on. The reminder is gone from memory, so the worst case is one duplicate after a restart.
4. Send raised `PermanentDeliveryError`: log a warning, then remove and save as in step 3.
5. Send raised any other `Exception`: log a warning and leave the reminder in place. The pass returns `False`.
6. `asyncio.CancelledError` propagates and the reminder stays.

Return `True` only if no delivery failed. Two identical reminders each deliver once. Delivery is at-least-once: a reminder that came due while the bot was offline is delivered on the first pass after startup, however late.

**Loop (in `bot.py`).** Start one task from `setup_hook`. It waits until the client is ready, then repeats: run `deliver_due(store, self._send_reminder, datetime.now(UTC))`, then sleep 1 second if it returned `True`, 60 seconds otherwise. An unexpected exception from a pass is logged and treated as `False`. The loop never dies on an error. Cancel it in `close()`. There is no attempt cap and no backoff: a reminder is retried every 60 seconds until it is sent or permanently refused.

### 3.6 Discord adapter (`bot.py`)

- **Intents:** start from `Intents.none()` and enable `guilds`, `guild_messages`, `dm_messages`, `message_content`. Nothing else.
- **`on_message`:** ignore messages whose author is a bot. Build a `Request`, call `commands.handle`. If it raises any `Exception`, log it with the command name and group, channel, and author ids, and reply `Sorry, something went wrong while handling that command.` If there is a reply, split it (below) and send each part to `message.channel` with `AllowedMentions.none()`. A failed send is logged and swallowed. Nothing escapes `on_message` except cancellation.
- **`Request` mapping:** `guild_id` is `message.guild.id` or `None`. `group_id` is the guild id, or `message.channel.id` in a DM. `group_name` is the guild name, or `This DM`. `is_admin` is `True` in a DM; in a guild it is `True` when the author has `guild_permissions.administrator` or `guild_permissions.manage_guild`, and `False` when the author object has no `guild_permissions`. `attachment_url` is the first attachment's URL, or `None`.
- **Long replies:** Discord rejects messages over 2,000 characters. Split a reply into parts of at most 2,000 characters, breaking at the last whitespace inside the window, or mid-word when there is none. Do not truncate replies. `tag list` in the largest guild is already 1,948 characters.
- **`_send_reminder(reminder, text)`:** send `text[:2000]` through `self.get_partial_messageable(reminder.channel_id)`, which reaches threads and needs no cache. Allowed mentions: only the user `reminder.user_id`; no everyone, roles, or replied user. On `discord.HTTPException` with status 400, 403, or 404 raise `PermanentDeliveryError`. Re-raise anything else unchanged.
- **Shutdown:** `Client.run()` already closes cleanly on SIGINT. For SIGTERM (`docker stop`), register a loop signal handler in `setup_hook` that schedules `self.close()`.

### 3.7 Configuration and startup

`load_settings(environ: Mapping[str, str]) -> Settings` (a frozen dataclass; keep the token out of its `repr`):

| Variable | Default | Rule |
| --- | --- | --- |
| `SMURFBOT_DISCORD_TOKEN` | none | required |
| `SMURFBOT_DELIM` | `$` | |
| `SMURFBOT_DATA_DIR` | `data` | `Path(value).expanduser()` |
| `SMURFBOT_LOG_LEVEL` | `INFO` | uppercased; must be DEBUG, INFO, WARNING, ERROR, or CRITICAL |

An empty value counts as unset for every variable. Other variables, including unknown `SMURFBOT_*` ones, are ignored. Problems raise `ConfigError` with a message that names the variable and never includes any value.

`main()`:

1. Load settings. On `ConfigError`, `sys.exit(message)`.
2. `logging.basicConfig` at the configured level.
3. Enter `data_dir_lock`, build `Store(data_dir / "state.json")`, build the client, and call `client.run(token, log_handler=None)`.
4. A held lock, a `StateFileError`, or `discord.LoginFailure` becomes `sys.exit(<one-line message>)` with no traceback.

Import `discord` normally at the top of the file. Do not use lazy imports.

## 4. Tests

Write new tests against the contracts in 2.3. Keep them direct: build a `Store` on `tmp_path`, build `Request`s with a small helper, call the function, and assert on the returned string and on `store.state` or the file. Command tests are plain synchronous tests. Use `asyncio.run` only for `deliver_due` and the adapter. No subprocess tests, no thread tests, no concurrency tests, no polling helpers.

Cover each of these at least once:

**test_store.py**
- missing file loads empty and is not created; save then load round-trips; save creates the directory
- invalid JSON, invalid UTF-8, wrong shape, and unknown keys each raise `StateFileError` and leave the bytes untouched
- a failed write (monkeypatch `os.fsync` or `os.replace` to raise) keeps the old file and leaves no temporary file
- the lock refuses a second holder and can be retaken after release

**test_config.py**
- all four variables read; defaults; empty values fall back; `~` expands; unknown variables ignored
- missing token and invalid log level raise `ConfigError`; the message and `repr(settings)` never contain a value

**test_commands.py**
- every row of 3.2; case-insensitive command name; no prefix; `$   ` is help; `$500 is the cost` is silent; a single-space prefix works
- unknown command returns `None` and logs the name
- a deactivated group ignores everything but `activate`; both spellings deactivate; non-admins are refused both ways, including while deactivated; a DM and a guild are independent; activation survives a reload from disk
- with `caplog` at DEBUG, message text and arguments never appear in the log

**test_tags.py**
- every row of both tables in 3.4, including each usage string
- names and subcommands are case-sensitive; every reserved name is refused by `create` and `rename`
- an attachment wins over text for `create` and `edit`; empty content is refused
- only the owner can edit, rename, delete, or gift; the new owner can after a gift
- gift accepts `42`, `<@42>`, `<@!42>` and rejects `alice`, `<@alice>`, `<@&42>`, `-1`
- the limit holds at 500; tags are separate per guild; tags survive a reload from disk
- help works in a DM; every other form replies `Tags only work in a server.`

**test_reminders.py**
- `parse_delay` for `30s`, `2M`, `3h`, `4d` and every failure row
- help forms; `list` empty and populated; create persists the exact record and reply; DM refusals; the limit holds at 100; a refused create writes nothing
- `deliver_due`: nothing due sends nothing; a due reminder sends then is removed from the file; the late note appears at 60 seconds and over; identical reminders each deliver once
- a transient error keeps the reminder and returns `False`, and the next pass delivers it; a permanent error drops it without a retry; cancellation during a send keeps it; a failed save after a send still removes it from memory
- reminders loaded from an existing file that are already overdue deliver on the first pass

**test_bot.py** (fake messages with `types.SimpleNamespace`; the client is never connected)
- intents are exactly the four listed
- `Request` mapping for a guild message and a DM, the admin rules, and the attachment URL
- bot authors are ignored
- replies are sent with no allowed mentions; a reply over 2,000 characters arrives in parts that are each within the limit and rejoin to the original words
- a raising handler produces the generic reply and a log; a failed send is swallowed
- `_send_reminder` targets `channel_id`, allows only the recipient mention, raises `PermanentDeliveryError` for 400, 403, 404, and re-raises 429, 503, and `ConnectionError` unchanged

**test_migrate.py**: see section 6.

## 5. Docs and packaging

- `README.md`: rewrite "Project layout", the reminder delivery paragraph, and the two JSON structure sections to match this document. Add a short "Migrating from 0.2" section. Update the command table for the mention and timestamp changes.
- `Dockerfile`: `mkdir -p data/tags data/reminders` becomes `mkdir -p data`.
- Remove `data/tags/.gitkeep` and `data/reminders/.gitkeep` from git and add `data/.gitkeep`. Leave the untracked JSON files in those directories alone.
- `pyproject.toml`: bump the version to `0.3.0`. Nothing else changes.

## 6. Legacy data importer (`migrate.py`)

This is the only place legacy formats appear. It is a one-off and can be deleted with its test once the live data is converted.

Run as `uv run python -m smurfbot.migrate <data_dir>`. It reads the files below, writes `<data_dir>/state.json` through `Store`, and logs a one-line summary (groups deactivated, tags per guild, reminders). It refuses to run if `state.json` already exists. It never modifies or deletes legacy files. Missing legacy files or directories are skipped. A legacy file it cannot parse aborts the run with the path in the message and writes nothing.

Legacy formats:

```jsonc
// <data_dir>/groups.json: keyed by display name. "id" may be a string or an int.
// "enabled" may be a bool or 0/1 and defaults to true. Ignore extra keys.
{ "Some Guild": { "id": "728428225268547616", "enabled": true } }
// -> deactivated = every id whose "enabled" is falsy

// <data_dir>/tags/<guild_id>.json: "id" and "owner" may be strings or ints.
{ "id": "728428225268547616", "name": "Some Guild",
  "tags": { "cooltag": { "owner": "112784387862450176", "content": "text" } } }
// -> tags[int(id)] = { name: Tag(owner=int(owner), content=content) }, order preserved

// <data_dir>/reminders/<guild_id>.json: timestamps are naive UTC, "%Y-%m-%dT%H:%M:%S".
{ "id": 728428225268547616, "name": "Some Guild",
  "reminders": [ { "user_id": 1, "name": "alice", "message": "text",
                   "created_at": "2026-06-03T14:16:47", "execution_time": "2026-06-03T14:26:47",
                   "timezone": "utc", "guild_id": 728428225268547616, "channel_id": 2 } ] }
// -> Reminder(guild_id, channel_id, user_id, message, due=execution_time as UTC); drop the rest
```

Ignore any other file in `<data_dir>`, such as `groups_dev.json`.

Tests: one fixture tree covering string and int ids, a disabled group, two guilds, and one reminder converts to the expected `State`; an existing `state.json` is refused; a corrupt legacy file aborts without writing; legacy files are byte-identical afterwards.

## 7. Do not build

Each of these existed before and was judged unnecessary at this scale. If you find yourself adding one, stop.

- A transport-neutral layer beyond the single `Request` dataclass, a composition-root class, or tests that police imports
- Lazy imports to avoid loading `discord`
- Per-guild files, a per-guild service registry, or lazy open and close of guild state
- Group name tracking, or handlers for ready-sync, guild join, guild available, or guild remove
- `asyncio.Lock`, `asyncio.to_thread`, or copy-on-write state
- Rollback of memory when a save fails
- One task per reminder, exponential backoff, attempt counters, or a clock/sleep abstraction
- Username lookups through the Discord API
- A hardcoded display timezone or `zoneinfo`
- Partial degradation when a state file is corrupt (the bot exits instead)
- Truncating stored tag or reminder text
- `discord.ext.commands`. Its argument parser treats quotes specially, which would change how tag content is read.

## 8. Deliberate changes from the current bot

These are settled. Implement them as written and do not ask the user to confirm them.

| # | Change | Why |
| --- | --- | --- |
| 1 | All state in one `state.json`; legacy files need a one-off import | Removes per-guild files, the registry, and three file formats |
| 2 | Times shown as Discord timestamps in the reader's timezone, not fixed US Eastern text | Removes the hardcoded timezone |
| 3 | Users shown as non-pinging mentions in `tag owner`, `tag gift`, and reminder replies, not looked-up usernames or raw ids | Removes the API lookup and makes handlers synchronous |
| 4 | Long replies are split across messages instead of cut with `...` | `tag list` is 52 characters from being cut in the largest guild |
| 5 | Activation replies in a DM say `This DM has been ...` | Removes DM name resolution |
| 6 | A mutation on a missing tag always says the tag does not exist; the separate `There are no tags` reply is gone | One code path |
| 7 | `tag edit` accepts an attachment, like `create` | Shared content rule |
| 8 | Reminders retry every 60 seconds until sent or refused, instead of 10 attempts with backoff; 401 is no longer treated as permanent | Simpler, and never drops a reminder during a long outage |
| 9 | An empty `SMURFBOT_DELIM` falls back to `$` instead of failing | One rule for empty variables |
| 10 | A corrupt state file stops the bot at startup | One file, so there is nothing to degrade to |
| 11 | After a failed disk write, the change may remain in memory | No rollback machinery |
| 12 | The reminder limit message no longer says "Delete or", since no delete command exists | Accuracy |
| 13 | The tag limit per guild is 500, up from 200 | The largest guild holds 194 tags, so the old limit was about to block normal use. It existed to bound list output and file size; replies are now split, and 500 tags in each of 5 guilds is still a small file |

## 9. Order of work

Run `make full-check` after each step.

1. Setup from section 0.
2. `core.py`, `store.py`, `test_store.py`.
3. `config.py`, `test_config.py`.
4. `commands.py` with stateless commands and activation, `test_commands.py`.
5. `tags.py`, `test_tags.py`.
6. `reminders.py`, `test_reminders.py`.
7. `bot.py`, `__main__.py`, `test_bot.py`.
8. `migrate.py`, `test_migrate.py`.
9. Section 5.

## 10. Done when

- `make full-check` passes with the lint and type configuration unchanged.
- Every bullet in section 4 has a test.
- Nothing in section 7 exists. `grep -rnE "asyncio\.Lock|to_thread|lazy import|zoneinfo|ext\.commands" src` prints nothing.
- Copy `data/` into the scratch directory and run the importer on the copy. It must report 2 guilds with 175 and 194 tags, 0 reminders, and 0 deactivated groups. Loading the result with `Store` must succeed.
- Report the line counts of `src` and `tests` next to the size guide, list any entries you added under `## Decisions`, and say plainly what you could not verify. A live Discord connection is not expected to be tested.

## Decisions

Choices made where this document was silent or ambiguous.

1. **`tag edit` with only an attachment.** "Name and content given" counts the attachment as content, so `$tag edit name` with an upload edits the tag. Without either, the reply is the usage string.
2. **`tag rename` order.** The renamed tag moves to the end of the listing order (remove, then insert). This is the simplest dict operation.
3. **No empty guild maps from reads.** Only `create` adds a guild's map to `state.tags`, so `$tag list` in a new guild does not write `"<id>": {}` on the next save. Deleting a guild's last tag leaves its empty map in place.
4. **`parse_delay` failures** raise `InvalidDelayError(ValueError)`. Its message is the reply string, and `remind_command` returns it.
5. **`split_reply` at whitespace** drops the single whitespace character it breaks on. Words are preserved, and a part never starts with that character. If sending one part fails, the remaining parts are skipped and the failure is logged once.
6. **Command name for error logs.** `Commands.parse(text)` is public and returns `(name, args)` or `None`. `bot.py` uses it to log the command name when `handle` raises. `Commands.prefix` is a public attribute.
7. **`SmurfBot(store, prefix)`** builds its own `Commands`, so `main()` only constructs the store and the client.
8. **`StateFileError` message** is `Cannot load {path}: {location}: {reason}`, built from the first validation error with input values left out, so file content never reaches the log. Invalid UTF-8 has its own message.
9. **Temporary file** for saves comes from `tempfile.mkstemp` in the same directory (mode 0600, name `.state.json.*.tmp`). It is unlinked on any exception, including cancellation.
10. **Timezones on load.** `due` accepts any aware offset and is not normalized. The bot only ever writes UTC, so this matters only for hand-edited files.
11. **`data_dir_lock`** creates the data directory if needed, and releases the lock explicitly on exit.
12. **`LoginFailure`** exits with `Discord rejected the token in SMURFBOT_DISCORD_TOKEN`.
13. **Importer details.** It takes `data_dir_lock` too, so it cannot run beside a live bot. Legacy tag and reminder files are read in filename order. A reminder's `guild_id` comes from the reminder entry. An unparseable `execution_time` aborts like any other bad file. If two tag files share a guild id, the later file wins. The summary line is `Imported {n} deactivated groups, tags for {g} guilds ({id}: {count}, ...), and {r} reminders`.
14. **Legacy `.gitkeep` files** were removed from the git index with `git rm --cached` and left on disk, because section 0 forbids deleting anything under `data/`.
15. **`uv.lock`** gets only the one-line version bump. Re-locking with the local uv 0.13 also raises the lock `revision`, which the Docker build's packaged uv might not read, so I edited the version line directly and confirmed it with `uv lock --check`.
16. **Test helpers** live in `tests/helpers.py` (request builders and constants), with the `store` fixture in `tests/conftest.py`. An empty `tests/__init__.py` lets tests import the helpers.
17. **`mock`** uppercases with `str.upper()`. A character that expands (for example `ß` to `SS`) still counts as one character for the alternation.
