import os
import sqlite3
import discord
from discord import app_commands
from discord.ext import commands
from discord.ui import View, Modal, TextInput, Button

from dotenv import load_dotenv
load_dotenv()

TOKEN=os.getenv("DISCORD_TOKEN")
GUILD_ID=os.getenv("GUILD_ID")
DB_FILE="tierbot.db"

TIERS=["HT1","LT1","HT2","LT2","HT3","LT3","HT4","LT4","HT5","LT5","Unranked"]

GAMEMODES=[
    "Sword","Axe","Mace","UHC","Pot","NethPot",
    "Crystal","SMP","Bow","Sumo"
]

intents=discord.Intents.default()
intents.members=True
bot=commands.Bot(command_prefix="!",intents=intents)

def db():
    con=sqlite3.connect(DB_FILE)
    con.row_factory=sqlite3.Row
    return con

def init_db():
    con=db()

    con.execute("""
    CREATE TABLE IF NOT EXISTS players(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        minecraft_username TEXT UNIQUE NOT NULL,
        discord_id INTEGER,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP
    )
    """)

    con.execute("""
    CREATE TABLE IF NOT EXISTS player_tiers(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        minecraft_username TEXT NOT NULL,
        gamemode TEXT NOT NULL,
        tier TEXT NOT NULL DEFAULT 'Unranked',
        wins INTEGER DEFAULT 0,
        losses INTEGER DEFAULT 0,
        tests INTEGER DEFAULT 0,
        tester TEXT,
        updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(minecraft_username,gamemode)
    )
    """)

    con.execute("""
    CREATE TABLE IF NOT EXISTS tests(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        minecraft_username TEXT NOT NULL,
        discord_id INTEGER,
        tester_id INTEGER,
        tester_name TEXT,
        gamemode TEXT NOT NULL,
        result TEXT,
        tier TEXT,
        notes TEXT,
        status TEXT DEFAULT 'waiting',
        ticket_channel_id INTEGER,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP,
        completed_at TEXT
    )
    """)

    con.execute("""
    CREATE TABLE IF NOT EXISTS appeals(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        minecraft_username TEXT NOT NULL,
        discord_id INTEGER NOT NULL,
        gamemode TEXT,
        reason TEXT NOT NULL,
        status TEXT DEFAULT 'open',
        channel_id INTEGER,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP,
        closed_at TEXT
    )
    """)

    con.commit()
    con.close()

def ensure_player(username,discord_id=None):
    con=db()
    con.execute("""
    INSERT INTO players(minecraft_username,discord_id)
    VALUES(?,?)
    ON CONFLICT(minecraft_username) DO UPDATE SET
    discord_id=COALESCE(excluded.discord_id,players.discord_id)
    """,(username,discord_id))

    # Create a row for every gamemode.
    for mode in GAMEMODES:
        con.execute("""
        INSERT OR IGNORE INTO player_tiers(minecraft_username,gamemode)
        VALUES(?,?)
        """,(username,mode))

    con.commit()
    con.close()

def get_player(username):
    con=db()
    row=con.execute(
        "SELECT * FROM players WHERE minecraft_username=?",(username,)
    ).fetchone()
    con.close()
    return row

def get_tiers(username):
    con=db()
    rows=con.execute(
        "SELECT * FROM player_tiers WHERE minecraft_username=? ORDER BY gamemode",
        (username,)
    ).fetchall()
    con.close()
    return rows

def tier_score(tier):
    # Lower score = better.
    values={
        "HT1":1,"LT1":2,
        "HT2":3,"LT2":4,
        "HT3":5,"LT3":6,
        "HT4":7,"LT4":8,
        "HT5":9,"LT5":10,
        "Unranked":99
    }
    return values.get(tier,99)

def overall_rank(rows):
    ranked=[tier_score(r["tier"]) for r in rows if r["tier"]!="Unranked"]

    if not ranked:
        return "Unranked"

    # Overall rank uses the average of all ranked gamemodes.
    # A player needs at least 2 ranked modes for an overall rank.
    if len(ranked)<2:
        return "Unranked"

    avg=sum(ranked)/len(ranked)

    # Round to nearest half and map back to the nearest tier.
    nearest=min(
        [(tier_score(t),t) for t in TIERS[:-1]],
        key=lambda x:abs(x[0]-avg)
    )
    return nearest[1]

def is_staff(interaction):
    p=interaction.user.guild_permissions
    return p.administrator or p.manage_guild

def is_tester(interaction):
    if is_staff(interaction):
        return True
    return any(r.name.lower()=="tester" for r in interaction.user.roles)

def get_tester_role(guild):
    return discord.utils.find(
        lambda r:r.name.lower()=="tester",guild.roles
    )

async def private_channel(guild,member,name,category_name):
    category=discord.utils.find(
        lambda c:c.name.lower()==category_name.lower(),
        guild.categories
    )
    if not category:
        category=await guild.create_category(category_name)

    overwrites={
        guild.default_role:discord.PermissionOverwrite(view_channel=False),
        member:discord.PermissionOverwrite(
            view_channel=True,
            send_messages=True,
            read_message_history=True
        )
    }

    role=get_tester_role(guild)
    if role:
        overwrites[role]=discord.PermissionOverwrite(
            view_channel=True,
            send_messages=True,
            read_message_history=True
        )

    overwrites[guild.me]=discord.PermissionOverwrite(
        view_channel=True,
        send_messages=True,
        read_message_history=True,
        manage_channels=True
    )

    return await guild.create_text_channel(
        name=name,
        category=category,
        overwrites=overwrites
    )


# ---------- RESULT MODAL ----------

class ResultModal(Modal,title="Submit PvP Test Result"):
    result=TextInput(
        label="Result",
        placeholder="Win or Loss",
        max_length=10
    )
    tier=TextInput(
        label="Tier",
        placeholder="HT3 / LT3 / etc.",
        max_length=5
    )
    notes=TextInput(
        label="Notes",
        required=False,
        style=discord.TextStyle.paragraph,
        max_length=1000
    )

    def __init__(self,test_id):
        super().__init__()
        self.test_id=test_id

    async def on_submit(self,interaction):
        if not is_tester(interaction):
            await interaction.response.send_message(
                "❌ Tester role required.",ephemeral=True
            )
            return

        result=self.result.value.strip().lower()
        tier=self.tier.value.strip().upper()

        if result not in ["win","loss"]:
            await interaction.response.send_message(
                "❌ Result must be Win or Loss.",ephemeral=True
            )
            return

        if tier not in TIERS[:-1]:
            await interaction.response.send_message(
                "❌ Invalid tier.",ephemeral=True
            )
            return

        con=db()
        test=con.execute(
            "SELECT * FROM tests WHERE id=?",(self.test_id,)
        ).fetchone()

        if not test:
            con.close()
            await interaction.response.send_message(
                "❌ Test not found.",ephemeral=True
            )
            return

        ensure_player(
            test["minecraft_username"],
            test["discord_id"]
        )

        # Update only this gamemode.
        con.execute("""
        UPDATE player_tiers SET
            tier=?,
            wins=wins+?,
            losses=losses+?,
            tests=tests+1,
            tester=?,
            updated_at=CURRENT_TIMESTAMP
        WHERE minecraft_username=? AND gamemode=?
        """,(
            tier,
            1 if result=="win" else 0,
            1 if result=="loss" else 0,
            str(interaction.user),
            test["minecraft_username"],
            test["gamemode"]
        ))

        con.execute("""
        UPDATE tests SET
            result=?,
            tier=?,
            notes=?,
            status='completed',
            tester_id=?,
            tester_name=?,
            completed_at=CURRENT_TIMESTAMP
        WHERE id=?
        """,(
            result,
            tier,
            self.notes.value.strip(),
            interaction.user.id,
            str(interaction.user),
            self.test_id
        ))

        con.commit()

        rows=con.execute(
            "SELECT * FROM player_tiers WHERE minecraft_username=?",
            (test["minecraft_username"],)
        ).fetchall()

        con.close()

        overall=overall_rank(rows)

        embed=discord.Embed(
            title="✅ PvP Test Completed",
            color=discord.Color.green()
        )
        embed.add_field(
            name="Player",
            value=f"`{test['minecraft_username']}"
        )
        embed.add_field(
            name="Gamemode",
            value=f"**{test['gamemode']}**"
        )
        embed.add_field(
            name="Gamemode Tier",
            value=f"**{tier}**"
        )
        embed.add_field(
            name="Overall Rank",
            value=f"**{overall}**"
        )
        embed.add_field(
            name="Result",
            value=result.title()
        )
        embed.add_field(
            name="Tester",
            value=interaction.user.mention
        )

        if self.notes.value.strip():
            embed.add_field(
                name="Notes",
                value=self.notes.value.strip(),
                inline=False
            )

        await interaction.response.send_message(embed=embed)

        try:
            await interaction.channel.edit(
                name=f"completed-{test['gamemode'].lower()}-{test['minecraft_username'].lower()}"
            )
        except:
            pass


# ---------- TEST TICKET ----------

class TestView(View):
    def __init__(self,test_id):
        super().__init__(timeout=None)
        self.test_id=test_id

    @discord.ui.button(
        label="Claim Test",
        style=discord.ButtonStyle.primary,
        emoji="🎯"
    )
    async def claim(self,interaction,button):
        if not is_tester(interaction):
            await interaction.response.send_message(
                "❌ Tester role required.",ephemeral=True
            )
            return

        con=db()
        test=con.execute(
            "SELECT * FROM tests WHERE id=?",(self.test_id,)
        ).fetchone()

        if not test:
            con.close()
            await interaction.response.send_message(
                "❌ Test not found.",ephemeral=True
            )
            return

        if test["tester_id"] and test["tester_id"]!=interaction.user.id:
            con.close()
            await interaction.response.send_message(
                f"❌ Already claimed by **{test['tester_name']}**.",
                ephemeral=True
            )
            return

        con.execute("""
        UPDATE tests SET
        tester_id=?,
        tester_name=?,
        status='claimed'
        WHERE id=?
        """,(interaction.user.id,str(interaction.user),self.test_id))

        con.commit()
        con.close()

        await interaction.response.send_message(
            f"🎯 {interaction.user.mention} claimed the **{test['gamemode']}** test."
        )

    @discord.ui.button(
        label="Submit Result",
        style=discord.ButtonStyle.success,
        emoji="📝"
    )
    async def result(self,interaction,button):
        if not is_tester(interaction):
            await interaction.response.send_message(
                "❌ Tester role required.",ephemeral=True
            )
            return

        await interaction.response.send_modal(
            ResultModal(self.test_id)
        )

    @discord.ui.button(
        label="Cancel Test",
        style=discord.ButtonStyle.danger,
        emoji="🗑️"
    )
    async def cancel(self,interaction,button):
        if not is_tester(interaction):
            await interaction.response.send_message(
                "❌ Tester role required.",ephemeral=True
            )
            return

        con=db()
        con.execute(
            "UPDATE tests SET status='cancelled' WHERE id=?",
            (self.test_id,)
        )
        con.commit()
        con.close()

        await interaction.response.send_message("🗑️ Test cancelled.")

        try:
            await interaction.channel.delete()
        except:
            pass


# ---------- APPEAL ----------

class AppealModal(Modal,title="Open Tier Appeal"):
    username=TextInput(
        label="Minecraft Username",
        max_length=32
    )
    gamemode=TextInput(
        label="Gamemode",
        placeholder="Sword / Crystal / etc.",
        max_length=30
    )
    reason=TextInput(
        label="Reason",
        style=discord.TextStyle.paragraph,
        max_length=1500
    )

    async def on_submit(self,interaction):
        con=db()
        existing=con.execute("""
        SELECT * FROM appeals
        WHERE discord_id=? AND status='open'
        """,(interaction.user.id,)).fetchone()
        con.close()

        if existing:
            await interaction.response.send_message(
                "❌ You already have an open appeal.",
                ephemeral=True
            )
            return

        channel=await private_channel(
            interaction.guild,
            interaction.user,
            f"appeal-{interaction.user.name}",
            "Tier Appeals"
        )

        con=db()
        cur=con.execute("""
        INSERT INTO appeals(
            minecraft_username,
            discord_id,
            gamemode,
            reason,
            channel_id
        )
        VALUES(?,?,?,?,?)
        """,(
            self.username.value.strip(),
            interaction.user.id,
            self.gamemode.value.strip(),
            self.reason.value.strip(),
            channel.id
        ))

        appeal_id=cur.lastrowid
        con.commit()
        con.close()

        embed=discord.Embed(
            title=f"⚖️ Tier Appeal #{appeal_id}",
            color=discord.Color.orange()
        )
        embed.add_field(
            name="Player",
            value=f"`{self.username.value.strip()}`"
        )
        embed.add_field(
            name="Gamemode",
            value=self.gamemode.value.strip()
        )
        embed.add_field(
            name="Reason",
            value=self.reason.value.strip(),
            inline=False
        )

        await channel.send(
            content=f"{interaction.user.mention} Your private appeal has been opened.",
            embed=embed,
            view=AppealView(appeal_id)
        )

        await interaction.response.send_message(
            f"✅ Appeal created: {channel.mention}",
            ephemeral=True
        )


class AppealView(View):
    def __init__(self,appeal_id):
        super().__init__(timeout=None)
        self.appeal_id=appeal_id

    @discord.ui.button(
        label="Accept Appeal",
        style=discord.ButtonStyle.success
    )
    async def accept(self,interaction,button):
        if not is_staff(interaction):
            await interaction.response.send_message(
                "❌ Staff only.",ephemeral=True
            )
            return
        await self.finish(interaction,"accepted")

    @discord.ui.button(
        label="Deny Appeal",
        style=discord.ButtonStyle.danger
    )
    async def deny(self,interaction,button):
        if not is_staff(interaction):
            await interaction.response.send_message(
                "❌ Staff only.",ephemeral=True
            )
            return
        await self.finish(interaction,"denied")

    async def finish(self,interaction,status):
        con=db()
        con.execute(
            "UPDATE appeals SET status=?,closed_at=CURRENT_TIMESTAMP WHERE id=?",
            (status,self.appeal_id)
        )
        con.commit()
        con.close()

        await interaction.response.send_message(
            f"⚖️ Appeal **{status}** by {interaction.user.mention}."
        )


# ---------- COMMANDS ----------

@bot.tree.command(
    name="joinqueue",
    description="Join the waitlist for a specific PvP gamemode."
)
@app_commands.describe(
    username="Minecraft username",
    gamemode="PvP gamemode"
)
@app_commands.choices(
    gamemode=[app_commands.Choice(name=x,value=x) for x in GAMEMODES]
)
async def joinqueue(interaction,username:str,gamemode:app_commands.Choice[str]):
    ensure_player(username,interaction.user.id)

    con=db()

    existing=con.execute("""
    SELECT * FROM tests
    WHERE discord_id=?
    AND gamemode=?
    AND status IN ('waiting','ticket','claimed')
    """,(interaction.user.id,gamemode.value)).fetchone()

    if existing:
        con.close()
        await interaction.response.send_message(
            f"❌ You already have an active **{gamemode.value}** test.",
            ephemeral=True
        )
        return

    con.execute("""
    INSERT INTO tests(
        minecraft_username,
        discord_id,
        gamemode,
        status
    )
    VALUES(?,?,?,'waiting')
    """,(
        username,
        interaction.user.id,
        gamemode.value
    ))

    test_id=con.execute(
        "SELECT last_insert_rowid()"
    ).fetchone()[0]

    position=con.execute("""
    SELECT COUNT(*) FROM tests
    WHERE gamemode=?
    AND status='waiting'
    AND id<=?
    """,(gamemode.value,test_id)).fetchone()[0]

    con.commit()
    con.close()

    embed=discord.Embed(
        title="📋 Added to Waitlist",
        color=discord.Color.blurple()
    )
    embed.add_field(
        name="Minecraft",
        value=f"`{username}`"
    )
    embed.add_field(
        name="Gamemode",
        value=f"**{gamemode.value}**"
    )
    embed.add_field(
        name="Position",
        value=f"**#{position}**"
    )
    embed.set_footer(
        text="A tester will open your private test ticket."
    )

    await interaction.response.send_message(embed=embed)


@bot.tree.command(
    name="queue",
    description="View the waitlist for a PvP gamemode."
)
@app_commands.describe(gamemode="PvP gamemode")
@app_commands.choices(
    gamemode=[app_commands.Choice(name=x,value=x) for x in GAMEMODES]
)
async def queue(interaction,gamemode:app_commands.Choice[str]):
    con=db()
    rows=con.execute("""
    SELECT * FROM tests
    WHERE gamemode=?
    AND status='waiting'
    ORDER BY id
    """,(gamemode.value,)).fetchall()
    con.close()

    if not rows:
        await interaction.response.send_message(
            f"📋 The **{gamemode.value}** queue is empty."
        )
        return

    lines=[
        f"**#{i}** `{r['minecraft_username']}`"
        for i,r in enumerate(rows,1)
    ]

    embed=discord.Embed(
        title=f"📋 {gamemode.value} Waitlist",
        description="\n".join(lines),
        color=discord.Color.blurple()
    )

    await interaction.response.send_message(embed=embed)


@bot.tree.command(
    name="nexttest",
    description="Tester: open the next private ticket for a gamemode."
)
@app_commands.describe(gamemode="PvP gamemode")
@app_commands.choices(
    gamemode=[app_commands.Choice(name=x,value=x) for x in GAMEMODES]
)
async def nexttest(interaction,gamemode:app_commands.Choice[str]):
    if not is_tester(interaction):
        await interaction.response.send_message(
            "❌ Tester role required.",ephemeral=True
        )
        return

    con=db()
    test=con.execute("""
    SELECT * FROM tests
    WHERE gamemode=?
    AND status='waiting'
    ORDER BY id
    LIMIT 1
    """,(gamemode.value,)).fetchone()
    con.close()

    if not test:
        await interaction.response.send_message(
            f"📋 No players are waiting for **{gamemode.value}**.",
            ephemeral=True
        )
        return

    member=interaction.guild.get_member(test["discord_id"])

    if not member:
        await interaction.response.send_message(
            "❌ Player is no longer in the Discord server.",
            ephemeral=True
        )
        return

    channel=await private_channel(
        interaction.guild,
        member,
        f"test-{gamemode.value.lower()}-{test['minecraft_username'].lower()}",
        "Tier Tests"
    )

    con=db()
    con.execute("""
    UPDATE tests SET
        status='ticket',
        ticket_channel_id=?
    WHERE id=?
    """,(channel.id,test["id"]))
    con.commit()
    con.close()

    embed=discord.Embed(
        title="🧪 Private PvP Tier Test",
        color=discord.Color.blurple()
    )
    embed.add_field(
        name="Player",
        value=f"`{test['minecraft_username']}`"
    )
    embed.add_field(
        name="Gamemode",
        value=f"**{test['gamemode']}**"
    )
    embed.add_field(
        name="Test ID",
        value=f"`#{test['id']}`"
    )
    embed.description=(
        "A tester should claim this ticket before starting the test."
    )

    await channel.send(
        content=f"{member.mention} Your **{gamemode.value}** test is ready!",
        embed=embed,
        view=TestView(test["id"])
    )

    await interaction.response.send_message(
        f"🎫 Created {channel.mention}",
        ephemeral=True
    )


@bot.tree.command(
    name="myqueue",
    description="See your active tests."
)
async def myqueue(interaction):
    con=db()
    rows=con.execute("""
    SELECT * FROM tests
    WHERE discord_id=?
    AND status IN ('waiting','ticket','claimed')
    ORDER BY id
    """,(interaction.user.id,)).fetchall()
    con.close()

    if not rows:
        await interaction.response.send_message(
            "You have no active tests.",
            ephemeral=True
        )
        return

    lines=[
        f"`#{r['id']}` **{r['gamemode']}** — {r['status']}"
        for r in rows
    ]

    await interaction.response.send_message(
        "📋 **Your active tests**\n"+"\n".join(lines),
        ephemeral=True
    )


@bot.tree.command(
    name="leavequeue",
    description="Leave a waiting queue."
)
@app_commands.describe(gamemode="PvP gamemode")
@app_commands.choices(
    gamemode=[app_commands.Choice(name=x,value=x) for x in GAMEMODES]
)
async def leavequeue(interaction,gamemode:app_commands.Choice[str]):
    con=db()
    cur=con.execute("""
    DELETE FROM tests
    WHERE discord_id=?
    AND gamemode=?
    AND status='waiting'
    """,(interaction.user.id,gamemode.value))
    con.commit()
    con.close()

    await interaction.response.send_message(
        f"✅ Removed from **{gamemode.value}** queue."
        if cur.rowcount
        else "❌ You aren't waiting in that queue.",
        ephemeral=True
    )


@bot.tree.command(
    name="player",
    description="View every gamemode tier and overall rank."
)
@app_commands.describe(username="Minecraft username")
async def player(interaction,username:str):
    row=get_player(username)

    if not row:
        await interaction.response.send_message(
            "❌ Player not found.",
            ephemeral=True
        )
        return

    tiers=get_tiers(username)
    overall=overall_rank(tiers)

    embed=discord.Embed(
        title=f"🏆 {username}",
        color=discord.Color.gold()
    )
    embed.add_field(
        name="⭐ Overall Rank",
        value=f"**{overall}**",
        inline=False
    )

    text=[]
    for r in tiers:
        record=f"{r['wins']}-{r['losses']}"
        text.append(
            f"**{r['gamemode']}** — `{r['tier']}` • `{record}`"
        )

    embed.add_field(
        name="⚔️ Gamemode Rankings",
        value="\n".join(text),
        inline=False
    )

    await interaction.response.send_message(embed=embed)


@bot.tree.command(
    name="tiers",
    description="Show a player's complete tier card."
)
@app_commands.describe(username="Minecraft username")
async def tiers(interaction,username:str):
    await player.callback(interaction,username)


@bot.tree.command(
    name="overall",
    description="Show a player's calculated overall rank."
)
@app_commands.describe(username="Minecraft username")
async def overall(interaction,username:str):
    row=get_player(username)

    if not row:
        await interaction.response.send_message(
            "❌ Player not found.",
            ephemeral=True
        )
        return

    rows=get_tiers(username)
    ranked=[r for r in rows if r["tier"]!="Unranked"]

    embed=discord.Embed(
        title=f"⭐ Overall Rank — {username}",
        color=discord.Color.gold()
    )
    embed.add_field(
        name="Overall",
        value=f"**{overall_rank(rows)}**",
        inline=False
    )
    embed.add_field(
        name="Ranked Gamemodes",
        value=f"{len(ranked)}/{len(GAMEMODES)}",
        inline=True
    )
    embed.add_field(
        name="Formula",
        value="Average of all ranked gamemode tier scores.",
        inline=True
    )

    await interaction.response.send_message(embed=embed)


@bot.tree.command(
    name="setrank",
    description="Staff: set a player's tier for one gamemode."
)
@app_commands.describe(
    username="Minecraft username",
    gamemode="PvP gamemode",
    tier="Tier"
)
@app_commands.choices(
    gamemode=[app_commands.Choice(name=x,value=x) for x in GAMEMODES],
    tier=[app_commands.Choice(name=x,value=x) for x in TIERS[:-1]]
)
async def setrank(
    interaction,
    username:str,
    gamemode:app_commands.Choice[str],
    tier:app_commands.Choice[str]
):
    if not is_staff(interaction):
        await interaction.response.send_message(
            "❌ Staff only.",ephemeral=True
        )
        return

    ensure_player(username)

    con=db()
    con.execute("""
    UPDATE player_tiers SET
        tier=?,
        updated_at=CURRENT_TIMESTAMP
    WHERE minecraft_username=? AND gamemode=?
    """,(tier.value,username,gamemode.value))
    con.commit()

    rows=con.execute(
        "SELECT * FROM player_tiers WHERE minecraft_username=?",
        (username,)
    ).fetchall()

    con.close()

    await interaction.response.send_message(
        f"🏆 `{username}` is now **{tier.value}** in "
        f"**{gamemode.value}**.\n"
        f"⭐ Overall rank: **{overall_rank(rows)}**"
    )


@bot.tree.command(
    name="gamemodes",
    description="List all PvP gamemodes."
)
async def gamemodes(interaction):
    await interaction.response.send_message(
        "⚔️ **PvP Gamemodes**\n"+
        "\n".join(f"• **{x}**" for x in GAMEMODES)
    )


@bot.tree.command(
    name="appeal",
    description="Open a private tier appeal."
)
async def appeal(interaction):
    await interaction.response.send_modal(AppealModal())


@bot.tree.command(
    name="addplayer",
    description="Staff: add a player."
)
@app_commands.describe(username="Minecraft username")
async def addplayer(interaction,username:str):
    if not is_staff(interaction):
        await interaction.response.send_message(
            "❌ Staff only.",ephemeral=True
        )
        return

    if get_player(username):
        await interaction.response.send_message(
            "❌ Player already exists.",
            ephemeral=True
        )
        return

    ensure_player(username)
    await interaction.response.send_message(
        f"✅ Added `{username}` with **Unranked** gamemode tiers."
    )


@bot.tree.command(
    name="teststats",
    description="Staff: tester statistics."
)
async def teststats(interaction):
    if not is_staff(interaction):
        await interaction.response.send_message(
            "❌ Staff only.",ephemeral=True
        )
        return

    con=db()
    rows=con.execute("""
    SELECT tester_name,COUNT(*) AS total
    FROM tests
    WHERE status='completed'
    AND tester_name IS NOT NULL
    GROUP BY tester_name
    ORDER BY total DESC
    """).fetchall()
    con.close()

    lines=[
        f"**{i}.** {r['tester_name']} — `{r['total']}` tests"
        for i,r in enumerate(rows,1)
    ]

    await interaction.response.send_message(
        "🎯 **Tester Statistics**\n"+
        ("\n".join(lines) if lines else "No completed tests.")
    )


@bot.tree.command(
    name="leaderboard",
    description="Show players sorted by overall rank."
)
async def leaderboard(interaction):
    con=db()
    players=con.execute("SELECT minecraft_username FROM players").fetchall()
    con.close()

    data=[]
    for p in players:
        rows=get_tiers(p["minecraft_username"])
        rank=overall_rank(rows)
        if rank!="Unranked":
            data.append((tier_score(rank),p["minecraft_username"],rank))

    data.sort(key=lambda x:x[0])

    lines=[
        f"**{i}.** `{name}` — **{rank}**"
        for i,(_,name,rank) in enumerate(data[:25],1)
    ]

    embed=discord.Embed(
        title="⭐ Overall Leaderboard",
        description="\n".join(lines) if lines else "No ranked players yet.",
        color=discord.Color.gold()
    )

    await interaction.response.send_message(embed=embed)


@bot.tree.command(
    name="help",
    description="Show bot commands."
)
async def help_cmd(interaction):
    embed=discord.Embed(
        title="🤖 Minecraft Tier Bot",
        color=discord.Color.blurple()
    )

    embed.add_field(
        name="📋 Queue",
        value="`/joinqueue` `/queue` `/myqueue` `/leavequeue`",
        inline=False
    )
    embed.add_field(
        name="🧪 Testing",
        value="`/nexttest` — create private test ticket",
        inline=False
    )
    embed.add_field(
        name="🏆 Rankings",
        value="`/player` `/tiers` `/overall` `/leaderboard`",
        inline=False
    )
    embed.add_field(
        name="🛠️ Staff",
        value="`/setrank` `/addplayer` `/teststats`",
        inline=False
    )
    embed.add_field(
        name="⚖️ Appeals",
        value="`/appeal`",
        inline=False
    )

    await interaction.response.send_message(embed=embed)


@bot.event
async def on_ready():
    init_db()

    try:
        if GUILD_ID:
            guild=discord.Object(id=int(GUILD_ID))
            bot.tree.copy_global_to(guild=guild)
            await bot.tree.sync(guild=guild)
            print(f"Logged in as {bot.user} | Guild commands synced")
        else:
            await bot.tree.sync()
            print(f"Logged in as {bot.user} | Global commands synced")
    except Exception as e:
        print("Command sync error:",e)


if not TOKEN:
    raise RuntimeError(
        "DISCORD_TOKEN is missing. Copy .env.example to .env and add your token."
    )

init_db()
bot.run(TOKEN)
