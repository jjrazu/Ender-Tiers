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
    for m in GAMEMODES:
        if m.lower() == mode.lower():
            return m
    return None


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


async def send_standalone(interaction, content=None, embed=None, view=None, ephemeral=False):
    if ephemeral:
        if not interaction.response.is_done():
            await interaction.response.send_message(content=content, embed=embed, view=view, ephemeral=True)
        else:
            await interaction.followup.send(content=content, embed=embed, view=view, ephemeral=True)
    else:
        if not interaction.response.is_done():
            await interaction.response.defer(ephemeral=True)
        msg = await interaction.channel.send(content=content, embed=embed, view=view)
        await interaction.followup.send("✅ Message sent below.", ephemeral=True)
        return msg


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


# ---------------- Modal and Ticket Views ----------------

class TicketCloseView(View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="🔒 Close Ticket", style=discord.ButtonStyle.danger, custom_id="tierbot_close_ticket")
    async def close_ticket(self, interaction: discord.Interaction, button: Button):
        if not is_staff(interaction):
            await interaction.response.send_message("❌ Staff only.", ephemeral=True)
            return
        await interaction.response.send_message("Closing ticket in 5 seconds...")
        await discord.utils.sleep_until(datetime.now(timezone.utc))
        try:
            await interaction.channel.delete()
        except discord.HTTPException:
            pass


class SupportTicketModal(Modal, title="General Support Ticket"):
    subject = TextInput(label="Subject", max_length=100)
    description = TextInput(label="How can we help?", style=discord.TextStyle.paragraph, max_length=1500)

    async def on_submit(self, interaction: discord.Interaction):
        channel = await create_private_channel(
            interaction.guild,
            "Support Tickets",
            f"support-{interaction.user.name}",
            interaction.user
        )
        embed = discord.Embed(title="🎟️ Support Ticket", color=discord.Color.blue())
        embed.add_field(name="User", value=interaction.user.mention, inline=True)
        embed.add_field(name="Subject", value=self.subject.value, inline=False)
        embed.add_field(name="Details", value=self.description.value, inline=False)

        await channel.send(content=f"{interaction.user.mention} Staff will assist you shortly.", embed=embed, view=TicketCloseView())
        await interaction.response.send_message(f"✅ Ticket created in {channel.mention}", ephemeral=True)


class AppealModal(Modal, title="Tier Appeal"):
    username = TextInput(label="Minecraft Username", max_length=32)
    gamemode = TextInput(label="Gamemode", placeholder="Sword / Axe / Crystal...", max_length=20)
    reason = TextInput(label="Why should your tier be changed?", style=discord.TextStyle.paragraph, max_length=1500)

    async def on_submit(self, interaction: discord.Interaction):
        mode = valid_gamemode(self.gamemode.value.strip())
        if not mode:
            await interaction.response.send_message(f"❌ Invalid gamemode. Use: {', '.join(GAMEMODES)}", ephemeral=True)
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
                INSERT INTO appeals (minecraft_username, discord_id, gamemode, reason, channel_id, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (username, interaction.user.id, mode, self.reason.value.strip(), channel.id, utc_now()))
            appeal_id = cur.lastrowid

        embed = discord.Embed(title="📨 Tier Appeal", color=discord.Color.orange())
        embed.add_field(name="Player", value=username, inline=True)
        embed.add_field(name="Gamemode", value=mode, inline=True)
        embed.add_field(name="Reason", value=self.reason.value.strip(), inline=False)

        await channel.send(content=f"{interaction.user.mention} Staff will review your appeal.", embed=embed, view=AppealView(appeal_id))
        await interaction.response.send_message(f"✅ Your appeal ticket has been opened: {channel.mention}", ephemeral=True)


class AppealView(View):
    def __init__(self, appeal_id):
        super().__init__(timeout=None)
        self.appeal_id = appeal_id

    @discord.ui.button(label="Accept Appeal", style=discord.ButtonStyle.success, custom_id="tierbot_appeal_accept")
    async def accept(self, interaction: discord.Interaction, button: Button):
        if not is_staff(interaction):
            await interaction.response.send_message("❌ Staff only.", ephemeral=True)
            return

        with db() as conn:
            conn.execute("UPDATE appeals SET status = 'Accepted', resolved_at = ? WHERE id = ?", (utc_now(), self.appeal_id))

        await interaction.channel.send("✅ **Appeal Accepted** by staff. Updating stats soon.")

    @discord.ui.button(label="Deny Appeal", style=discord.ButtonStyle.danger, custom_id="tierbot_appeal_deny")
    async def deny(self, interaction: discord.Interaction, button: Button):
        if not is_staff(interaction):
            await interaction.response.send_message("❌ Staff only.", ephemeral=True)
            return

        with db() as conn:
            conn.execute("UPDATE appeals SET status = 'Denied', resolved_at = ? WHERE id = ?", (utc_now(), self.appeal_id))

        await interaction.channel.send("❌ **Appeal Denied** by staff.")


class StaffAppModal(Modal, title="Staff Application"):
    mc_username = TextInput(label="Minecraft Username", max_length=32)
    age = TextInput(label="Age", max_length=3)
    experience = TextInput(label="Previous Staff Experience", style=discord.TextStyle.paragraph, max_length=1000)
    why_staff = TextInput(label="Why do you want to join staff?", style=discord.TextStyle.paragraph, max_length=1000)

    async def on_submit(self, interaction: discord.Interaction):
        channel = await create_private_channel(
            interaction.guild,
            "Staff Applications",
            f"staff-app-{self.mc_username.value.lower()}",
            interaction.user
        )
        embed = discord.Embed(title="📋 Staff Application", color=discord.Color.purple())
        embed.add_field(name="User", value=interaction.user.mention, inline=True)
        embed.add_field(name="Minecraft IGN", value=self.mc_username.value, inline=True)
        embed.add_field(name="Age", value=self.age.value, inline=True)
        embed.add_field(name="Experience", value=self.experience.value, inline=False)
        embed.add_field(name="Motivation", value=self.why_staff.value, inline=False)

        await channel.send(content="New Staff Application submitted.", embed=embed, view=TicketCloseView())
        await interaction.response.send_message(f"✅ Staff application opened: {channel.mention}", ephemeral=True)


class TesterAppModal(Modal, title="Tester Application"):
    mc_username = TextInput(label="Minecraft Username", max_length=32)
    gamemodes = TextInput(label="Gamemodes you can test", placeholder="e.g. Sword, Pot, Crystal", max_length=100)
    experience = TextInput(label="Testing/PvP Experience", style=discord.TextStyle.paragraph, max_length=1000)
    availability = TextInput(label="Weekly Availability (Hours)", max_length=50)

    async def on_submit(self, interaction: discord.Interaction):
        channel = await create_private_channel(
            interaction.guild,
            "Tester Applications",
            f"tester-app-{self.mc_username.value.lower()}",
            interaction.user
        )
        embed = discord.Embed(title="🧪 Tester Application", color=discord.Color.teal())
        embed.add_field(name="User", value=interaction.user.mention, inline=True)
        embed.add_field(name="Minecraft IGN", value=self.mc_username.value, inline=True)
        embed.add_field(name="Gamemodes", value=self.gamemodes.value, inline=False)
        embed.add_field(name="Experience", value=self.experience.value, inline=False)
        embed.add_field(name="Availability", value=self.availability.value, inline=False)

        await channel.send(content="New Tester Application submitted.", embed=embed, view=TicketCloseView())
        await interaction.response.send_message(f"✅ Tester application opened: {channel.mention}", ephemeral=True)


class MainTicketSystemView(View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="📩 Support Ticket", style=discord.ButtonStyle.primary, custom_id="tierbot_btn_support")
    async def open_support(self, interaction: discord.Interaction, button: Button):
        await interaction.response.send_modal(SupportTicketModal())

    @discord.ui.button(label="⚖️ Appeal Tier", style=discord.ButtonStyle.secondary, custom_id="tierbot_btn_appeal")
    async def open_appeal(self, interaction: discord.Interaction, button: Button):
        await interaction.response.send_modal(AppealModal())

    @discord.ui.button(label="🛡️ Staff App", style=discord.ButtonStyle.success, custom_id="tierbot_btn_staff_app")
    async def open_staff_app(self, interaction: discord.Interaction, button: Button):
        await interaction.response.send_modal(StaffAppModal())

    @discord.ui.button(label="🧪 Tester App", style=discord.ButtonStyle.success, custom_id="tierbot_btn_tester_app")
    async def open_tester_app(self, interaction: discord.Interaction, button: Button):
        await interaction.response.send_modal(TesterAppModal())


class TestResultModal(Modal, title="Submit Test Result"):
    tier = TextInput(label="Tier", placeholder="Example: HT3", max_length=5)
    result = TextInput(label="Result", placeholder="Win or Loss", max_length=10)
    notes = TextInput(label="Notes", placeholder="Optional notes about the test", required=False, style=discord.TextStyle.paragraph, max_length=1000)

    def __init__(self, test_id):
        super().__init__()
        self.test_id = test_id

    async def on_submit(self, interaction: discord.Interaction):
        if not is_tester(interaction):
            await interaction.response.send_message("❌ You need the **Tester** role.", ephemeral=True)
            return

        tier = self.tier.value.upper().strip()
        result = self.result.value.lower().strip()

        if tier not in TIERS or tier == "Unranked":
            await interaction.response.send_message("❌ Invalid tier. Use HT1-LT5.", ephemeral=True)
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
                SET tester_id = ?, tester_name = ?, result = ?, tier = ?, notes = ?, status = 'Completed', completed_at = ?
                WHERE id = ?
            """, (interaction.user.id, interaction.user.display_name, result, tier, self.notes.value.strip(), utc_now(), self.test_id))

            win = 1 if result == "win" else 0
            loss = 1 if result == "loss" else 0

            conn.execute("""
                UPDATE player_tiers
                SET tier = ?, wins = wins + ?, losses = losses + ?, tests = tests + 1, tester = ?, updated_at = ?
                WHERE minecraft_username = ? AND gamemode = ?
            """, (tier, win, loss, interaction.user.display_name, utc_now(), test["minecraft_username"], test["gamemode"]))

            conn.execute("DELETE FROM queue WHERE discord_id = ? AND gamemode = ?", (test["discord_id"], test["gamemode"]))

        overall = overall_rank(test["minecraft_username"])
        await interaction.channel.send(
            f"✅ **Test Completed** for **{test['minecraft_username']}**!\n"
            f"Gamemode: **{test['gamemode']}** | Tier: **{tier}** | Overall: **{overall}**"
        )
        await interaction.response.send_message("Result recorded successfully.", ephemeral=True)


class TestTicketView(View):
    def __init__(self, test_id):
        super().__init__(timeout=None)
        self.test_id = test_id

    @discord.ui.button(label="Claim Test", style=discord.ButtonStyle.primary, custom_id="tierbot_claim")
    async def claim(self, interaction: discord.Interaction, button: Button):
        if not is_tester(interaction):
            await interaction.response.send_message("❌ You need the **Tester** role.", ephemeral=True)
            return

        with db() as conn:
            conn.execute("UPDATE tests SET tester_id = ?, tester_name = ?, status = 'Claimed' WHERE id = ?",
                         (interaction.user.id, interaction.user.display_name, self.test_id))

        await interaction.channel.send(f"🎯 **{interaction.user.display_name}** claimed this test.")
        await interaction.response.send_message("Test claimed.", ephemeral=True)

    @discord.ui.button(label="Submit Result", style=discord.ButtonStyle.success, custom_id="tierbot_result")
    async def result(self, interaction: discord.Interaction, button: Button):
        if not is_tester(interaction):
            await interaction.response.send_message("❌ You need the **Tester** role.", ephemeral=True)
            return
        await interaction.response.send_modal(TestResultModal(self.test_id))

    @discord.ui.button(label="Cancel Test", style=discord.ButtonStyle.danger, custom_id="tierbot_cancel")
    async def cancel(self, interaction: discord.Interaction, button: Button):
        if not is_tester(interaction):
            await interaction.response.send_message("❌ You need the **Tester** role.", ephemeral=True)
            return

        with db() as conn:
            conn.execute("UPDATE tests SET status = 'Cancelled', completed_at = ? WHERE id = ?", (utc_now(), self.test_id))

        await interaction.channel.send("🛑 Test cancelled. Closing channel...")
        await discord.utils.sleep_until(datetime.now(timezone.utc))
        try:
            await interaction.channel.delete()
        except discord.HTTPException:
            pass


# ---------------- Bot Commands ----------------

@bot.event
async def on_ready():
    bot.add_view(MainTicketSystemView())
    print(f"Logged in as {bot.user} ({bot.user.id})")
    if GUILD_ID:
        guild = discord.Object(id=int(GUILD_ID))
        bot.tree.copy_global_to(guild=guild)
        await bot.tree.sync(guild=guild)
        print(f"Synced commands to guild {GUILD_ID}")
    else:
        await bot.tree.sync()
        print("Synced global commands")


@bot.tree.command(name="setup_tickets", description="Post the ticket system dashboard.")
async def setup_tickets(interaction: discord.Interaction):
    if not is_staff(interaction):
        await interaction.response.send_message("❌ Staff only.", ephemeral=True)
        return

    embed = discord.Embed(
        title="🎫 Support & Application Hub",
        description="Select an option below to open a ticket:\n\n"
                    "• **Support Ticket**: Help & General Inquiries\n"
                    "• **Appeal Tier**: Submit an appeal for a tier re-evaluation\n"
                    "• **Staff App**: Apply to join the server staff team\n"
                    "• **Tester App**: Apply to become an official tier tester",
        color=discord.Color.blurple()
    )
    await interaction.channel.send(embed=embed, view=MainTicketSystemView())
    await interaction.response.send_message("Ticket hub posted successfully.", ephemeral=True)


@bot.tree.command(name="joinqueue", description="Join the testing queue.")
@app_commands.describe(username="Your Minecraft username", gamemode="Gamemode to test")
async def joinqueue(interaction: discord.Interaction, username: str, gamemode: str):
    mode = valid_gamemode(gamemode.strip())
    if not mode:
        await send_standalone(interaction, f"❌ Invalid gamemode. Available: {', '.join(GAMEMODES)}")
        return

    ensure_player(username, interaction.user.id)

    try:
        with db() as conn:
            conn.execute("""
                INSERT INTO queue (minecraft_username, discord_id, gamemode, joined_at)
                VALUES (?, ?, ?, ?)
            """, (username.strip(), interaction.user.id, mode, utc_now()))
    except sqlite3.IntegrityError:
        await send_standalone(interaction, f"❌ You are already in the **{mode}** queue.")
        return

    await send_standalone(interaction, f"✅ **{username}** joined the **{mode}** testing queue.")


@bot.tree.command(name="queue", description="View the testing queue for a gamemode.")
@app_commands.describe(gamemode="Gamemode")
async def queue(interaction: discord.Interaction, gamemode: str):
    mode = valid_gamemode(gamemode.strip())
    if not mode:
        await send_standalone(interaction, "❌ Invalid gamemode.")
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
        lines = [f"**{i}.** {row['minecraft_username']} — <@{row['discord_id']}>" for i, row in enumerate(rows, 1)]
        embed.description = "\n".join(lines)

    await send_standalone(interaction, embed=embed)


@bot.tree.command(name="queueinfo", description="View all queued players and active testers.")
async def queueinfo(interaction: discord.Interaction):
    with db() as conn:
        queued_players = conn.execute("SELECT minecraft_username, gamemode FROM queue ORDER BY gamemode, id").fetchall()

    role = await ensure_tester_role(interaction.guild)
    testers_list = [m.mention for m in role.members if not m.bot]

    embed = discord.Embed(title="📊 Testing Queue & Testers Info", color=discord.Color.blue())

    tester_str = "\n".join(testers_list) if testers_list else "No active testers assigned."
    embed.add_field(name="🧪 Active Testers", value=tester_str, inline=False)

    if not queued_players:
        embed.add_field(name="👥 Queue Status", value="No players currently in queue.", inline=False)
    else:
        q_dict = {}
        for row in queued_players:
            q_dict.setdefault(row["gamemode"], []).append(row["minecraft_username"])

        q_summary = []
        for mode, players in q_dict.items():
            q_summary.append(f"**{mode}** ({len(players)}): " + ", ".join(f"`{p}`" for p in players))

        embed.add_field(name="👥 Players Waiting in Queue", value="\n".join(q_summary), inline=False)

    await send_standalone(interaction, embed=embed)


@bot.tree.command(name="testinginfo", description="Information about how testing works.")
async def testinginfo(interaction: discord.Interaction):
    embed = discord.Embed(
        title="⚔️ Tier Testing Information",
        description="Welcome to the Tier Testing System! Here is how to get ranked:",
        color=discord.Color.gold()
    )
    embed.add_field(
        name="1. How to Queue",
        value="Use `/joinqueue <ign> <gamemode>` to enter a queue. You will be notified when a tester claims your test.",
        inline=False
    )
    embed.add_field(
        name="2. Testing Rules",
        value="• Be online and ready when your test is opened.\n"
              "• Modded or modified clients strictly prohibited.\n"
              "• Respect testers and staff at all times.",
        inline=False
    )
    embed.add_field(
        name="3. Tiers Overview",
        value="Tiers range from **HT1** (High Tier 1) down to **LT5** (Low Tier 5).\n"
              "Your overall rank requires tests in at least 2 gamemodes.",
        inline=False
    )
    await send_standalone(interaction, embed=embed)


@bot.tree.command(name="myqueue", description="See your current queue positions.")
async def myqueue(interaction: discord.Interaction):
    with db() as conn:
        rows = conn.execute("""
            SELECT gamemode, id, minecraft_username
            FROM queue WHERE discord_id = ? ORDER BY id
        """, (interaction.user.id,)).fetchall()

    if not rows:
        await send_standalone(interaction, "You are not currently in any queues.", ephemeral=True)
        return

    lines = []
    for row in rows:
        with db() as conn:
            position = conn.execute(
                "SELECT COUNT(*) FROM queue WHERE gamemode = ? AND id <= ?",
                (row["gamemode"], row["id"])
            ).fetchone()[0]
        lines.append(f"**{row['gamemode']}** — position `{position}` as `{row['minecraft_username']}`")

    await send_standalone(interaction, "\n".join(lines), ephemeral=True)


@bot.tree.command(name="leavequeue", description="Leave a testing queue.")
@app_commands.describe(gamemode="Gamemode")
async def leavequeue(interaction: discord.Interaction, gamemode: str):
    mode = valid_gamemode(gamemode.strip())
    if not mode:
        await send_standalone(interaction, "❌ Invalid gamemode.", ephemeral=True)
        return

    with db() as conn:
        cur = conn.execute("DELETE FROM queue WHERE discord_id = ? AND gamemode = ?", (interaction.user.id, mode))

    if cur.rowcount == 0:
        await send_standalone(interaction, "❌ You weren't in that queue.", ephemeral=True)
    else:
        await send_standalone(interaction, f"✅ You left the **{mode}** queue.")


@bot.tree.command(name="nexttest", description="Take the next player in a queue and open a private test ticket.")
@app_commands.describe(gamemode="Gamemode")
async def nexttest(interaction: discord.Interaction, gamemode: str):
    if not is_tester(interaction):
        await send_standalone(interaction, "❌ You need the **Tester** role.", ephemeral=True)
        return

    mode = valid_gamemode(gamemode.strip())
    if not mode:
        await send_standalone(interaction, "❌ Invalid gamemode.", ephemeral=True)
        return

    with db() as conn:
        row = conn.execute("SELECT * FROM queue WHERE gamemode = ? ORDER BY id LIMIT 1", (mode,)).fetchone()

    if not row:
        await send_standalone(interaction, f"❌ The **{mode}** queue is empty.", ephemeral=True)
        return

    member = interaction.guild.get_member(row["discord_id"])
    if member is None:
        await send_standalone(interaction, "❌ That Discord member is no longer in the server.", ephemeral=True)
        return

    channel = await create_private_channel(
        interaction.guild,
        "Tier Tests",
        f"test-{row['minecraft_username'].lower()}-{mode.lower()}",
        member
    )

    with db() as conn:
        cur = conn.execute("""
            INSERT INTO tests (minecraft_username, discord_id, gamemode, status, ticket_channel_id, created_at)
            VALUES (?, ?, ?, 'Open', ?, ?)
        """, (row["minecraft_username"], row["discord_id"], mode, channel.id, utc_now()))
        test_id = cur.lastrowid
        conn.execute("DELETE FROM queue WHERE id = ?", (row["id"],))

    embed = discord.Embed(
        title="⚔️ Tier Test",
        description=f"**Player:** {row['minecraft_username']}\n**Gamemode:** {mode}\n\nA tester can claim this test below.",
        color=discord.Color.green()
    )

    await channel.send(content=f"{member.mention} Your **{mode}** tier test is ready!", embed=embed, view=TestTicketView(test_id))
    await send_standalone(interaction, f"✅ Opened {channel.mention} for **{row['minecraft_username']}**.")


@bot.tree.command(name="player", description="View a player's full tier profile.")
@app_commands.describe(username="Minecraft username")
async def player(interaction: discord.Interaction, username: str):
    await send_standalone(interaction, embed=tier_embed(username.strip()))


@bot.tree.command(name="tiers", description="View a player's tiers.")
@app_commands.describe(username="Minecraft username")
async def tiers(interaction: discord.Interaction, username: str):
    await send_standalone(interaction, embed=tier_embed(username.strip()))


@bot.tree.command(name="overall", description="View a player's overall rank.")
@app_commands.describe(username="Minecraft username")
async def overall(interaction: discord.Interaction, username: str):
    username = username.strip()
    ensure_player(username)
    await send_standalone(interaction, f"🏆 **{username}**'s overall rank is **{overall_rank(username)}**.")


@bot.tree.command(name="setrank", description="Set a player's tier for one gamemode.")
@app_commands.describe(username="Minecraft username", gamemode="Gamemode", tier="Tier")
async def setrank(interaction: discord.Interaction, username: str, gamemode: str, tier: str):
    if not is_staff(interaction):
        await send_standalone(interaction, "❌ Staff only.", ephemeral=True)
        return

    mode = valid_gamemode(gamemode.strip())
    tier = tier.upper().strip()

    if not mode or tier not in TIERS or tier == "Unranked":
        await send_standalone(interaction, "❌ Invalid gamemode or tier.", ephemeral=True)
        return

    ensure_player(username)

    with db() as conn:
        conn.execute("""
            UPDATE player_tiers
            SET tier = ?, updated_at = ?
            WHERE minecraft_username = ? AND gamemode = ?
        """, (tier, utc_now(), username.strip(), mode))

    await send_standalone(
        interaction,
        f"✅ Set **{username}**'s **{mode}** tier to **{tier}**.\nOverall: **{overall_rank(username.strip())}**"
    )


@bot.tree.command(name="gamemodes", description="List available PvP gamemodes.")
async def gamemodes(interaction: discord.Interaction):
    await send_standalone(interaction, "🎮 **Gamemodes:**\n" + "\n".join(f"• {m}" for m in GAMEMODES))


@bot.tree.command(name="appeal", description="Submit a tier appeal.")
async def appeal(interaction: discord.Interaction):
    await interaction.response.send_modal(AppealModal())


@bot.tree.command(name="addplayer", description="Add a player to the tier database.")
@app_commands.describe(username="Minecraft username")
async def addplayer(interaction: discord.Interaction, username: str):
    if not is_staff(interaction):
        await send_standalone(interaction, "❌ Staff only.", ephemeral=True)
        return

    ensure_player(username.strip(), interaction.user.id)
    await send_standalone(interaction, f"✅ Added **{username.strip()}**.")


@bot.tree.command(name="testers", description="Show current Tester role members.")
async def testers(interaction: discord.Interaction):
    role = await ensure_tester_role(interaction.guild)
    members = [member.mention for member in role.members if not member.bot]

    if not members:
        await send_standalone(interaction, "There are currently no testers.")
    else:
        await send_standalone(interaction, "🧪 **Testers:**\n" + "\n".join(members))


@bot.tree.command(name="teststats", description="Show your tester statistics.")
async def teststats(interaction: discord.Interaction):
    if not is_tester(interaction):
        await send_standalone(interaction, "❌ Tester only.", ephemeral=True)
        return

    with db() as conn:
        completed = conn.execute("SELECT COUNT(*) FROM tests WHERE tester_id = ? AND status = 'Completed'",
                                 (interaction.user.id,)).fetchone()[0]

    await send_standalone(interaction, f"🧪 **{interaction.user.display_name}** has completed **{completed}** tests.")


@bot.tree.command(name="leaderboard", description="Show players ranked by overall tier.")
async def leaderboard(interaction: discord.Interaction):
    with db() as conn:
        players = conn.execute("SELECT minecraft_username FROM players ORDER BY minecraft_username").fetchall()

    ranked = []
    for row in players:
        rank = overall_rank(row["minecraft_username"])
        if rank != "Unranked":
            ranked.append((TIER_SCORES[rank], row["minecraft_username"], rank))

    ranked.sort(key=lambda x: (x[0], x[1]))

    embed = discord.Embed(title="🏆 Overall Leaderboard", color=discord.Color.gold())

    if not ranked:
        embed.description = "No players have enough ranked gamemodes yet."
    else:
        header = f"{'Rank':<6} | {'Player':<18} | {'Tier':<6}\n" + "-" * 36
        rows = [f"{i:<6} | {username:<18} | {tier:<6}" for i, (_, username, tier) in enumerate(ranked[:25], 1)]
        embed.description = f"```text\n{header}\n" + "\n".join(rows) + "\n```"

    await send_standalone(interaction, embed=embed)


@bot.tree.command(name="help", description="Show tier bot commands.")
async def help_command(interaction: discord.Interaction):
    embed = discord.Embed(title="📚 Tier Bot Commands", color=discord.Color.blurple())
    embed.add_field(
        name="Players",
        value="`/joinqueue` — Join a queue\n"
              "`/queue` — View a queue\n"
              "`/myqueue` — Your queue positions\n"
              "`/leavequeue` — Leave a queue\n"
              "`/player` — Full profile\n"
              "`/overall` — Overall rank\n"
              "`/gamemodes` — Gamemode list\n"
              "`/testinginfo` — Testing guidelines\n"
              "`/queueinfo` — View queued players & testers\n"
              "`/appeal` — Appeal a tier",
        inline=False
    )
    embed.add_field(
        name="Testers / Staff",
        value="`/setup_tickets` — Post ticket dashboard\n"
              "`/nexttest` — Open the next test\n"
              "`/testers` — Tester list\n"
              "`/teststats` — Your test stats\n"
              "`/setrank` — Manually set a tier\n"
              "`/addplayer` — Add a player\n"
              "`/leaderboard` — Clean overall leaderboard",
        inline=False
    )
    await send_standalone(interaction, embed=embed)


init_db()

if not TOKEN:
    raise RuntimeError("DISCORD_TOKEN is missing. Add it as an environment variable.")

bot.run(TOKEN)
