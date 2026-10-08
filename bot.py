import os
import sqlite3
from datetime import datetime, timezone

import discord
from discord import app_commands
from discord.ext import commands
from discord.ui import Modal, TextInput, View, Button
from dotenv import load_dotenv

load_dotenv()

TOKEN = os.getenv("DISCORD_TOKEN")
GUILD_ID = os.getenv("GUILD_ID")

DB_FILE = "tierbot.db"

TIERS = ["HT1", "LT1", "HT2", "LT2", "HT3", "LT3", "HT4", "LT4", "HT5", "LT5", "Unranked"]
GAMEMODES = ["Sword", "Axe", "Mace", "UHC", "Pot", "NethPot", "Crystal", "SMP", "Bow", "Sumo"]
TIER_SCORES = {
    "HT1": 1, "LT1": 2, "HT2": 3, "LT2": 4, "HT3": 5,
    "LT3": 6, "HT4": 7, "LT4": 8, "HT5": 9, "LT5": 10
}

intents = discord.Intents.default()
intents.guilds = True
intents.members = True
bot = commands.Bot(command_prefix="!", intents=intents)


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def db():
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with db() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS players (
                minecraft_username TEXT PRIMARY KEY,
                discord_id INTEGER,
                created_at TEXT NOT NULL
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS player_tiers (
                minecraft_username TEXT NOT NULL,
                gamemode TEXT NOT NULL,
                tier TEXT NOT NULL DEFAULT 'Unranked',
                wins INTEGER NOT NULL DEFAULT 0,
                losses INTEGER NOT NULL DEFAULT 0,
                tests INTEGER NOT NULL DEFAULT 0,
                tester TEXT,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (minecraft_username, gamemode)
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS queue (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                minecraft_username TEXT NOT NULL,
                discord_id INTEGER NOT NULL,
                gamemode TEXT NOT NULL,
                joined_at TEXT NOT NULL,
                UNIQUE(discord_id, gamemode)
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS tests (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                minecraft_username TEXT NOT NULL,
                discord_id INTEGER NOT NULL,
                tester_id INTEGER,
                tester_name TEXT,
                gamemode TEXT NOT NULL,
                result TEXT,
                tier TEXT,
                notes TEXT,
                status TEXT NOT NULL DEFAULT 'Open',
                ticket_channel_id INTEGER,
                created_at TEXT NOT NULL,
                completed_at TEXT
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS appeals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                minecraft_username TEXT NOT NULL,
                discord_id INTEGER NOT NULL,
                gamemode TEXT NOT NULL,
                reason TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'Open',
                channel_id INTEGER,
                created_at TEXT NOT NULL,
                resolved_at TEXT
            )
        """)


def ensure_player(username, discord_id=None):
    username = username.strip()
    with db() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO players (minecraft_username, discord_id, created_at) VALUES (?, ?, ?)",
            (username, discord_id, utc_now())
        )
        if discord_id is not None:
            conn.execute(
                "UPDATE players SET discord_id = ? WHERE minecraft_username = ?",
                (discord_id, username)
            )
        for mode in GAMEMODES:
            conn.execute("""
                INSERT OR IGNORE INTO player_tiers
                (minecraft_username, gamemode, tier, updated_at)
                VALUES (?, ?, 'Unranked', ?)
            """, (username, mode, utc_now()))


def valid_gamemode(mode):
    return mode if mode in GAMEMODES else None


def get_tiers(username):
    ensure_player(username)
    with db() as conn:
        rows = conn.execute("""
            SELECT * FROM player_tiers
            WHERE minecraft_username = ?
            ORDER BY gamemode
        """, (username,)).fetchall()
    return rows


def tier_score(tier):
    return TIER_SCORES.get(tier)


def overall_rank(username):
    rows = get_tiers(username)
    ranked = [tier_score(r["tier"]) for r in rows if tier_score(r["tier"]) is not None]

    if len(ranked) < 2:
        return "Unranked"

    average = sum(ranked) / len(ranked)
    return min(TIER_SCORES, key=lambda t: abs(TIER_SCORES[t] - average))


def is_staff(interaction):
    return interaction.user.guild_permissions.administrator or interaction.user.guild_permissions.manage_guild


def tester_role(guild):
    return discord.utils.get(guild.roles, name="Tester")


def is_tester(interaction):
    if is_staff(interaction):
        return True
    role = tester_role(interaction.guild)
    return role is not None and role in interaction.user.roles


async def ensure_tester_role(guild):
    role = tester_role(guild)
    if role is None:
        role = await guild.create_role(
            name="Tester",
            reason="Tier bot tester role"
        )
    return role


async def get_or_create_category(guild, name):
    category = discord.utils.get(guild.categories, name=name)
    if category is None:
        category = await guild.create_category(name)
    return category


async def create_private_channel(guild, category_name, channel_name, member):
    category = await get_or_create_category(guild, category_name)
    overwrites = {
        guild.default_role: discord.PermissionOverwrite(view_channel=False),
        member: discord.PermissionOverwrite(
            view_channel=True, send_messages=True, read_message_history=True
        ),
        guild.me: discord.PermissionOverwrite(
            view_channel=True, send_messages=True, read_message_history=True,
            manage_channels=True
        )
    }

    role = tester_role(guild)
    if role:
        overwrites[role] = discord.PermissionOverwrite(
            view_channel=True, send_messages=True, read_message_history=True
        )

    return await guild.create_text_channel(
        channel_name[:95],
        category=category,
        overwrites=overwrites
    )


def tier_embed(username):
    rows = get_tiers(username)
    overall = overall_rank(username)

    embed = discord.Embed(
        title=f"⚔️ {username} — Tier Profile",
        description=f"**Overall Rank:** `{overall}`",
        color=discord.Color.blurple()
    )

    for row in rows:
        embed.add_field(
            name=row["gamemode"],
            value=(
                f"Tier: **{row['tier']}**\n"
                f"W/L: `{row['wins']}/{row['losses']}`\n"
                f"Tests: `{row['tests']}`"
            ),
            inline=True
        )

    return embed


class TestResultModal(Modal, title="Submit Test Result"):
    tier = TextInput(
        label="Tier",
        placeholder="Example: HT3",
        max_length=5
    )
    result = TextInput(
        label="Result",
        placeholder="Win or Loss",
        max_length=10
    )
    notes = TextInput(
        label="Notes",
        placeholder="Optional notes about the test",
        required=False,
        style=discord.TextStyle.paragraph,
        max_length=1000
    )

    def __init__(self, test_id):
        super().__init__()
        self.test_id = test_id

    async def on_submit(self, interaction):
        if not is_tester(interaction):
            await interaction.response.send_message("❌ You need the **Tester** role.", ephemeral=True)
            return

        tier = self.tier.value.upper().strip()
        result = self.result.value.lower().strip()

        if tier not in TIERS or tier == "Unranked":
            await interaction.response.send_message(
                "❌ Invalid tier. Use HT1, LT1, HT2, LT2, HT3, LT3, HT4, LT4, HT5 or LT5.",
                ephemeral=True
            )
            return

        if result not in ("win", "loss"):
            await interaction.response.send_message("❌ Result must be `Win` or `Loss`.", ephemeral=True)
            return

        with db() as conn:
            test = conn.execute("SELECT * FROM tests WHERE id = ?", (self.test_id,)).fetchone()

        if not test:
            await interaction.response.send_message("❌ Test not found.", ephemeral=True)
            return

        with db() as conn:
            conn.execute("""
                UPDATE tests
                SET tester_id = ?, tester_name = ?, result = ?, tier = ?,
                    notes = ?, status = 'Completed', completed_at = ?
                WHERE id = ?
            """, (
                interaction.user.id, interaction.user.display_name,
                result, tier, self.notes.value.strip(), utc_now(), self.test_id
            ))

            win = 1 if result == "win" else 0
            loss = 1 if result == "loss" else 0

            conn.execute("""
                UPDATE player_tiers
                SET tier = ?, wins = wins + ?, losses = losses + ?,
                    tests = tests + 1, tester = ?, updated_at = ?
                WHERE minecraft_username = ? AND gamemode = ?
            """, (
                tier, win, loss, interaction.user.display_name, utc_now(),
                test["minecraft_username"], test["gamemode"]
            ))

            conn.execute(
                "DELETE FROM queue WHERE discord_id = ? AND gamemode = ?",
                (test["discord_id"], test["gamemode"])
            )

        overall = overall_rank(test["minecraft_username"])

        await interaction.response.send_message(
            f"✅ Test completed for **{test['minecraft_username']}**!\n"
            f"Gamemode: **{test['gamemode']}**\n"
            f"Tier: **{tier}**\n"
            f"Overall: **{overall}**"
        )

        channel = interaction.channel
        if channel:
            try:
                await channel.edit(name=f"completed-{test['minecraft_username'].lower()}")
            except discord.HTTPException:
                pass


class TestTicketView(View):
    def __init__(self, test_id):
        super().__init__(timeout=None)
        self.test_id = test_id

    @discord.ui.button(label="Claim Test", style=discord.ButtonStyle.primary, custom_id="tierbot_claim")
    async def claim(self, interaction, button):
        if not is_tester(interaction):
            await interaction.response.send_message("❌ You need the **Tester** role.", ephemeral=True)
            return

        with db() as conn:
            test = conn.execute("SELECT * FROM tests WHERE id = ?", (self.test_id,)).fetchone()
            if not test:
                await interaction.response.send_message("❌ Test not found.", ephemeral=True)
                return

            conn.execute(
                "UPDATE tests SET tester_id = ?, tester_name = ?, status = 'Claimed' WHERE id = ?",
                (interaction.user.id, interaction.user.display_name, self.test_id)
            )

        await interaction.response.send_message(
            f"🎯 **{interaction.user.display_name}** claimed this test."
        )

    @discord.ui.button(label="Submit Result", style=discord.ButtonStyle.success, custom_id="tierbot_result")
    async def result(self, interaction, button):
        if not is_tester(interaction):
            await interaction.response.send_message("❌ You need the **Tester** role.", ephemeral=True)
            return
        await interaction.response.send_modal(TestResultModal(self.test_id))

    @discord.ui.button(label="Cancel Test", style=discord.ButtonStyle.danger, custom_id="tierbot_cancel")
    async def cancel(self, interaction, button):
        if not is_tester(interaction):
            await interaction.response.send_message("❌ You need the **Tester** role.", ephemeral=True)
            return

        with db() as conn:
            conn.execute(
                "UPDATE tests SET status = 'Cancelled', completed_at = ? WHERE id = ?",
                (utc_now(), self.test_id)
            )

        await interaction.response.send_message("🛑 Test cancelled.")
        try:
            await interaction.channel.delete()
        except discord.HTTPException:
            pass


class AppealModal(Modal, title="Tier Appeal"):
    username = TextInput(label="Minecraft Username", max_length=32)
    gamemode = TextInput(label="Gamemode", placeholder="Sword / Axe / Crystal...", max_length=20)
    reason = TextInput(
        label="Why should your tier be changed?",
        style=discord.TextStyle.paragraph,
        max_length=1500
    )

    async def on_submit(self, interaction):
        mode = valid_gamemode(self.gamemode.value.strip())
        if not mode:
            await interaction.response.send_message(
                f"❌ Invalid gamemode. Use: {', '.join(GAMEMODES)}",
                ephemeral=True
            )
            return

        username = self.username.value.strip()
        ensure_player(username, interaction.user.id)

        channel = await create_private_channel(
            interaction.guild,
            "Tier Appeals",
            f"appeal-{username.lower()}",
            interaction.user
        )

        with db() as conn:
            cur = conn.execute("""
                INSERT INTO appeals
                (minecraft_username, discord_id, gamemode, reason, channel_id, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (
                username, interaction.user.id, mode,
                self.reason.value.strip(), channel.id, utc_now()
            ))
            appeal_id = cur.lastrowid

        embed = discord.Embed(
            title="📨 Tier Appeal",
            color=discord.Color.orange()
        )
        embed.add_field(name="Player", value=username, inline=True)
        embed.add_field(name="Gamemode", value=mode, inline=True)
        embed.add_field(name="Reason", value=self.reason.value.strip(), inline=False)

        await channel.send(
            content=f"{interaction.user.mention} Staff/Testers will review this appeal.",
            embed=embed,
            view=AppealView(appeal_id)
        )

        await interaction.response.send_message(
            f"✅ Your appeal has been created: {channel.mention}",
            ephemeral=True
        )


class AppealView(View):
    def __init__(self, appeal_id):
        super().__init__(timeout=None)
        self.appeal_id = appeal_id

    @discord.ui.button(label="Accept", style=discord.ButtonStyle.success, custom_id="tierbot_appeal_accept")
    async def accept(self, interaction, button):
        if not is_staff(interaction):
            await interaction.response.send_message("❌ Staff only.", ephemeral=True)
            return

        with db() as conn:
            conn.execute(
                "UPDATE appeals SET status = 'Accepted', resolved_at = ? WHERE id = ?",
                (utc_now(), self.appeal_id)
            )
        await interaction.response.send_message("✅ Appeal accepted.")

    @discord.ui.button(label="Deny", style=discord.ButtonStyle.danger, custom_id="tierbot_appeal_deny")
    async def deny(self, interaction, button):
        if not is_staff(interaction):
            await interaction.response.send_message("❌ Staff only.", ephemeral=True)
            return

        with db() as conn:
            conn.execute(
                "UPDATE appeals SET status = 'Denied', resolved_at = ? WHERE id = ?",
                (utc_now(), self.appeal_id)
            )
        await interaction.response.send_message("❌ Appeal denied.")


@bot.event
async def on_ready():
    print(f"Logged in as {bot.user} ({bot.user.id})")
    if GUILD_ID:
        guild = discord.Object(id=int(GUILD_ID))
        bot.tree.copy_global_to(guild=guild)
        await bot.tree.sync(guild=guild)
        print(f"Synced commands to guild {GUILD_ID}")
    else:
        await bot.tree.sync()
        print("Synced global commands")


@bot.tree.command(name="joinqueue", description="Join the testing queue.")
@app_commands.describe(username="Your Minecraft username", gamemode="Gamemode to test")
async def joinqueue(interaction, username: str, gamemode: str):
    mode = valid_gamemode(gamemode.strip())
    if not mode:
        await interaction.response.send_message(
            f"❌ Invalid gamemode. Use: {', '.join(GAMEMODES)}", ephemeral=True
        )
        return

    ensure_player(username, interaction.user.id)

    try:
        with db() as conn:
            conn.execute("""
                INSERT INTO queue (minecraft_username, discord_id, gamemode, joined_at)
                VALUES (?, ?, ?, ?)
            """, (username.strip(), interaction.user.id, mode, utc_now()))
    except sqlite3.IntegrityError:
        await interaction.response.send_message(
            f"❌ You are already in the **{mode}** queue.", ephemeral=True
        )
        return

    await interaction.response.send_message(
        f"✅ You joined the **{mode}** testing queue as **{username}**."
    )


@bot.tree.command(name="queue", description="View the testing queue for a gamemode.")
@app_commands.describe(gamemode="Gamemode")
async def queue(interaction, gamemode: str):
    mode = valid_gamemode(gamemode.strip())
    if not mode:
        await interaction.response.send_message("❌ Invalid gamemode.", ephemeral=True)
        return

    with db() as conn:
        rows = conn.execute("""
            SELECT minecraft_username, discord_id, joined_at
            FROM queue WHERE gamemode = ? ORDER BY id
        """, (mode,)).fetchall()

    embed = discord.Embed(title=f"📋 {mode} Queue", color=discord.Color.blurple())

    if not rows:
        embed.description = "The queue is currently empty."
    else:
        lines = [
            f"**{i}.** {row['minecraft_username']} — <@{row['discord_id']}>"
            for i, row in enumerate(rows, 1)
        ]
        embed.description = "\n".join(lines)

    await interaction.response.send_message(embed=embed)


@bot.tree.command(name="myqueue", description="See your current queue positions.")
async def myqueue(interaction):
    with db() as conn:
        rows = conn.execute("""
            SELECT gamemode, id, minecraft_username
            FROM queue WHERE discord_id = ? ORDER BY id
        """, (interaction.user.id,)).fetchall()

    if not rows:
        await interaction.response.send_message("You are not currently in any queues.", ephemeral=True)
        return

    lines = []
    for row in rows:
        with db() as conn:
            position = conn.execute(
                "SELECT COUNT(*) FROM queue WHERE gamemode = ? AND id <= ?",
                (row["gamemode"], row["id"])
            ).fetchone()[0]
        lines.append(f"**{row['gamemode']}** — position `{position}` as `{row['minecraft_username']}`")

    await interaction.response.send_message("\n".join(lines), ephemeral=True)


@bot.tree.command(name="leavequeue", description="Leave a testing queue.")
@app_commands.describe(gamemode="Gamemode")
async def leavequeue(interaction, gamemode: str):
    mode = valid_gamemode(gamemode.strip())
    if not mode:
        await interaction.response.send_message("❌ Invalid gamemode.", ephemeral=True)
        return

    with db() as conn:
        cur = conn.execute(
            "DELETE FROM queue WHERE discord_id = ? AND gamemode = ?",
            (interaction.user.id, mode)
        )

    if cur.rowcount == 0:
        await interaction.response.send_message("❌ You weren't in that queue.", ephemeral=True)
    else:
        await interaction.response.send_message(f"✅ You left the **{mode}** queue.")


@bot.tree.command(name="nexttest", description="Take the next player in a queue and open a private test ticket.")
@app_commands.describe(gamemode="Gamemode")
async def nexttest(interaction, gamemode: str):
    if not is_tester(interaction):
        await interaction.response.send_message("❌ You need the **Tester** role.", ephemeral=True)
        return

    mode = valid_gamemode(gamemode.strip())
    if not mode:
        await interaction.response.send_message("❌ Invalid gamemode.", ephemeral=True)
        return

    with db() as conn:
        row = conn.execute("""
            SELECT * FROM queue WHERE gamemode = ?
            ORDER BY id LIMIT 1
        """, (mode,)).fetchone()

    if not row:
        await interaction.response.send_message(f"❌ The **{mode}** queue is empty.", ephemeral=True)
        return

    member = interaction.guild.get_member(row["discord_id"])
    if member is None:
        await interaction.response.send_message(
            "❌ That Discord member is no longer in the server.", ephemeral=True
        )
        return

    channel = await create_private_channel(
        interaction.guild,
        "Tier Tests",
        f"test-{row['minecraft_username'].lower()}-{mode.lower()}",
        member
    )

    with db() as conn:
        cur = conn.execute("""
            INSERT INTO tests
            (minecraft_username, discord_id, gamemode, status, ticket_channel_id, created_at)
            VALUES (?, ?, ?, 'Open', ?, ?)
        """, (
            row["minecraft_username"], row["discord_id"], mode, channel.id, utc_now()
        ))
        test_id = cur.lastrowid
        conn.execute("DELETE FROM queue WHERE id = ?", (row["id"],))

    embed = discord.Embed(
        title="⚔️ Tier Test",
        description=(
            f"**Player:** {row['minecraft_username']}\n"
            f"**Gamemode:** {mode}\n\n"
            "A tester can claim this test below."
        ),
        color=discord.Color.green()
    )

    await channel.send(
        content=f"{member.mention} Your **{mode}** tier test is ready!",
        embed=embed,
        view=TestTicketView(test_id)
    )

    await interaction.response.send_message(
        f"✅ Opened {channel.mention} for **{row['minecraft_username']}**."
    )


@bot.tree.command(name="player", description="View a player's full tier profile.")
@app_commands.describe(username="Minecraft username")
async def player(interaction, username: str):
    await interaction.response.send_message(embed=tier_embed(username.strip()))


@bot.tree.command(name="tiers", description="View a player's tiers.")
@app_commands.describe(username="Minecraft username")
async def tiers(interaction, username: str):
    await interaction.response.send_message(embed=tier_embed(username.strip()))


@bot.tree.command(name="overall", description="View a player's overall rank.")
@app_commands.describe(username="Minecraft username")
async def overall(interaction, username: str):
    username = username.strip()
    ensure_player(username)
    await interaction.response.send_message(
        f"🏆 **{username}**'s overall rank is **{overall_rank(username)}**."
    )


@bot.tree.command(name="setrank", description="Set a player's tier for one gamemode.")
@app_commands.describe(username="Minecraft username", gamemode="Gamemode", tier="Tier")
async def setrank(interaction, username: str, gamemode: str, tier: str):
    if not is_staff(interaction):
        await interaction.response.send_message("❌ Staff only.", ephemeral=True)
        return

    mode = valid_gamemode(gamemode.strip())
    tier = tier.upper().strip()

    if not mode:
        await interaction.response.send_message("❌ Invalid gamemode.", ephemeral=True)
        return
    if tier not in TIERS or tier == "Unranked":
        await interaction.response.send_message("❌ Invalid tier.", ephemeral=True)
        return

    ensure_player(username)

    with db() as conn:
        conn.execute("""
            UPDATE player_tiers
            SET tier = ?, updated_at = ?
            WHERE minecraft_username = ? AND gamemode = ?
        """, (tier, utc_now(), username.strip(), mode))

    await interaction.response.send_message(
        f"✅ Set **{username}**'s **{mode}** tier to **{tier}**.\n"
        f"Overall: **{overall_rank(username.strip())}**"
    )


@bot.tree.command(name="gamemodes", description="List available PvP gamemodes.")
async def gamemodes(interaction):
    await interaction.response.send_message(
        "🎮 **Gamemodes:**\n" + "\n".join(f"• {m}" for m in GAMEMODES)
    )


@bot.tree.command(name="appeal", description="Submit a tier appeal.")
async def appeal(interaction):
    await interaction.response.send_modal(AppealModal())


@bot.tree.command(name="addplayer", description="Add a player to the tier database.")
@app_commands.describe(username="Minecraft username")
async def addplayer(interaction, username: str):
    if not is_staff(interaction):
        await interaction.response.send_message("❌ Staff only.", ephemeral=True)
        return

    ensure_player(username.strip(), interaction.user.id)
    await interaction.response.send_message(f"✅ Added **{username.strip()}**.")


@bot.tree.command(name="testers", description="Show the current Tester role members.")
async def testers(interaction):
    role = await ensure_tester_role(interaction.guild)
    members = [member.mention for member in role.members if not member.bot]

    if not members:
        await interaction.response.send_message("There are currently no testers.")
    else:
        await interaction.response.send_message(
            "🧪 **Testers:**\n" + "\n".join(members)
        )


@bot.tree.command(name="teststats", description="Show your tester statistics.")
async def teststats(interaction):
    if not is_tester(interaction):
        await interaction.response.send_message("❌ Tester only.", ephemeral=True)
        return

    with db() as conn:
        completed = conn.execute("""
            SELECT COUNT(*) FROM tests
            WHERE tester_id = ? AND status = 'Completed'
        """, (interaction.user.id,)).fetchone()[0]

    await interaction.response.send_message(
        f"🧪 **{interaction.user.display_name}** has completed **{completed}** tests."
    )


@bot.tree.command(name="leaderboard", description="Show players ranked by overall tier.")
async def leaderboard(interaction):
    with db() as conn:
        players = conn.execute(
            "SELECT minecraft_username FROM players ORDER BY minecraft_username"
        ).fetchall()

    ranked = []
    for row in players:
        rank = overall_rank(row["minecraft_username"])
        if rank != "Unranked":
            ranked.append((TIER_SCORES[rank], row["minecraft_username"], rank))

    ranked.sort(key=lambda x: (x[0], x[1]))

    embed = discord.Embed(
        title="🏆 Overall Leaderboard",
        color=discord.Color.gold()
    )

    if not ranked:
        embed.description = "No players have enough ranked gamemodes yet."
    else:
        embed.description = "\n".join(
            f"**{i}.** `{username}` — **{tier}**"
            for i, (_, username, tier) in enumerate(ranked[:25], 1)
        )

    await interaction.response.send_message(embed=embed)


@bot.tree.command(name="help", description="Show the tier bot commands.")
async def help_command(interaction):
    embed = discord.Embed(
        title="📚 Tier Bot Commands",
        color=discord.Color.blurple()
    )
    embed.add_field(
        name="Players",
        value=(
            "`/joinqueue` — Join a queue\n"
            "`/queue` — View a queue\n"
            "`/myqueue` — Your queue positions\n"
            "`/leavequeue` — Leave a queue\n"
            "`/player` — Full profile\n"
            "`/overall` — Overall rank\n"
            "`/gamemodes` — Gamemode list\n"
            "`/appeal` — Appeal a tier"
        ),
        inline=False
    )
    embed.add_field(
        name="Testers / Staff",
        value=(
            "`/nexttest` — Open the next test\n"
            "`/testers` — Tester list\n"
            "`/teststats` — Your test stats\n"
            "`/setrank` — Manually set a tier\n"
            "`/addplayer` — Add a player\n"
            "`/leaderboard` — Overall leaderboard"
        ),
        inline=False
    )
    await interaction.response.send_message(embed=embed)


init_db()

if not TOKEN:
    raise RuntimeError("DISCORD_TOKEN is missing. Add it as a Railway environment variable.")

bot.run(TOKEN)
