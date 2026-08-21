# Dosya Konumu: /bot_cog.py
import discord
from discord.ext import commands, tasks
import datetime
from database import get_db

class ActivityInactivityCog(commands.Cog):
    def __init__(self, bot: commands.Bot, owner_ids: list, admin_ids: list, guild_id: int):
        self.bot = bot
        self.owner_ids = owner_ids
        self.admin_ids = admin_ids
        self.guild_id = guild_id
        self.inactivity_check_loop.start()

    def cog_unload(self):
        self.inactivity_check_loop.cancel()

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot or not message.guild:
            return

        user_id = message.author.id
        username = str(message.author)
        channel_id = message.channel.id
        channel_name = message.channel.name
        now = datetime.datetime.utcnow()
        hour = now.hour
        timestamp = now.strftime("%Y-%m-%d %H:%M:%S")

        db = await get_db()
        async with db.execute("SELECT is_rp_enabled FROM rp_channels WHERE channel_id = ?", (channel_id,)) as cursor:
            row = await cursor.fetchone()
            is_rp = 1 if row and row[0] == 1 else 0

        # 1. Record activity log for heatmap
        await db.execute(
            "INSERT INTO activity_logs (user_id, username, channel_id, channel_name, hour, is_rp_channel, timestamp) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (user_id, username, channel_id, channel_name, hour, is_rp, timestamp)
        )

        # 2. Update user_last_active
        async with db.execute("SELECT user_id FROM user_last_active WHERE user_id = ?", (user_id,)) as cursor:
            exists = await cursor.fetchone()

        if exists:
            if is_rp:
                await db.execute("UPDATE user_last_active SET username = ?, last_message_date = ?, last_rp_date = ? WHERE user_id = ?", (username, timestamp, timestamp, user_id))
            else:
                await db.execute("UPDATE user_last_active SET username = ?, last_message_date = ? WHERE user_id = ?", (username, timestamp, user_id))
        else:
            l_rp = timestamp if is_rp else None
            await db.execute("INSERT INTO user_last_active (user_id, username, last_message_date, last_rp_date) VALUES (?, ?, ?, ?)", (user_id, username, timestamp, l_rp))

        await db.commit()

    @tasks.loop(hours=72)
    async def inactivity_check_loop(self):
        try:
            guild = self.bot.get_guild(self.guild_id)
            if not guild:
                return

            now = datetime.datetime.utcnow()
            inactive_users = []

            db = await get_db()
            async with db.execute("SELECT user_id, username, last_message_date, last_rp_date FROM user_last_active") as cursor:
                rows = await cursor.fetchall()

            for r in rows:
                u_id, uname, l_msg, l_rp = r
                last_active_str = l_msg or l_rp
                if not last_active_str:
                    continue
                try:
                    last_active_dt = datetime.datetime.strptime(last_active_str, "%Y-%m-%d %H:%M:%S")
                    delta = now - last_active_dt
                    days_inactive = delta.days
                    if days_inactive >= 3:
                        inactive_users.append({
                            "user_id": u_id,
                            "username": uname,
                            "days": days_inactive,
                            "last_active": last_active_str
                        })
                except Exception:
                    pass

            if not inactive_users:
                return

            embed = discord.Embed(
                title="[SECURITY] // INACTIVITY AUDIT REPORT",
                description=f"Automated 72-hour scan detected **{len(inactive_users)}** inactive operatives across the grid.",
                color=discord.Color(0xFF0000),
                timestamp=now
            )

            report_lines = []
            for u in inactive_users[:20]:
                report_lines.append(f"• <@{u['user_id']}> (`{u['username']}`) | Inactive: **{u['days']} days** | Last Active: `{u['last_active']}`")

            embed.add_field(name="PASSIVE OPERATIVES", value="\n".join(report_lines) if report_lines else "None", inline=False)
            embed.set_footer(text="NETRUNNER AUTOMATED AUDIT SYSTEM")

            target_admin_ids = set(self.owner_ids + self.admin_ids)
            for adm_id in target_admin_ids:
                try:
                    admin_user = guild.get_member(adm_id) or await self.bot.fetch_user(adm_id)
                    if admin_user:
                        await admin_user.send(embed=embed)
                except Exception:
                    pass
        except Exception as e:
            print(f"Inactivity check loop error: {e}")

    @inactivity_check_loop.before_loop
    async def before_inactivity_check(self):
        await self.bot.wait_until_ready()

async def setup(bot: commands.Bot):
    owner_ids = [973745249543401482, 671439881909698560, 1152921256837001288, 957268410662805564]
    admin_ids = [671439881909698560, 1152921256837001288, 957268410662805564]
    guild_id = 1386773654876197005
    await bot.add_cog(ActivityInactivityCog(bot, owner_ids, admin_ids, guild_id))
