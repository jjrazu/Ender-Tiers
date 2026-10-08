# 🏆 Minecraft Tier Testing Discord Bot V3

This version supports **separate PvP tiers per gamemode AND an overall rank**.

## Gamemodes

Default modes:

- Sword
- Axe
- Mace
- UHC
- Pot
- NethPot
- Crystal
- SMP
- Bow
- Sumo

You can edit `GAMEMODES` near the top of `bot.py`.

## Separate gamemode tiers

Every player has a separate tier for every gamemode.

Example:

```text
jjrazu
────────────────────────
Sword    → HT2
Axe      → LT2
Mace     → HT3
Crystal  → HT1
UHC      → LT3
Pot      → HT2
NethPot  → Unranked
SMP      → LT2
Bow      → HT4
Sumo     → HT3
```

A test only changes the tier for the gamemode that was tested.

## Overall rank

The bot calculates an overall rank using the player's ranked gamemodes.

Tier scores:

```text
HT1 = 1
LT1 = 2
HT2 = 3
LT2 = 4
HT3 = 5
LT3 = 6
HT4 = 7
LT4 = 8
HT5 = 9
LT5 = 10
```

The bot averages the scores of all ranked gamemodes.

A player needs at least **2 ranked gamemodes** before an overall rank is displayed.

This prevents someone who has only tested one mode from immediately receiving an overall ranking.

## Important commands

### Players

`/joinqueue username gamemode`

Join a specific gamemode's testing queue.

`/queue gamemode`

See the queue for that gamemode.

`/myqueue`

See your active tests.

`/leavequeue gamemode`

Leave a waiting queue.

`/player username`

Shows:

- Overall rank
- Every gamemode
- Individual tier
- Win/loss record

`/overall username`

Shows the calculated overall rank.

`/leaderboard`

Shows players ranked by overall rank.

`/gamemodes`

Shows available modes.

`/appeal`

Creates a private appeal ticket.

### Testers

Create a Discord role named:

`Tester`

Testers can use:

`/nexttest gamemode`

This creates a private test ticket for the next player in that mode's queue.

The ticket has:

- Claim Test
- Submit Result
- Cancel Test

When a result is submitted, only that gamemode's ranking is changed.

### Staff

`/setrank username gamemode tier`

Manually set a player's tier for one specific gamemode.

`/addplayer username`

Adds a player with all modes set to Unranked.

`/teststats`

Shows tester statistics.

Administrators and users with Manage Server are treated as staff.

## Private tickets

The bot automatically creates:

```text
📁 Tier Tests
    #test-sword-player
    #test-crystal-player
    #test-mace-player

📁 Tier Appeals
    #appeal-player
```

The player and Tester role can see their ticket.

The bot needs:

- View Channels
- Send Messages
- Read Message History
- Embed Links
- Manage Channels

## Setup

1. Install Python 3.11+.
2. Create a Discord application:
   https://discord.com/developers/applications
3. Create a bot.
4. Invite it using:
   - `bot`
   - `applications.commands`
5. Copy `.env.example` to `.env`.
6. Put your token in `.env`.
7. Put your server ID in `.env`.
8. Install:

```bash
pip install -r requirements.txt
```

9. Start:

```bash
python bot.py
```

Windows users can use:

```text
start.bat
```

## GitHub security

Never upload `.env`.

Never publish your bot token.

The `.gitignore` already ignores:

```text
.env
tierbot.db
```

## Database

SQLite is used automatically.

Database:

```text
tierbot.db
```

Back it up regularly because it contains player tiers, test history, queues and appeals.

## Future upgrades

Possible additions:

- Automatic Discord tier roles per gamemode
- Player verification
- Minecraft account verification
- Tester cooldowns
- Tester availability status
- Test requirements
- Minimum matches before ranking
- Season system
- Tier-change announcements
- Public ranking cards
- Web dashboard
- Elo/MMR
- Automated queue notifications
