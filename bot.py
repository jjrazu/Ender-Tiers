import os
import sqlite3
from datetime import datetime, timezone

import discord
from discord import app_commands
from discord.ext import commands
from discord.ui import Modal, TextInput, View

from dotenv import load_dotenv

load_dotenv()

TOKEN = os.getenv("DISCORD_TOKEN")
GUILD_ID = os.getenv("GUILD_ID")
DB_FILE = os.getenv("DB_FILE", "tierbot.db")

TIERS = ["HT1", "LT1", "HT2", "LT2", "HT3", "LT3", "HT4", "LT4", "HT5", "LT5", "Unranked"]
GAMEMODES = ["Sword", "Axe", "Mace", "UHC", "Pot", "NethPot", "Crystal", "SMP", "Bow", "Sumo"]
TIER_SCORES = {
    "HT1": 1, "LT1": 2, "HT2": 3, "LT2": 4, "HT3": 5,
    "LT3": 6, "HT4": 7, "LT4": 8, "HT5": 9, "LT5": 10
}

intents = discord.Intents.default()
intents.guilds = True
intents.members = False
bot = commands.Bot(command_prefix="!", intents=intents)


# -----------------------------
# Helpers
# -----------------------------

def utc_now():
    return datetime.now(timezone.utc).isoformat()


def db():
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    return conn


async def bot_send(interaction, content=None, *, embed=None, embeds=None,
                   view=None, ephemeral=False):
    """Send as a normal follow-up message instead of making the command response look like a reply."""
    if not interaction.response.is_done():
        await interaction.response.defer(ephemeral=ephemeral)
    return await interaction.followup.send(
        content=content, embed=embed, embeds=embeds, view=view,
        ephemeral=ephemeral, wait=True
    )


def clean_channel_name(value):
    value = "".join(ch.lower() if ch.isalnum() else "-" for ch in value)
    while "--" in value:
        value = value.replace("--", "-")
    return value.strip("-")[:80] or "ticket"


def is_staff(interaction):
    return (
        interaction.user.guild_permissions.administrator
        or interaction.user.guild_permissions.manage_guild
    )


def tester_role(guild):
    return discord.utils.get(guild.roles, name="Tester")


def staff_role(guild):
    return discord.utils.get(guild.roles, name="Staff")


def is_tester(interaction):
    if is_staff(interaction):
        return True
    role = tester_role(interaction.guild)
    return role is not None and role in interaction.user.roles


async def ensure_role(guild, name, reason):
    role = discord.utils.get(guild.roles, name=name)
    if role is None:
        role = await guild.create_role(name=name, reason=reason)
    return role


async def ensure_tester_role(guild):
    return await ensure_role(guild, "Tester", "Tier bot tester role")


async def get_or_create_category(guild, name):
    category = discord.utils.get(guild.categories, name=name)
    if category is None:
        category = await guild.create_category(name)
    return category


async def create_private_channel(guild, category_name, channel_name, member,
                                  include_tester=True, include_staff=True):
    category = await get_or_create_category(guild, category_name)

    overwrites = {
        guild.default_role: discord.PermissionOverwrite(view_channel=False),
        member: discord.PermissionOverwrite(
            view_channel=True, send_messages=True, read_message_history=True
        ),
        guild.me: discord.PermissionOverwrite(
            view_channel=True, send_messages=True, read_message_history=True,
            manage_channels=True, manage_messages=True
        )
    }

    if include_tester:
        role = tester_role(guild)
        if role:
            overwrites[role] = discord.PermissionOverwrite(
                view_channel=True, send_messages=True, read_message_history=True
            )

    if include_staff:
        role = staff_role(guild)
        if role:
            overwrites[role] = discord.PermissionOverwrite(
                view_channel=True, send_messages=True, read_message_history=True
            )

    return await guild.create_text_channel(
        clean_channel_name(channel_name),
        category=category,
        overwrites=overwrites
    )


def valid_gamemode(mode):
    mode = mode.strip()
    for gm in GAMEMODES:
        if gm.lower() == mode.lower():
            return gm
    return None


# -----------------------------
# Database
# -----------------------------

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
        conn.execute("""
            CREATE TABLE IF NOT EXISTS tickets (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                discord_id INTEGER NOT NULL,
                username TEXT,
                ticket_type TEXT NOT NULL,
                channel_id INTEGER,
                status TEXT NOT NULL DEFAULT 'Open',
                created_at TEXT NOT NULL,
                closed_at TEXT
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS applications (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                discord_id INTEGER NOT NULL,
                username TEXT,
                application_type TEXT NOT NULL,
                channel_id INTEGER,
                status TEXT NOT NULL DEFAULT 'Open',
                created_at TEXT NOT NULL,
                resolved_at TEXT
            )
        """)
        conn.commit()


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


def get_tiers(username):
    ensure_player(username)
    with db() as conn:
        return conn.execute("""
            SELECT * FROM player_tiers
            WHERE minecraft_username = ?
            ORDER BY CASE gamemode
                WHEN 'Sword' THEN 1 WHEN 'Axe' THEN 2 WHEN 'Mace' THEN 3
                WHEN 'UHC' THEN 4 WHEN 'Pot' THEN 5 WHEN 'NethPot' THEN 6
                WHEN 'Crystal' THEN 7 WHEN 'SMP' THEN 8 WHEN 'Bow' THEN 9
                WHEN 'Sumo' THEN 10 ELSE 99 END
        """, (username,)).fetchall()


def tier_score(tier):
    return TIER_SCORES.get(tier)


def overall_rank(username):
    rows = get_tiers(username)
    ranked = [tier_score(r["tier"]) for r in rows if tier_score(r["tier"]) is not None]
    if len(ranked) < 2:
        return "Unranked"
    average = sum(ranked) / len(ranked)
    return min(TIER_SCORES, key=lambda t: abs(TIER_SCORES[t] - average))


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


# -----------------------------
# Persistent test / appeal views
# -----------------------------

class TestResultModal(Modal, title="Submit Test Result"):
    tier = TextInput(label="Tier", placeholder="Example: HT3", max_length=5)
    result = TextInput(label="Result", placeholder="Win or Loss", max_length=10)
    notes = TextInput(
        label="Notes", placeholder="Optional notes about the test",
        required=False, style=discord.TextStyle.paragraph, max_length=1000
    )

    def __init__(self, test_id):
        super().__init__()
        self.test_id = test_id

    async def on_submit(self, interaction):
        if not is_tester(interaction):
            await bot_send(interaction, "❌ You need the **Tester** role.", ephemeral=True)
            return

        tier = self.tier.value.upper().strip()
        result = self.result.value.lower().strip()
        if tier not in TIERS or tier == "Unranked":
            await bot_send(interaction, "❌ Invalid tier. Use HT1/LT1 through HT5/LT5.", ephemeral=True)
            return
        if result not in ("win", "loss"):
            await bot_send(interaction, "❌ Result must be `Win` or `Loss`.", ephemeral=True)
            return

        with db() as conn:
            test = conn.execute("SELECT * FROM tests WHERE id = ?", (self.test_id,)).fetchone()

        if not test or test["status"] in ("Completed", "Cancelled"):
            await bot_send(interaction, "❌ This test no longer accepts results.", ephemeral=True)
            return

        with db() as conn:
            conn.execute("""
                UPDATE tests
                SET tester_id=?, tester_name=?, result=?, tier=?,
                    notes=?, status='Completed', completed_at=?
                WHERE id=?
            """, (
                interaction.user.id, interaction.user.display_name, result, tier,
                self.notes.value.strip(), utc_now(), self.test_id
            ))
            conn.execute("""
                UPDATE player_tiers
                SET tier=?, wins=wins+?, losses=losses+?,
                    tests=tests+1, tester=?, updated_at=?
                WHERE minecraft_username=? AND gamemode=?
            """, (
                tier, 1 if result == "win" else 0, 1 if result == "loss" else 0,
                interaction.user.display_name, utc_now(),
                test["minecraft_username"], test["gamemode"]
            ))
            conn.execute(
                "DELETE FROM queue WHERE discord_id=? AND gamemode=?",
                (test["discord_id"], test["gamemode"])
            )

        await bot_send(
            interaction,
            f"✅ **Test completed**\n\n"
            f"Player: **{test['minecraft_username']}**\n"
            f"Gamemode: **{test['gamemode']}**\n"
            f"Tier: **{tier}**\n"
            f"Overall: **{overall_rank(test['minecraft_username'])}**"
        )
        try:
            await interaction.channel.edit(name=f"completed-{clean_channel_name(test['minecraft_username'])}")
        except discord.HTTPException:
            pass


class TestTicketView(View):
    def __init__(self, test_id):
        super().__init__(timeout=None)
        self.test_id = test_id

        claim = discord.ui.Button(
            label="Claim Test", style=discord.ButtonStyle.primary,
            custom_id=f"tierbot:test:claim:{test_id}"
        )
        claim.callback = self.claim
        self.add_item(claim)

        result = discord.ui.Button(
            label="Submit Result", style=discord.ButtonStyle.success,
            custom_id=f"tierbot:test:result:{test_id}"
        )
        result.callback = self.result
        self.add_item(result)

        cancel = discord.ui.Button(
            label="Cancel Test", style=discord.ButtonStyle.danger,
            custom_id=f"tierbot:test:cancel:{test_id}"
        )
        cancel.callback = self.cancel
        self.add_item(cancel)

    async def claim(self, interaction):
        if not is_tester(interaction):
            await bot_send(interaction, "❌ You need the **Tester** role.", ephemeral=True)
            return
        with db() as conn:
            test = conn.execute("SELECT * FROM tests WHERE id=?", (self.test_id,)).fetchone()
            if not test or test["status"] in ("Completed", "Cancelled"):
                await bot_send(interaction, "❌ This test is no longer active.", ephemeral=True)
                return
            if test["tester_id"] and test["tester_id"] != interaction.user.id:
                await bot_send(interaction, "❌ This test has already been claimed.", ephemeral=True)
                return
            conn.execute(
                "UPDATE tests SET tester_id=?, tester_name=?, status='Claimed' WHERE id=?",
                (interaction.user.id, interaction.user.display_name, self.test_id)
            )
        await bot_send(interaction, f"🎯 **{interaction.user.display_name}** claimed this test.")

    async def result(self, interaction):
        if not is_tester(interaction):
            await bot_send(interaction, "❌ You need the **Tester** role.", ephemeral=True)
            return
        await interaction.response.send_modal(TestResultModal(self.test_id))

    async def cancel(self, interaction):
        if not is_tester(interaction):
            await bot_send(interaction, "❌ You need the **Tester** role.", ephemeral=True)
            return
        with db() as conn:
            test = conn.execute("SELECT * FROM tests WHERE id=?", (self.test_id,)).fetchone()
            if not test:
                await bot_send(interaction, "❌ Test not found.", ephemeral=True)
                return
            conn.execute(
                "UPDATE tests SET status='Cancelled', completed_at=? WHERE id=?",
                (utc_now(), self.test_id)
            )
        await bot_send(interaction, "🛑 Test cancelled.")
        try:
            await interaction.channel.delete()
        except discord.HTTPException:
            pass


class AppealModal(Modal, title="Tier Appeal"):
    username = TextInput(label="Minecraft Username", max_length=32)
    gamemode = TextInput(label="Gamemode", placeholder="Sword / Axe / Crystal...", max_length=20)
    reason = TextInput(
        label="Why should your tier be changed?",
        style=discord.TextStyle.paragraph, max_length=1500
    )

    async def on_submit(self, interaction):
        mode = valid_gamemode(self.gamemode.value)
        if not mode:
            await bot_send(interaction, f"❌ Invalid gamemode. Use: {', '.join(GAMEMODES)}", ephemeral=True)
            return

        username = self.username.value.strip()
        ensure_player(username, interaction.user.id)

        channel = await create_private_channel(
            interaction.guild, "Tier Appeals",
            f"appeal-{username}-{mode}", interaction.user
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
            description="Staff can review and resolve this appeal below.",
            color=discord.Color.orange()
        )
        embed.add_field(name="Player", value=username, inline=True)
        embed.add_field(name="Gamemode", value=mode, inline=True)
        embed.add_field(name="Reason", value=self.reason.value.strip(), inline=False)

        await channel.send(
            content=f"{interaction.user.mention} Staff/Testers will review this appeal.",
            embed=embed, view=AppealView(appeal_id)
        )
        await bot_send(interaction, f"✅ Your appeal has been created: {channel.mention}", ephemeral=True)


class AppealView(View):
    def __init__(self, appeal_id):
        super().__init__(timeout=None)
        self.appeal_id = appeal_id

        accept = discord.ui.Button(
            label="Accept", style=discord.ButtonStyle.success,
            custom_id=f"tierbot:appeal:accept:{appeal_id}"
        )
        accept.callback = self.accept
        self.add_item(accept)

        deny = discord.ui.Button(
            label="Deny", style=discord.ButtonStyle.danger,
            custom_id=f"tierbot:appeal:deny:{appeal_id}"
        )
        deny.callback = self.deny
        self.add_item(deny)

        close = discord.ui.Button(
            label="Close", style=discord.ButtonStyle.secondary,
            custom_id=f"tierbot:appeal:close:{appeal_id}"
        )
        close.callback = self.close
        self.add_item(close)

    async def resolve(self, interaction, status):
        if not is_staff(interaction):
            await bot_send(interaction, "❌ Staff only.", ephemeral=True)
            return
        with db() as conn:
            conn.execute(
                "UPDATE appeals SET status=?, resolved_at=? WHERE id=? AND status='Open'",
                (status, utc_now(), self.appeal_id)
            )
        await bot_send(interaction, f"{'✅' if status == 'Accepted' else '❌'} Appeal **{status.lower()}**.")
        if interaction.channel:
            try:
                await interaction.channel.edit(name=f"{status.lower()}-appeal")
            except discord.HTTPException:
                pass

    async def accept(self, interaction):
        await self.resolve(interaction, "Accepted")

    async def deny(self, interaction):
        await self.resolve(interaction, "Denied")

    async def close(self, interaction):
        if not is_staff(interaction):
            await bot_send(interaction, "❌ Staff only.", ephemeral=True)
            return
        with db() as conn:
            conn.execute(
                "UPDATE appeals SET status='Closed', resolved_at=? WHERE id=? AND status='Open'",
                (utc_now(), self.appeal_id)
            )
        await bot_send(interaction, "🔒 Appeal closed.")
        try:
            await interaction.channel.delete()
        except discord.HTTPException:
            pass


class LegacyTestTicketView(View):
    """Compatibility view for test tickets created by older bot versions."""
    def __init__(self):
        super().__init__(timeout=None)

        for label, style, custom_id, callback in [
            ("Claim Test", discord.ButtonStyle.primary, "tierbot_claim", self.claim),
            ("Submit Result", discord.ButtonStyle.success, "tierbot_result", self.result),
            ("Cancel Test", discord.ButtonStyle.danger, "tierbot_cancel", self.cancel),
        ]:
            button = discord.ui.Button(label=label, style=style, custom_id=custom_id)
            button.callback = callback
            self.add_item(button)

    async def get_test(self, interaction):
        with db() as conn:
            return conn.execute(
                "SELECT * FROM tests WHERE ticket_channel_id=? ORDER BY id DESC LIMIT 1",
                (interaction.channel.id,)
            ).fetchone()

    async def claim(self, interaction):
        if not is_tester(interaction):
            await bot_send(interaction, "❌ You need the **Tester** role.", ephemeral=True)
            return
        test = await self.get_test(interaction)
        if not test or test["status"] in ("Completed", "Cancelled"):
            await bot_send(interaction, "❌ This test is no longer active.", ephemeral=True)
            return
        if test["tester_id"] and test["tester_id"] != interaction.user.id:
            await bot_send(interaction, "❌ This test has already been claimed.", ephemeral=True)
            return
        with db() as conn:
            conn.execute(
                "UPDATE tests SET tester_id=?, tester_name=?, status='Claimed' WHERE id=?",
                (interaction.user.id, interaction.user.display_name, test["id"])
            )
        await bot_send(interaction, f"🎯 **{interaction.user.display_name}** claimed this test.")

    async def result(self, interaction):
        if not is_tester(interaction):
            await bot_send(interaction, "❌ You need the **Tester** role.", ephemeral=True)
            return
        test = await self.get_test(interaction)
        if not test or test["status"] in ("Completed", "Cancelled"):
            await bot_send(interaction, "❌ This test is no longer active.", ephemeral=True)
            return
        await interaction.response.send_modal(TestResultModal(test["id"]))

    async def cancel(self, interaction):
        if not is_tester(interaction):
            await bot_send(interaction, "❌ You need the **Tester** role.", ephemeral=True)
            return
        test = await self.get_test(interaction)
        if not test:
            await bot_send(interaction, "❌ Test not found.", ephemeral=True)
            return
        with db() as conn:
            conn.execute(
                "UPDATE tests SET status='Cancelled', completed_at=? WHERE id=?",
                (utc_now(), test["id"])
            )
        await bot_send(interaction, "🛑 Test cancelled.")
        try:
            await interaction.channel.delete()
        except discord.HTTPException:
            pass


class LegacyAppealView(View):
    """Compatibility view for appeal tickets created by older bot versions."""
    def __init__(self):
        super().__init__(timeout=None)

        for label, style, custom_id, callback in [
            ("Accept", discord.ButtonStyle.success, "tierbot_appeal_accept", self.accept),
            ("Deny", discord.ButtonStyle.danger, "tierbot_appeal_deny", self.deny),
        ]:
            button = discord.ui.Button(label=label, style=style, custom_id=custom_id)
            button.callback = callback
            self.add_item(button)

    async def get_appeal(self, interaction):
        with db() as conn:
            return conn.execute(
                "SELECT * FROM appeals WHERE channel_id=? ORDER BY id DESC LIMIT 1",
                (interaction.channel.id,)
            ).fetchone()

    async def resolve(self, interaction, status):
        if not is_staff(interaction):
            await bot_send(interaction, "❌ Staff only.", ephemeral=True)
            return
        appeal = await self.get_appeal(interaction)
        if not appeal or appeal["status"] != "Open":
            await bot_send(interaction, "❌ This appeal is already resolved or missing.", ephemeral=True)
            return
        with db() as conn:
            conn.execute(
                "UPDATE appeals SET status=?, resolved_at=? WHERE id=?",
                (status, utc_now(), appeal["id"])
            )
        await bot_send(interaction, f"{'✅' if status == 'Accepted' else '❌'} Appeal **{status.lower()}**.")

    async def accept(self, interaction):
        await self.resolve(interaction, "Accepted")

    async def deny(self, interaction):
        await self.resolve(interaction, "Denied")


# -----------------------------
# Generic tickets
# -----------------------------

class CloseTicketView(View):
    def __init__(self, ticket_id):
        super().__init__(timeout=None)
        button = discord.ui.Button(
            label="Close Ticket", style=discord.ButtonStyle.danger,
            custom_id=f"tierbot:ticket:close:{ticket_id}"
        )
        button.callback = self.close
        self.add_item(button)
        self.ticket_id = ticket_id

    async def close(self, interaction):
        if not is_staff(interaction):
            await bot_send(interaction, "❌ Staff only.", ephemeral=True)
            return
        with db() as conn:
            conn.execute(
                "UPDATE tickets SET status='Closed', closed_at=? WHERE id=?",
                (utc_now(), self.ticket_id)
            )
        await bot_send(interaction, "🔒 Ticket closed. This channel will be deleted.")
        try:
            await interaction.channel.delete()
        except discord.HTTPException:
            pass


class TicketPanelView(View):
    def __init__(self):
        super().__init__(timeout=None)
        for label, emoji, kind, style in [
            ("General Support", "🎫", "Support", discord.ButtonStyle.primary),
            ("Tier Help", "⚔️", "Tier Help", discord.ButtonStyle.secondary),
        ]:
            button = discord.ui.Button(
                label=label, emoji=emoji, style=style,
                custom_id=f"tierbot:panel:ticket:{kind.lower().replace(' ', '-')}"
            )
            button.callback = self.open_ticket
            self.add_item(button)

    async def open_ticket(self, interaction):
        kind = "Tier Help" if "tier-help" in interaction.data.get("custom_id", "") else "Support"

        with db() as conn:
            existing = conn.execute("""
                SELECT channel_id FROM tickets
                WHERE discord_id=? AND ticket_type=? AND status='Open'
                ORDER BY id DESC LIMIT 1
            """, (interaction.user.id, kind)).fetchone()

        if existing:
            channel = interaction.guild.get_channel(existing["channel_id"])
            if channel:
                await bot_send(interaction, f"❌ You already have an open ticket: {channel.mention}", ephemeral=True)
                return

        channel = await create_private_channel(
            interaction.guild, "Tickets",
            f"{kind}-{interaction.user.display_name}", interaction.user,
            include_tester=False, include_staff=True
        )
        with db() as conn:
            cur = conn.execute("""
                INSERT INTO tickets (discord_id, username, ticket_type, channel_id, created_at)
                VALUES (?, ?, ?, ?, ?)
            """, (interaction.user.id, interaction.user.display_name, kind, channel.id, utc_now()))
            ticket_id = cur.lastrowid

        embed = discord.Embed(
            title=f"🎫 {kind} Ticket",
            description="Explain what you need help with. Staff will respond here.",
            color=discord.Color.blurple()
        )
        await channel.send(
            content=f"{interaction.user.mention}",
            embed=embed, view=CloseTicketView(ticket_id)
        )
        await bot_send(interaction, f"✅ Ticket created: {channel.mention}", ephemeral=True)


# -----------------------------
# Applications
# -----------------------------

class ApplicationModal(Modal):
    def __init__(self, application_type):
        self.application_type = application_type
        title = "Staff Application" if application_type == "Staff" else "Tester Application"
        super().__init__(title=title)

        self.username = TextInput(
            label="Minecraft Username", max_length=32,
            placeholder="Your Minecraft username"
        )
        self.experience = TextInput(
            label="Experience", style=discord.TextStyle.paragraph, max_length=1000,
            placeholder="Tell us about your PvP/testing/staff experience"
        )
        self.reason = TextInput(
            label="Why should we choose you?", style=discord.TextStyle.paragraph,
            max_length=1200
        )
        self.availability = TextInput(
            label="Availability", max_length=500,
            placeholder="When are you normally available?"
        )
        self.add_item(self.username)
        self.add_item(self.experience)
        self.add_item(self.reason)
        self.add_item(self.availability)

    async def on_submit(self, interaction):
        with db() as conn:
            existing = conn.execute("""
                SELECT channel_id FROM applications
                WHERE discord_id=? AND application_type=? AND status='Open'
                ORDER BY id DESC LIMIT 1
            """, (interaction.user.id, self.application_type)).fetchone()

        if existing:
            channel = interaction.guild.get_channel(existing["channel_id"])
            if channel:
                await bot_send(interaction, f"❌ You already have an open application: {channel.mention}", ephemeral=True)
                return

        channel = await create_private_channel(
            interaction.guild, "Applications",
            f"{self.application_type.lower()}-{interaction.user.display_name}",
            interaction.user, include_tester=True, include_staff=True
        )
        with db() as conn:
            cur = conn.execute("""
                INSERT INTO applications
                (discord_id, username, application_type, channel_id, created_at)
                VALUES (?, ?, ?, ?, ?)
            """, (
                interaction.user.id, self.username.value.strip(),
                self.application_type, channel.id, utc_now()
            ))
            application_id = cur.lastrowid

        embed = discord.Embed(
            title=f"📋 {self.application_type} Application",
            color=discord.Color.green()
        )
        embed.add_field(name="Applicant", value=interaction.user.mention, inline=True)
        embed.add_field(name="Minecraft", value=self.username.value.strip(), inline=True)
        embed.add_field(name="Experience", value=self.experience.value.strip(), inline=False)
        embed.add_field(name="Why should we choose you?", value=self.reason.value.strip(), inline=False)
        embed.add_field(name="Availability", value=self.availability.value.strip(), inline=False)

        await channel.send(
            content=f"{interaction.user.mention} Your application has been submitted.",
            embed=embed, view=ApplicationReviewView(application_id, self.application_type)
        )
        await bot_send(interaction, f"✅ Your **{self.application_type} application** has been submitted: {channel.mention}", ephemeral=True)


class ApplicationPanelView(View):
    def __init__(self):
        super().__init__(timeout=None)
        for label, kind, emoji, style in [
            ("Staff Application", "Staff", "🛡️", discord.ButtonStyle.primary),
            ("Tester Application", "Tester", "🧪", discord.ButtonStyle.success),
        ]:
            button = discord.ui.Button(
                label=label, emoji=emoji, style=style,
                custom_id=f"tierbot:panel:application:{kind.lower()}"
            )
            button.callback = self.open_application
            self.add_item(button)

    async def open_application(self, interaction):
        kind = "Staff" if interaction.data["custom_id"].endswith(":staff") else "Tester"
        await interaction.response.send_modal(ApplicationModal(kind))


class ApplicationReviewView(View):
    def __init__(self, application_id, application_type):
        super().__init__(timeout=None)
        self.application_id = application_id
        self.application_type = application_type

        approve = discord.ui.Button(
            label="Approve", style=discord.ButtonStyle.success,
            custom_id=f"tierbot:application:approve:{application_id}"
        )
        approve.callback = self.approve
        self.add_item(approve)

        deny = discord.ui.Button(
            label="Deny", style=discord.ButtonStyle.danger,
            custom_id=f"tierbot:application:deny:{application_id}"
        )
        deny.callback = self.deny
        self.add_item(deny)

        close = discord.ui.Button(
            label="Close", style=discord.ButtonStyle.secondary,
            custom_id=f"tierbot:application:close:{application_id}"
        )
        close.callback = self.close
        self.add_item(close)

    async def approve(self, interaction):
        await self.resolve(interaction, "Approved")

    async def deny(self, interaction):
        await self.resolve(interaction, "Denied")

    async def close(self, interaction):
        await self.resolve(interaction, "Closed", delete=True)

    async def resolve(self, interaction, status, delete=False):
        if not is_staff(interaction):
            await bot_send(interaction, "❌ Staff only.", ephemeral=True)
            return

        with db() as conn:
            row = conn.execute(
                "SELECT * FROM applications WHERE id=?", (self.application_id,)
            ).fetchone()
            if not row:
                await bot_send(interaction, "❌ Application not found.", ephemeral=True)
                return
            conn.execute(
                "UPDATE applications SET status=?, resolved_at=? WHERE id=?",
                (status, utc_now(), self.application_id)
            )

        if status == "Approved":
            try:
                member = await interaction.guild.fetch_member(row["discord_id"])
            except discord.NotFound:
                member = None
            if member:
                role_name = "Staff" if row["application_type"] == "Staff" else "Tester"
                role = await ensure_role(
                    interaction.guild, role_name,
                    f"Approved {row['application_type']} application"
                )
                try:
                    await member.add_roles(role, reason=f"Approved {row['application_type']} application")
                except discord.HTTPException:
                    pass

        await bot_send(interaction, f"✅ Application **{status.lower()}**.")
        if delete:
            try:
                await interaction.channel.delete()
            except discord.HTTPException:
                pass


# -----------------------------
# Commands
# -----------------------------

@bot.tree.command(name="joinqueue", description="Join the testing queue.")
@app_commands.describe(username="Your Minecraft username", gamemode="Gamemode to test")
async def joinqueue(interaction, username: str, gamemode: str):
    mode = valid_gamemode(gamemode)
    if not mode:
        await bot_send(interaction, f"❌ Invalid gamemode. Use: {', '.join(GAMEMODES)}", ephemeral=True)
        return

    ensure_player(username, interaction.user.id)
    try:
        with db() as conn:
            conn.execute(
                "INSERT INTO queue (minecraft_username, discord_id, gamemode, joined_at) VALUES (?, ?, ?, ?)",
                (username.strip(), interaction.user.id, mode, utc_now())
            )
    except sqlite3.IntegrityError:
        await bot_send(interaction, f"❌ You are already in the **{mode}** queue.", ephemeral=True)
        return

    with db() as conn:
        position = conn.execute(
            "SELECT COUNT(*) FROM queue WHERE gamemode=? AND id <= (SELECT MAX(id) FROM queue WHERE discord_id=? AND gamemode=?)",
            (mode, interaction.user.id, mode)
        ).fetchone()[0]

    await bot_send(interaction, f"✅ You joined the **{mode}** testing queue as **{username.strip()}**.\n📍 Queue position: **#{position}**")


@bot.tree.command(name="queue", description="View the testing queue for a gamemode.")
@app_commands.describe(gamemode="Gamemode")
async def queue(interaction, gamemode: str):
    mode = valid_gamemode(gamemode)
    if not mode:
        await bot_send(interaction, "❌ Invalid gamemode.", ephemeral=True)
        return

    with db() as conn:
        rows = conn.execute(
            "SELECT minecraft_username, discord_id FROM queue WHERE gamemode=? ORDER BY id",
            (mode,)
        ).fetchall()

    embed = discord.Embed(title=f"📋 {mode} Testing Queue", color=discord.Color.blurple())
    embed.set_footer(text=f"{len(rows)} player(s) waiting")
    if not rows:
        embed.description = "✨ The queue is currently empty."
    else:
        lines = []
        for i, row in enumerate(rows, 1):
            medal = {1: "🥇", 2: "🥈", 3: "🥉"}.get(i, f"`{i:02}`")
            lines.append(f"{medal} **{row['minecraft_username']}**  •  <@{row['discord_id']}>")
        embed.description = "\n".join(lines[:30])
        if len(rows) > 30:
            embed.description += f"\n\n…and **{len(rows)-30}** more."

    await bot_send(interaction, embed=embed)


@bot.tree.command(name="queues", description="Show every testing queue.")
async def queues(interaction):
    with db() as conn:
        rows = conn.execute(
            "SELECT gamemode, COUNT(*) AS count FROM queue GROUP BY gamemode"
        ).fetchall()
        by_mode = {r["gamemode"]: r["count"] for r in rows}

    embed = discord.Embed(title="📋 Testing Queues", description="Current players waiting for each gamemode.", color=discord.Color.blurple())
    total = 0
    for mode in GAMEMODES:
        count = by_mode.get(mode, 0)
        total += count
        embed.add_field(
            name=f"🎮 {mode}",
            value=f"**{count}** waiting",
            inline=True
        )
    embed.set_footer(text=f"{total} player(s) currently waiting")
    await bot_send(interaction, embed=embed)


@bot.tree.command(name="myqueue", description="See your current queue positions.")
async def myqueue(interaction):
    with db() as conn:
        rows = conn.execute(
            "SELECT gamemode, id, minecraft_username FROM queue WHERE discord_id=? ORDER BY id",
            (interaction.user.id,)
        ).fetchall()

    if not rows:
        await bot_send(interaction, "You are not currently in any queues.", ephemeral=True)
        return

    lines = []
    with db() as conn:
        for row in rows:
            position = conn.execute(
                "SELECT COUNT(*) FROM queue WHERE gamemode=? AND id<=?",
                (row["gamemode"], row["id"])
            ).fetchone()[0]
            lines.append(f"**{row['gamemode']}** — **#{position}** as `{row['minecraft_username']}`")

    await bot_send(interaction, "\n".join(lines), ephemeral=True)


@bot.tree.command(name="leavequeue", description="Leave a testing queue.")
@app_commands.describe(gamemode="Gamemode")
async def leavequeue(interaction, gamemode: str):
    mode = valid_gamemode(gamemode)
    if not mode:
        await bot_send(interaction, "❌ Invalid gamemode.", ephemeral=True)
        return

    with db() as conn:
        cur = conn.execute(
            "DELETE FROM queue WHERE discord_id=? AND gamemode=?",
            (interaction.user.id, mode)
        )
    await bot_send(interaction, f"✅ You left the **{mode}** queue." if cur.rowcount else "❌ You weren't in that queue.", ephemeral=True)


@bot.tree.command(name="nexttest", description="Take the next player and open a private test ticket.")
@app_commands.describe(gamemode="Gamemode")
async def nexttest(interaction, gamemode: str):
    if not is_tester(interaction):
        await bot_send(interaction, "❌ You need the **Tester** role.", ephemeral=True)
        return
    mode = valid_gamemode(gamemode)
    if not mode:
        await bot_send(interaction, "❌ Invalid gamemode.", ephemeral=True)
        return

    with db() as conn:
        row = conn.execute(
            "SELECT * FROM queue WHERE gamemode=? ORDER BY id LIMIT 1", (mode,)
        ).fetchone()
    if not row:
        await bot_send(interaction, f"❌ The **{mode}** queue is empty.", ephemeral=True)
        return

    try:
        member = await interaction.guild.fetch_member(row["discord_id"])
    except discord.NotFound:
        member = None
    if member is None:
        with db() as conn:
            conn.execute("DELETE FROM queue WHERE id=?", (row["id"],))
        await bot_send(interaction, "❌ The next player is no longer in the server, so they were removed from the queue.", ephemeral=True)
        return

    channel = await create_private_channel(
        interaction.guild, "Tier Tests",
        f"test-{row['minecraft_username']}-{mode}", member
    )
    with db() as conn:
        cur = conn.execute("""
            INSERT INTO tests
            (minecraft_username, discord_id, gamemode, status, ticket_channel_id, created_at)
            VALUES (?, ?, ?, 'Open', ?, ?)
        """, (row["minecraft_username"], row["discord_id"], mode, channel.id, utc_now()))
        test_id = cur.lastrowid
        conn.execute("DELETE FROM queue WHERE id=?", (row["id"],))

    embed = discord.Embed(
        title="⚔️ Tier Test",
        description=(
            f"**Player:** `{row['minecraft_username']}`\n"
            f"**Gamemode:** `{mode}`\n\n"
            "A tester can claim this test and submit the result."
        ),
        color=discord.Color.green()
    )
    await channel.send(content=f"{member.mention}", embed=embed, view=TestTicketView(test_id))
    await bot_send(interaction, f"✅ Opened {channel.mention} for **{row['minecraft_username']}**.")


@bot.tree.command(name="player", description="View a player's full tier profile.")
@app_commands.describe(username="Minecraft username")
async def player(interaction, username: str):
    await bot_send(interaction, embed=tier_embed(username.strip()))


@bot.tree.command(name="tiers", description="View a player's tiers.")
@app_commands.describe(username="Minecraft username")
async def tiers(interaction, username: str):
    await bot_send(interaction, embed=tier_embed(username.strip()))


@bot.tree.command(name="overall", description="View a player's overall rank.")
@app_commands.describe(username="Minecraft username")
async def overall(interaction, username: str):
    username = username.strip()
    ensure_player(username)
    await bot_send(interaction, f"🏆 **{username}**'s overall rank is **{overall_rank(username)}**.")


@bot.tree.command(name="setrank", description="Set a player's tier for one gamemode.")
@app_commands.describe(username="Minecraft username", gamemode="Gamemode", tier="Tier")
async def setrank(interaction, username: str, gamemode: str, tier: str):
    if not is_staff(interaction):
        await bot_send(interaction, "❌ Staff only.", ephemeral=True)
        return
    mode = valid_gamemode(gamemode)
    tier = tier.upper().strip()
    if not mode or tier not in TIERS or tier == "Unranked":
        await bot_send(interaction, "❌ Invalid gamemode or tier.", ephemeral=True)
        return

    ensure_player(username)
    with db() as conn:
        conn.execute(
            "UPDATE player_tiers SET tier=?, updated_at=? WHERE minecraft_username=? AND gamemode=?",
            (tier, utc_now(), username.strip(), mode)
        )
    await bot_send(interaction, f"✅ Set **{username.strip()}**'s **{mode}** tier to **{tier}**.\nOverall: **{overall_rank(username.strip())}**")


@bot.tree.command(name="gamemodes", description="List available PvP gamemodes.")
async def gamemodes(interaction):
    await bot_send(interaction, "🎮 **Gamemodes:**\n" + "\n".join(f"• {m}" for m in GAMEMODES))


@bot.tree.command(name="appeal", description="Submit a tier appeal.")
async def appeal(interaction):
    await interaction.response.send_modal(AppealModal())


@bot.tree.command(name="addplayer", description="Add a player to the tier database.")
@app_commands.describe(username="Minecraft username")
async def addplayer(interaction, username: str):
    if not is_staff(interaction):
        await bot_send(interaction, "❌ Staff only.", ephemeral=True)
        return
    ensure_player(username.strip(), interaction.user.id)
    await bot_send(interaction, f"✅ Added **{username.strip()}**.")


@bot.tree.command(name="testers", description="Show the current Tester role members.")
async def testers(interaction):
    role = await ensure_tester_role(interaction.guild)
    members = [member.mention for member in role.members if not member.bot]
    embed = discord.Embed(title="🧪 Testers", color=discord.Color.green())
    embed.description = "\n".join(members) if members else "No testers are currently assigned."
    embed.set_footer(text=f"{len(members)} tester(s)")
    await bot_send(interaction, embed=embed)


@bot.tree.command(name="teststats", description="Show your tester statistics.")
async def teststats(interaction):
    if not is_tester(interaction):
        await bot_send(interaction, "❌ Tester only.", ephemeral=True)
        return
    with db() as conn:
        completed = conn.execute(
            "SELECT COUNT(*) FROM tests WHERE tester_id=? AND status='Completed'",
            (interaction.user.id,)
        ).fetchone()[0]
    await bot_send(interaction, f"🧪 **{interaction.user.display_name}** has completed **{completed}** tests.")


@bot.tree.command(name="leaderboard", description="Show players ranked by overall tier.")
async def leaderboard(interaction):
    with db() as conn:
        players = conn.execute("SELECT minecraft_username FROM players").fetchall()

    ranked = []
    for row in players:
        rank = overall_rank(row["minecraft_username"])
        if rank != "Unranked":
            ranked.append((TIER_SCORES[rank], row["minecraft_username"], rank))

    ranked.sort(key=lambda x: (x[0], x[1].lower()))

    embed = discord.Embed(
        title="🏆  OVERALL LEADERBOARD",
        description="**Top players by overall tier**\n",
        color=discord.Color.gold()
    )

    if not ranked:
        embed.description += "\nNo players have enough ranked gamemodes yet."
    else:
        lines = []
        for i, (_, username, tier) in enumerate(ranked[:25], 1):
            if i == 1:
                prefix = "🥇"
            elif i == 2:
                prefix = "🥈"
            elif i == 3:
                prefix = "🥉"
            else:
                prefix = f"`#{i:02}`"
            lines.append(f"{prefix}  **{username}**  •  `{tier}`")
        embed.description += "\n".join(lines)

    embed.set_footer(text=f"{len(ranked)} ranked player(s) • Updated now")
    await bot_send(interaction, embed=embed)


@bot.tree.command(name="testinginfo", description="Show how tier testing works.")
async def testinginfo(interaction):
    embed = discord.Embed(
        title="🧪 Testing Information",
        description="Everything you need to know before joining a tier test.",
        color=discord.Color.green()
    )
    embed.add_field(
        name="1️⃣ Join the queue",
        value="Use `/joinqueue` with your Minecraft username and the gamemode you want tested.",
        inline=False
    )
    embed.add_field(
        name="2️⃣ Wait for a tester",
        value="Your position is based on when you joined. Use `/myqueue` to check your position or `/queues` to see all queues.",
        inline=False
    )
    embed.add_field(
        name="3️⃣ Get your test ticket",
        value="A tester uses `/nexttest` to take the next player. A private test channel is then created.",
        inline=False
    )
    embed.add_field(
        name="4️⃣ Complete the test",
        value="The tester claims the test, runs the assessment, then submits your result and tier.",
        inline=False
    )
    embed.add_field(
        name="5️⃣ Tier updated",
        value="Your tier, wins/losses and test count are automatically saved to your profile.",
        inline=False
    )
    embed.add_field(
        name="Available gamemodes",
        value=", ".join(GAMEMODES),
        inline=False
    )
    embed.set_footer(text="Please follow tester instructions and be ready when your turn comes.")
    await bot_send(interaction, embed=embed)


@bot.tree.command(name="ticketpanel", description="Create the support ticket panel.")
async def ticketpanel(interaction):
    if not is_staff(interaction):
        await bot_send(interaction, "❌ Staff only.", ephemeral=True)
        return
    embed = discord.Embed(
        title="🎫 SUPPORT TICKETS",
        description="Need help? Open a private ticket and staff will assist you.\n\n"
                    "🎫 **General Support** — general questions or server issues\n"
                    "⚔️ **Tier Help** — tier/testing related help",
        color=discord.Color.blurple()
    )
    await bot_send(interaction, embed=embed, view=TicketPanelView())


@bot.tree.command(name="applicationpanel", description="Create the staff/tester application panel.")
async def applicationpanel(interaction):
    if not is_staff(interaction):
        await bot_send(interaction, "❌ Staff only.", ephemeral=True)
        return
    embed = discord.Embed(
        title="📋 APPLICATIONS",
        description="Interested in helping the server?\n\n"
                    "🛡️ **Staff Application** — apply to join the staff team\n"
                    "🧪 **Tester Application** — apply to become a tier tester\n\n"
                    "Fill out the form honestly and a member of the team will review it.",
        color=discord.Color.green()
    )
    await bot_send(interaction, embed=embed, view=ApplicationPanelView())


@bot.tree.command(name="close", description="Close the current ticket/application.")
async def close(interaction):
    if not is_staff(interaction):
        await bot_send(interaction, "❌ Staff only.", ephemeral=True)
        return

    with db() as conn:
        conn.execute("UPDATE tickets SET status='Closed', closed_at=? WHERE channel_id=?", (utc_now(), interaction.channel.id))
        conn.execute("UPDATE applications SET status='Closed', resolved_at=? WHERE channel_id=?", (utc_now(), interaction.channel.id))
        conn.execute("UPDATE appeals SET status='Closed', resolved_at=? WHERE channel_id=? AND status='Open'", (utc_now(), interaction.channel.id))

    await bot_send(interaction, "🔒 Closing this channel...")
    try:
        await interaction.channel.delete()
    except discord.HTTPException:
        pass


@bot.tree.command(name="help", description="Show the tier bot commands.")
async def help_command(interaction):
    embed = discord.Embed(title="📚 Tier Testing Bot", color=discord.Color.blurple())
    embed.add_field(
        name="🎮 Players",
        value="`/joinqueue` • `/queue` • `/queues` • `/myqueue` • `/leavequeue`\n"
              "`/player` • `/tiers` • `/overall` • `/gamemodes` • `/appeal`",
        inline=False
    )
    embed.add_field(
        name="🧪 Testing",
        value="`/testinginfo` • `/testers` • `/teststats` • `/nexttest`",
        inline=False
    )
    embed.add_field(
        name="🏆 Rankings",
        value="`/leaderboard` • `/setrank` • `/addplayer`",
        inline=False
    )
    embed.add_field(
        name="🎫 Staff Setup",
        value="`/ticketpanel` • `/applicationpanel` • `/close`",
        inline=False
    )
    await bot_send(interaction, embed=embed)


# -----------------------------
# Startup
# -----------------------------

async def restore_persistent_views():
    with db() as conn:
        tests = conn.execute(
            "SELECT id FROM tests WHERE status IN ('Open','Claimed')"
        ).fetchall()
        appeals = conn.execute(
            "SELECT id FROM appeals WHERE status='Open'"
        ).fetchall()
        tickets = conn.execute(
            "SELECT id FROM tickets WHERE status='Open'"
        ).fetchall()
        applications = conn.execute(
            "SELECT id, application_type FROM applications WHERE status='Open'"
        ).fetchall()

    for row in tests:
        bot.add_view(TestTicketView(row["id"]))
    for row in appeals:
        bot.add_view(AppealView(row["id"]))
    for row in tickets:
        bot.add_view(CloseTicketView(row["id"]))
    for row in applications:
        bot.add_view(ApplicationReviewView(row["id"], row["application_type"]))

    bot.add_view(TicketPanelView())
    bot.add_view(ApplicationPanelView())
    # Old button IDs from v4.0/v4.1 remain supported.
    bot.add_view(LegacyTestTicketView())
    bot.add_view(LegacyAppealView())


async def sync_commands():
    if GUILD_ID:
        guild = discord.Object(id=int(GUILD_ID))
        bot.tree.copy_global_to(guild=guild)
        await bot.tree.sync(guild=guild)
        print(f"Synced commands to guild {GUILD_ID}")
    else:
        await bot.tree.sync()
        print("Synced global commands")


async def setup_hook():
    await restore_persistent_views()
    await sync_commands()


bot.setup_hook = setup_hook


@bot.event
async def on_ready():
    print(f"Logged in as {bot.user} ({bot.user.id})")


init_db()

if not TOKEN:
    raise RuntimeError("DISCORD_TOKEN is missing. Add it as a Railway environment variable.")

bot.run(TOKEN)
