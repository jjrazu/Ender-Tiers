# Minecraft Tier List Discord Bot — Railway Edition

A Discord bot for Minecraft PvP tier-testing/list servers.

## Included systems

- Separate tiers for every gamemode
- Overall rank
- HT1 / LT1 through HT5 / LT5
- Testing queues
- Private tier-test tickets
- Tester role
- Claim Test / Submit Result / Cancel Test buttons
- Tier appeals
- Private appeal tickets
- Tester statistics
- Overall leaderboard
- SQLite database
- Slash commands
- Railway worker deployment

## Gamemodes

Sword, Axe, Mace, UHC, Pot, NethPot, Crystal, SMP, Bow and Sumo.

## 1. Create the Discord application

Go to the Discord Developer Portal:

https://discord.com/developers/applications

1. Click **New Application**.
2. Give the application a name.
3. Open **Bot**.
4. Create/reset the bot token.
5. Copy the token somewhere safe.
6. Never upload the token to GitHub.

## 2. Invite the bot

Use the application's **Installation** page in the Developer Portal.

The bot needs permissions such as:

- View Channels
- Send Messages
- Read Message History
- Embed Links
- Manage Channels
- Manage Roles (only needed if you want the bot to create the Tester role)

Make sure the bot is actually added to your tier-testing server.

## 3. Get your Server ID

In Discord:

1. User Settings
2. Advanced
3. Enable **Developer Mode**
4. Right-click your Discord server
5. Click **Copy Server ID**

Use that ID as `GUILD_ID`.

Using a GUILD_ID makes slash commands appear in that server quickly.

## 4. Upload to GitHub

Create a new GitHub repository and upload:

- bot.py
- requirements.txt
- Procfile
- .env.example
- .gitignore
- README.md

Do NOT upload `.env` or your bot token.

## 5. Deploy on Railway

Go to:

https://railway.com/

1. Create a new project.
2. Choose **Deploy from GitHub Repo**.
3. Select this repository.
4. Railway will install `requirements.txt`.
5. Railway will use the `Procfile` to start the bot.

Create these Railway variables:

`DISCORD_TOKEN`
Your Discord bot token.

`GUILD_ID`
Your Discord server ID.

Example:

DISCORD_TOKEN=your_real_token
GUILD_ID=123456789012345678

## 6. Start the bot

After deployment, Railway should start the worker.

Check the Railway deployment logs. You should see something similar to:

Logged in as YourBot
Synced commands to guild 123456789...

If the bot is online in Discord and slash commands appear, setup is complete.

## Main commands

### Players

`/joinqueue username gamemode`

`/queue gamemode`

`/myqueue`

`/leavequeue gamemode`

`/player username`

`/overall username`

`/gamemodes`

`/appeal`

### Testers / staff

`/nexttest gamemode`

`/testers`

`/teststats`

`/setrank username gamemode tier`

`/addplayer username`

`/leaderboard`

`/help`

## Tier system

Each gamemode has its own tier.

Example:

Sword: HT2
Axe: LT3
Mace: HT1
Crystal: HT3

The overall rank is calculated from the ranked gamemodes.

A player needs at least **2 ranked gamemodes** to receive an overall rank.

## Important security note

Never put your Discord bot token in:

- GitHub
- README.md
- screenshots
- Discord messages
- public code

If your token is exposed, immediately reset it in the Discord Developer Portal.

## Database

The bot automatically creates `tierbot.db`.

The database is intentionally ignored by Git so your private server data is not accidentally uploaded.

For serious production use, consider backing up the database regularly or moving to a persistent database service.
