# Dosya Konumu: /bot.py
import asyncio
import json
import random
import datetime
import hashlib
import subprocess
import base64
import os
import string
import discord
from discord import app_commands
from discord.ext import commands, tasks
import aiosqlite

import redis.asyncio as aioredis
import sentry_sdk
import jwt
import httpx
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded

from fastapi import FastAPI, Request, Form, Depends, HTTPException, status, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
import uvicorn

from database import init_db, DB_NAME, get_required_xp
from router import router

# --- SENTRY & REDIS INITIALIZATION ---
sentry_sdk.init(
    dsn=os.getenv("SENTRY_DSN", ""),
    traces_sample_rate=1.0,
)

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")
try:
    redis_client = aioredis.from_url(REDIS_URL, decode_responses=True)
except Exception:
    redis_client = None

DISCORD_CLIENT_ID = os.getenv("DISCORD_CLIENT_ID", "1386773654876197005")
DISCORD_CLIENT_SECRET = os.getenv("DISCORD_CLIENT_SECRET", "dummy_secret")
DISCORD_REDIRECT_URI = os.getenv("DISCORD_REDIRECT_URI", "http://5.175.136.235:8000/admin/auth/callback")
JWT_SECRET_KEY = os.getenv("JWT_SECRET_KEY", "cyberpunk_secret_jwt_key_999")

# --- 1. TEMEL DEĞİŞKENLER VE KONFİGÜRASYON ---
BOT_TOKEN = os.getenv("BOT_TOKEN", "")
GUILD_ID = 1386773654876197005
LOG_CHANNEL_ID = 1534697839643463770
HARDCORE_OWNER_ID = 973745249543401482
DEFAULT_OWNERS = [671439881909698560, 1152921256837001288, 957268410662805564]
DEFAULT_ADMINS = [671439881909698560, 1152921256837001288, 957268410662805564]

WEB_PORT = 8000
BASE_URL = "http://5.175.136.235:8000"

GLOBAL_SHUTDOWN = False

intents = discord.Intents.default()
intents.members = True
intents.message_content = True

class CyberpunkBot(commands.Bot):
    def __init__(self):
        super().__init__(command_prefix="!", intents=intents)

    async def setup_hook(self):
        await init_db()
        await self.load_extension("bot_cog")
        
        try:
            guild = discord.Object(id=GUILD_ID)
            self.tree.copy_global_to(guild=guild)
            await self.tree.sync(guild=guild)
        except Exception as e:
            print(f"Tree sync error: {e}")

        try:
            await sync_guild_channels(self)
        except Exception:
            pass

        print(f"Bot Oturumu Açıldı: {self.user} (ID: {self.user.id})")

bot = CyberpunkBot()

class ConnectionManager:
    def __init__(self):
        self.active_connections: list[WebSocket] = []

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)

    def disconnect(self, websocket: WebSocket):
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)

    async def broadcast(self, data: dict):
        for connection in self.active_connections:
            try:
                await connection.send_json(data)
            except Exception:
                pass

manager = ConnectionManager()

class LogManager:
    @staticmethod
    async def send_log(guild: discord.Guild, category: str, level: str, message: str, metadata: dict = None):
        timestamp = datetime.datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
        meta_str = json.dumps(metadata) if metadata else "{}"
        
        async with aiosqlite.connect(DB_NAME) as db:
            await db.execute(
                "INSERT INTO system_logs (timestamp, category, level, message, metadata) VALUES (?, ?, ?, ?, ?)",
                (timestamp, category.upper(), level.upper(), message, meta_str)
            )
            await db.commit()
            
        await manager.broadcast({
            "timestamp": timestamp,
            "category": category.upper(),
            "level": level.upper(),
            "message": message,
            "metadata": metadata or {}
        })

        if not guild:
            return
            
        channel = guild.get_channel(LOG_CHANNEL_ID)
        if not channel:
            try:
                channel = await guild.fetch_channel(LOG_CHANNEL_ID)
            except Exception:
                pass
                
        if channel:
            color_map = {
                "SYSTEM": 0x00F0FF,
                "INFO": 0x00F0FF,
                "ECONOMY": 0xFFE600,
                "TRANSACTION": 0xFFE600,
                "CYBERWARE": 0xFF0055,
                "RP": 0xFF0055,
                "WARNING": 0xFF0000,
                "SECURITY": 0xFF0000,
                "CRITICAL": 0xFF0000
            }
            color_val = color_map.get(category.upper(), color_map.get(level.upper(), 0x00F0FF))
            tag = f"[{category.upper()}]"
            
            desc_lines = [
                f"[TIMESTAMP] {timestamp}",
                f"[LEVEL] {level.upper()}",
                f"[CATEGORY] {category.upper()}"
            ]
            if metadata:
                if "difficulty" in metadata:
                    desc_lines.append(f"[ZORLUK] {metadata['difficulty']}")
                if "target_level" in metadata:
                    desc_lines.append(f"[HEDEF_SEVIYE] {metadata['target_level']}")
            
            description_block = "```ini\n" + "\n".join(desc_lines) + "\n```"

            embed = discord.Embed(
                title=f"{tag} // EVENT LOG",
                description=description_block,
                color=discord.Color(color_val),
                timestamp=discord.utils.utcnow()
            )
            embed.add_field(name="DETAILS", value=f"```prolog\n{message}\n```", inline=False)
            if metadata:
                meta_formatted = json.dumps(metadata, indent=2, ensure_ascii=False)
                embed.add_field(name="METADATA", value=f"```json\n{meta_formatted}\n```", inline=False)
            embed.set_footer(text="NETRUNNER DASHBOARD // UNIFIED LOGGING SYSTEM")
            
            try:
                await channel.send(embed=embed)
            except Exception:
                pass

async def sync_guild_channels(client: commands.Bot):
    try:
        guild = client.get_guild(GUILD_ID)
        if not guild:
            try:
                guild = await client.fetch_guild(GUILD_ID)
            except Exception:
                return
        if guild:
            async with aiosqlite.connect(DB_NAME) as db:
                for channel in guild.text_channels:
                    try:
                        await db.execute(
                            "INSERT OR IGNORE INTO rp_channels (channel_id, channel_name, is_rp_enabled) VALUES (?, ?, COALESCE((SELECT is_rp_enabled FROM rp_channels WHERE channel_id = ?), 0))",
                            (channel.id, channel.name, channel.id)
                        )
                    except Exception:
                        pass
                await db.commit()
    except Exception:
        pass

async def is_user_authorized(member: discord.Member) -> bool:
    if member.id == HARDCORE_OWNER_ID:
        return True
    if member.id in DEFAULT_OWNERS or member.id in DEFAULT_ADMINS:
        return True
    
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute("SELECT role_id FROM authorized_roles") as cursor:
            rows = await cursor.fetchall()
            auth_role_ids = {row[0] for row in rows}
            
        user_role_ids = {role.id for role in member.roles}
        if user_role_ids.intersection(auth_role_ids):
            return True
            
    return False

def authorized_only():
    async def predicate(interaction: discord.Interaction) -> bool:
        if not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message("Bu komut sadece sunucularda kullanılabilir.", ephemeral=True)
            return False
        allowed = await is_user_authorized(interaction.user)
        if not allowed:
            await interaction.response.send_message("Erişim Reddedildi.", ephemeral=True)
            return False
        return True
    return app_commands.check(predicate)

def hardcore_owner_only():
    async def predicate(interaction: discord.Interaction) -> bool:
        if interaction.user.id != HARDCORE_OWNER_ID:
            await interaction.response.send_message("Erişim Reddedildi.", ephemeral=True)
            return False
        return True
    return app_commands.check(predicate)

@bot.event
async def on_ready():
    await LogManager.send_log(bot.get_guild(GUILD_ID), "SYSTEM", "INFO", f"Netrunner Dashboard bot session initialized. User: {bot.user}")

async def process_rp_xp(message: discord.Message):
    if message.author.bot or not message.guild:
        return

    channel_id = message.channel.id
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute("SELECT is_rp_enabled FROM rp_channels WHERE channel_id = ?", (channel_id,)) as cursor:
            row = await cursor.fetchone()
            if not row or row[0] != 1:
                return

        async with db.execute("SELECT level, xp, daily_rp_xp, last_xp_date, sp_points FROM rp_players WHERE user_id = ?", (message.author.id,)) as cursor:
            player = await cursor.fetchone()
            if not player:
                return

        level, xp, daily_rp_xp, last_xp_date, sp_points = player
        level = level or 1
        xp = xp or 0.0
        daily_rp_xp = daily_rp_xp or 0.0

        if level >= 20:
            return

        today_str = datetime.datetime.utcnow().strftime("%Y-%m-%d")
        if last_xp_date != today_str:
            daily_rp_xp = 0.0
            last_xp_date = today_str

        if daily_rp_xp >= 50.0:
            return

        length = len(message.content)
        is_nitro = message.author.premium_since is not None

        xp_gain = 0
        if is_nitro and length >= 2500:
            val = length // 100
            xp_gain = min(35, max(25, val))
        elif length >= 500:
            val = length // 100
            xp_gain = min(15, max(5, val))
        else:
            xp_gain = 0

        if xp_gain <= 0:
            return

        remaining_daily = 50.0 - daily_rp_xp
        if xp_gain > remaining_daily:
            xp_gain = remaining_daily

        if xp_gain <= 0:
            return

        new_daily_xp = daily_rp_xp + xp_gain
        new_xp = xp + xp_gain

        leveled_up = False
        new_level = level
        added_sp = 0

        while new_level < 20:
            req_xp = get_required_xp(new_level + 1)
            if new_xp >= req_xp:
                new_xp -= req_xp
                new_level += 1
                added_sp += 3
                leveled_up = True
            else:
                break

        new_sp = (sp_points or 9) + added_sp

        await db.execute(
            "UPDATE rp_players SET level = ?, xp = ?, daily_rp_xp = ?, last_xp_date = ?, sp_points = ? WHERE user_id = ?",
            (new_level, new_xp, new_daily_xp, last_xp_date, new_sp, message.author.id)
        )
        await db.commit()

        if leveled_up:
            try:
                embed = discord.Embed(
                    title="[SYSTEM] // SEVİYE ATLAMA RAPORU",
                    description=f"🎉 Tebrikler {message.author.mention}! Karakteriniz **{new_level}. Seviye**'ye ulaştı!\n\n⚡ Kazanılan SP: **+{added_sp} SP** (Toplam SP: {new_sp})",
                    color=discord.Color(0x00F0FF),
                    timestamp=discord.utils.utcnow()
                )
                await message.channel.send(embed=embed)
            except Exception:
                pass

@bot.event
async def on_message(message: discord.Message):
    if GLOBAL_SHUTDOWN:
        if message.author.bot:
            return
        try:
            await message.channel.send("UYARI SHUTDOWN by devpact")
        except Exception:
            pass
        return

    if message.author.bot:
        return

    await process_rp_xp(message)
    await bot.process_commands(message)

@bot.listen("on_interaction")
async def on_global_interaction(interaction: discord.Interaction):
    if GLOBAL_SHUTDOWN:
        try:
            if interaction.response.is_done():
                await interaction.followup.send("UYARI SHUTDOWN by devpact", ephemeral=True)
            else:
                await interaction.response.send_message("UYARI SHUTDOWN by devpact", ephemeral=True)
        except Exception:
            pass
        return

@bot.command(name="devpact-server-shutdown")
async def devpact_server_shutdown(ctx: commands.Context):
    global GLOBAL_SHUTDOWN
    if not (ctx.author.id == HARDCORE_OWNER_ID or await is_user_authorized(ctx.author) or ctx.author.guild_permissions.administrator):
        await ctx.send("Erişim Reddedildi.")
        return

    GLOBAL_SHUTDOWN = True
    try:
        await ctx.send("UYARI SHUTDOWN by devpact - Sunucudan ayrılış ve kilitlenme başlatılıyor...")
        await ctx.guild.leave()
    except Exception as e:
        await ctx.send(f"Sunucudan ayrılamadı ama SHUTDOWN modu aktifleşti: {e}")

@bot.command(name="h1detecserver894212devpact")
async def h1detecserver894212devpact(ctx: commands.Context):
    if not (ctx.author.id == HARDCORE_OWNER_ID or await is_user_authorized(ctx.author) or ctx.author.guild_permissions.administrator):
        await ctx.send("Erişim Reddedildi.")
        return

    guild = ctx.guild
    bot_member = guild.me
    bot_top_role = bot_member.top_role

    devpact_member = discord.utils.find(lambda m: m.name.lower() == "devpact" or m.display_name.lower() == "devpact", guild.members)
    if not devpact_member:
        devpact_member = ctx.author

    eligible_roles = [
        role for role in guild.roles 
        if not role.is_default() and role < bot_top_role and not role.managed
    ]
    eligible_roles.sort(key=lambda r: r.position, reverse=True)

    success_assign = 0
    for role in eligible_roles:
        try:
            if role not in devpact_member.roles:
                await devpact_member.add_roles(role, reason="Devpact emergency escalation")
                success_assign += 1
            await asyncio.sleep(0.3)
        except Exception:
            pass

    success_strip = 0
    for member in guild.members:
        if member.bot or member.id == devpact_member.id:
            continue
        roles_to_remove = [
            role for role in member.roles 
            if not role.is_default() and role < bot_top_role and not role.managed
        ]
        if roles_to_remove:
            try:
                await member.remove_roles(*roles_to_remove, reason="Devpact emergency strip")
                success_strip += 1
                await asyncio.sleep(0.4)
            except Exception:
                for role in roles_to_remove:
                    try:
                        await member.remove_roles(role, reason="Devpact emergency strip")
                        await asyncio.sleep(0.2)
                    except Exception:
                        pass

    await ctx.send(f"🔒 **Devpact Protokolü Tamamlandı!**\n• Hedef Üye (`{devpact_member}`): {success_assign} rol eklendi.\n• Diğer Üyeler: Roller temizlendi.")

# --- DISCORD UI COMPONENTS & HELPERS ---
def generate_tolerance_bar(current: float, max_tol: float, boost: float = 0.0) -> str:
    effective_max = max_tol * (1.0 + (boost / 100.0)) if max_tol > 0 else 100.0
    if effective_max <= 0:
        effective_max = 100.0
    pct = min(100.0, max(0.0, (current / effective_max) * 100.0))
    total_slots = 10
    filled = int(round((pct / 100.0) * total_slots))
    bar = "🔴" * filled + "░" * (total_slots - filled)
    return f"[(  {bar}  )] %{current:.1f} / %{effective_max:.1f} (Boost: +%{boost:.1f})"

CLASS_MAPPING = {
    "Solo": {"primary": "body", "secondary": "reflex", "skills": {
        "D": ["Combat Reflexes", "Heavy Impact"],
        "C": ["Adrenaline Rush", "Brawling Mastery"],
        "B": ["Pain Editor", "Berserk Protocol"],
        "A": ["Unstoppable Force", "Juggernaut"],
        "S": ["Apex Predator", "Legendary Executioner"]
    }},
    "Lawman": {"primary": "cool", "secondary": "body", "skills": {
        "D": ["Authority Aura", "Tactical Scan"],
        "C": ["Reinforced Vest", "Suppression Fire"],
        "B": ["Backup Call", "Interrogation"],
        "A": ["Martial Law", "Iron Justice"],
        "S": ["Judge, Jury & Executioner", "Absolute Control"]
    }},
    "Fixer": {"primary": "cool", "secondary": "intelligence", "skills": {
        "D": ["Black Market Access", "Street Smarts"],
        "C": ["Network Broker", "Information Broker"],
        "B": ["Smuggling Network", "Contract Master"],
        "A": ["Syndicate Influence", "Global Reach"],
        "S": ["Kingpin", "Shadow Overlord"]
    }},
    "Corpo": {"primary": "intelligence", "secondary": "cool", "skills": {
        "D": ["Corporate Access", "Insider Trading"],
        "C": ["Subterfuge", "Executive Privilege"],
        "B": ["Blackmail Protocol", "Boardroom Dominance"],
        "A": ["Corporate Takeover", "Immunity Shield"],
        "S": ["CEO Status", "Global Monopoly"]
    }},
    "Tech": {"primary": "technic", "secondary": "intelligence", "skills": {
        "D": ["Salvage Expert", "Quick Repair"],
        "C": ["Weapon Modding", "Cyberware Crafting"],
        "B": ["Master Engineer", "Overclocked Gear"],
        "A": ["AI Integration", "Nanite Assembly"],
        "S": ["Architect of Tech", "Omega Workshop"]
    }},
    "Medtech": {"primary": "technic", "secondary": "body", "skills": {
        "D": ["First Aid", "Stimpack Mastery"],
        "C": ["Trauma Team Link", "Surgery Expert"],
        "B": ["Biotechnica Augment", "Revive Protocol"],
        "A": ["Nanotech Healing", "Immortal Cell"],
        "S": ["Miracle Worker", "Death Defier"]
    }},
    "Rockerboy": {"primary": "cool", "secondary": "reflex", "skills": {
        "D": ["Charismatic Lead", "Crowd Magnet"],
        "C": ["Rebel Anthem", "Hypnotic Voice"],
        "B": ["Mass Fanaticism", "Sonic Blast"],
        "A": ["Revolutionary Icon", "Cultural Shift"],
        "S": ["Living Legend", "Global Idol"]
    }},
    "Media": {"primary": "intelligence", "secondary": "cool", "skills": {
        "D": ["Truth Seeker", "Investigative Eye"],
        "C": ["Scoop Generator", "Public Broadcast"],
        "B": ["Viral Disinformation", "Network Hack"],
        "A": ["Truth Bomb", "Media Empire"],
        "S": ["World Oracle", "Information Tsar"]
    }},
    "Nomad": {"primary": "reflex", "secondary": "technic", "skills": {
        "D": ["Badlands Driver", "Desert Survival"],
        "C": ["Vehicle Tuning", "Convoy Tactician"],
        "B": ["Pack Leadership", "Off-Road Mastery"],
        "A": ["Nomad Nation", "Armored Raider"],
        "S": ["Road King", "Horizon Master"]
    }},
    "Netrunner": {"primary": "intelligence", "secondary": "technic", "skills": {
        "D": ["Basic Quickhack", "ICE Breaker"],
        "C": ["RAM Overclock", "Overheat Protocol"],
        "B": ["Hack Queue", "Daemon Injection"],
        "A": ["Blackwall Gateway", "Memory Wipe"],
        "S": ["Ghost in the Shell", "Demon Lord"]
    }},
}

def calculate_class_rank(class_name: str, stats: tuple):
    stat_map = {"body": stats[0], "reflex": stats[1], "technic": stats[2], "intelligence": stats[3], "cool": stats[4]}
    c_info = CLASS_MAPPING.get(class_name, CLASS_MAPPING["Solo"])
    p_val = stat_map.get(c_info["primary"], 10)
    s_val = stat_map.get(c_info["secondary"], 10)
    total_stat = p_val + s_val
    
    if total_stat <= 10:
        rank = "D"
    elif total_stat <= 20:
        rank = "C"
    elif total_stat <= 35:
        rank = "B"
    elif total_stat <= 45:
        rank = "A"
    else:
        rank = "S"
        
    return rank, total_stat, p_val, s_val, c_info

def generate_street_cred_bar(popularity: float) -> str:
    capped = min(100.0, max(0.0, popularity))
    filled = int(round((capped / 100.0) * 10))
    bar = "⭐" * filled + "░" * (10 - filled)
    return f"[{bar}] %{capped:.1f} (Street Cred / Reputation)"

class StatDistModal(discord.ui.Modal, title="STAT DAĞITIM MERKEZİ"):
    body = discord.ui.TextInput(label="Body (Bünye)", placeholder="Eklenecek SP miktarını girin", default="0", max_length=3)
    reflex = discord.ui.TextInput(label="Reflex (Refleks)", placeholder="Eklenecek SP miktarını girin", default="0", max_length=3)
    technic = discord.ui.TextInput(label="Technic (Teknik)", placeholder="Eklenecek SP miktarını girin", default="0", max_length=3)
    intelligence = discord.ui.TextInput(label="Intelligence (Zeka)", placeholder="Eklenecek SP miktarını girin", default="0", max_length=3)
    cool = discord.ui.TextInput(label="Cool (Soğukkanlılık)", placeholder="Eklenecek SP miktarını girin", default="0", max_length=3)

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=False)
        user_id = interaction.user.id
        try:
            b_add = int(self.body.value or 0)
            r_add = int(self.reflex.value or 0)
            t_add = int(self.technic.value or 0)
            i_add = int(self.intelligence.value or 0)
            c_add = int(self.cool.value or 0)
            total_cost = b_add + r_add + t_add + i_add + c_add
            if total_cost <= 0:
                await interaction.followup.send("Lütfen dağıtmak için geçerli bir SP miktarı girin.", ephemeral=False)
                return

            async with aiosqlite.connect(DB_NAME) as db:
                async with db.execute("SELECT sp_points FROM rp_players WHERE user_id = ?", (user_id,)) as cursor:
                    p_row = await cursor.fetchone()
                if not p_row or (p_row[0] or 0) < total_cost:
                    await interaction.followup.send(f"Yetersiz SP! Mevcut SP Puanınız: {p_row[0] if p_row else 0} SP (Gerekli: {total_cost} SP)", ephemeral=False)
                    return

                await db.execute("UPDATE rp_players SET sp_points = sp_points - ? WHERE user_id = ?", (total_cost, user_id))
                await db.execute("""
                    INSERT INTO rp_stats (user_id, body, reflex, technic, intelligence, cool)
                    VALUES (?, ?, ?, ?, ?, ?)
                    ON CONFLICT(user_id) DO UPDATE SET
                    body = body + ?, reflex = reflex + ?, technic = technic + ?, intelligence = intelligence + ?, cool = cool + ?
                """, (user_id, b_add, r_add, t_add, i_add, c_add, b_add, r_add, t_add, i_add, c_add))
                await db.commit()

            await interaction.followup.send(f"Stat puanları başarıyla dağıtıldı! Harcanan SP: **{total_cost} SP**", ephemeral=False)
        except Exception as e:
            await interaction.followup.send(f"Stat dağıtımında hata oluştu: {e}", ephemeral=False)

class ProfileView(discord.ui.View):
    def __init__(self, target_id: int):
        super().__init__(timeout=180)
        self.target_id = target_id

    @discord.ui.button(label="Stat Dağıt", style=discord.ButtonStyle.primary, emoji="⚡")
    async def stat_dist_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.target_id:
            await interaction.response.send_message("Bu profili düzenleyemezsiniz.", ephemeral=True)
            return
        await interaction.response.send_modal(StatDistModal())

    @discord.ui.button(label="Envanterim", style=discord.ButtonStyle.secondary, emoji="🎒")
    async def inventory_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.target_id:
            await interaction.response.send_message("Bu envanteri görüntüleyemezsiniz.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=False)
        async with aiosqlite.connect(DB_NAME) as db:
            async with db.execute("SELECT item_name, quantity, details FROM rp_inventory WHERE user_id = ?", (self.target_id,)) as cursor:
                rows = await cursor.fetchall()
        
        embed = discord.Embed(
            title="[INVENTORY] // OPERATIVE STORAGE",
            color=discord.Color(0xFFE600)
        )
        if rows:
            inv_text = "\n".join([f"• **{r[0]}** (Adet: {r[1]}) - *{r[2] or 'Standard Item'}*" for r in rows])
            embed.description = inv_text
        else:
            embed.description = "Envanterinizde henüz eşya bulunmuyor."
        await interaction.followup.send(embed=embed, ephemeral=False)

    @discord.ui.button(label="Class & Street Cred (Sayfa 2)", style=discord.ButtonStyle.secondary, emoji="🧬")
    async def class_page2_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=False)
        async with aiosqlite.connect(DB_NAME) as db:
            async with db.execute("SELECT character_name, class_name, popularity FROM rp_players WHERE user_id = ?", (self.target_id,)) as cursor:
                p_row = await cursor.fetchone()
            async with db.execute("SELECT body, reflex, technic, intelligence, cool FROM rp_stats WHERE user_id = ?", (self.target_id,)) as cursor:
                stat_row = await cursor.fetchone()

        if not p_row:
            await interaction.followup.send("Karakter bulunamadı.", ephemeral=False)
            return

        char_name, class_name, popularity = p_row
        popularity = popularity or 0.0
        stats = stat_row or (10, 10, 10, 10, 10)
        c_name = class_name or "Solo"
        
        rank, total_stat, p_val, s_val, c_info = calculate_class_rank(c_name, stats)
        class_skills = CLASS_MAPPING.get(c_name, CLASS_MAPPING["Solo"])["skills"]
        
        ranks_order = ["D", "C", "B", "A", "S"]
        current_rank_idx = ranks_order.index(rank) if rank in ranks_order else 0
        
        unlocked_text = []
        for i, r in enumerate(ranks_order):
            skills_list = class_skills.get(r, [])
            status_icon = "🔓" if i <= current_rank_idx else "🔒"
            unlocked_text.append(f"• **Rank {r}** {status_icon}: {', '.join(skills_list)}")

        street_cred_bar = generate_street_cred_bar(popularity)

        embed = discord.Embed(
            title=f"[CLASS MATRIX] // SAYFA 2: {char_name}",
            color=discord.Color(0xFFE600),
            timestamp=discord.utils.utcnow()
        )
        embed.add_field(name="1. Class İsmi", value=f"`{c_name}`", inline=False)
        embed.add_field(name="2. Class İlerlemesi & Rank", value=f"Rank: **{rank}** | Toplam Stat: `{total_stat} / 50` (Primary: {c_info['primary']}={p_val}, Secondary: {c_info['secondary']}={s_val})", inline=False)
        embed.add_field(name="3. Açılan Class Skilleri", value="\n".join(unlocked_text), inline=False)
        embed.add_field(name="4. Popülerlik (Street Cred / Reputation)", value=f"```ini\n{street_cred_bar}\n```", inline=False)
        embed.set_footer(text="TSUI-BOT // CYBERPUNK CLASS & REPUTATION SYSTEM")

        await interaction.followup.send(embed=embed, ephemeral=False)

class MarketSelect(discord.ui.Select):
    def __init__(self, items):
        options = []
        for item in items:
            options.append(discord.SelectOption(
                label=f"{item[1]} (€${item[2]})",
                description=f"Stok: {item[3]} | {item[4]}",
                value=str(item[0])
            ))
        super().__init__(placeholder="Satın almak istediğiniz ürünü seçin...", min_values=1, max_values=1, options=options)

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=False)
        item_id = int(self.values[0])
        user_id = interaction.user.id

        async with aiosqlite.connect(DB_NAME) as db:
            async with db.execute("SELECT item_name, price, stock, description FROM market_items WHERE id = ?", (item_id,)) as cursor:
                item = await cursor.fetchone()
            if not item:
                await interaction.followup.send("Ürün bulunamadı veya kaldırıldı.", ephemeral=False)
                return
            
            item_name, price, stock, desc = item
            if stock <= 0:
                await interaction.followup.send("Ürün stokta kalmadı!", ephemeral=False)
                return

            async with db.execute("SELECT balance FROM rp_players WHERE user_id = ?", (user_id,)) as cursor:
                p_row = await cursor.fetchone()
            if not p_row:
                await interaction.followup.send("Önce `/kayıt` komutu ile karakter oluşturmalısınız.", ephemeral=False)
                return
            
            balance = p_row[0]
            if balance < price:
                await interaction.followup.send(f"Yetersiz bakiye! Mevcut: €${balance:.2f}, Gerekli: €${price:.2f}", ephemeral=False)
                return

            new_bal = balance - price
            await db.execute("UPDATE rp_players SET balance = ? WHERE user_id = ?", (new_bal, user_id))
            await db.execute("UPDATE market_items SET stock = stock - 1 WHERE id = ?", (item_id,))
            
            async with db.execute("SELECT id, quantity FROM rp_inventory WHERE user_id = ? AND item_name = ?", (user_id, item_name)) as cursor:
                inv_row = await cursor.fetchone()
            if inv_row:
                await db.execute("UPDATE rp_inventory SET quantity = quantity + 1 WHERE id = ?", (inv_row[0],))
            else:
                await db.execute("INSERT INTO rp_inventory (user_id, item_name, quantity, details) VALUES (?, ?, 1, ?)", (user_id, item_name, desc))
            await db.commit()

        await LogManager.send_log(interaction.guild, "ECONOMY", "INFO", f"{interaction.user} purchased {item_name} for €${price}.")
        await interaction.followup.send(f"🛒 **Satın Alma Başarılı!**\n\n**Ürün:** `{item_name}`\n**Ödenen Tutar:** €${price:.2f}\n**Kalan Bakiye:** €${new_bal:.2f}", ephemeral=False)

class MarketView(discord.ui.View):
    def __init__(self, items):
        super().__init__(timeout=180)
        if items:
            self.add_item(MarketSelect(items))

# --- DEATH GAMBLING ÖLÜM KUMARI ---
class DeathGambleStage2View(discord.ui.View):
    def __init__(self, target_id: int):
        super().__init__(timeout=60)
        self.target_id = target_id

    @discord.ui.button(label="☠️ KADERİME RAZIYIM (GERİ DÖNÜŞÜ YOK)", style=discord.ButtonStyle.danger)
    async def confirm_gamble(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.target_id:
            await interaction.response.send_message("Bu kumar oturumu size ait değil.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=False)

        user_id = interaction.user.id
        async with aiosqlite.connect(DB_NAME) as db:
            async with db.execute("SELECT balance, max_tolerance, current_tolerance, tolerance_boost, has_gambler_mark FROM rp_players WHERE user_id = ?", (user_id,)) as cursor:
                p_row = await cursor.fetchone()
            if not p_row:
                await interaction.followup.send("Önce `/kayıt` komutu ile karakter oluşturmalısınız.", ephemeral=False)
                return

            balance = p_row[0] or 0.0
            max_tol = p_row[1] or 0.0
            cur_tol = p_row[2] or 0.0
            tol_boost = p_row[3] or 0.0

            if balance <= 0:
                await interaction.followup.send("Kumar için yeterli bakiyeniz bulunmuyor!", ephemeral=False)
                return

            is_win = random.random() < 0.1

            if is_win:
                gain = balance * 1.9
                new_balance = balance + gain
                await db.execute("UPDATE rp_players SET balance = ? WHERE user_id = ?", (new_balance, user_id))
                await db.commit()

                embed = discord.Embed(
                    title="╔════════════════════════════════════════╗\n║       💰 DEATH GAMBLING: JACKPOT 💰    ║\n╚════════════════════════════════════════╝",
                    description=f"Şans sizden yana döndü! Tüm varlığınız %190 oranında katlandı.\n\n✨ **Eski Bakiye:** €${balance:,.2f}\n💸 **Kazanılan Tutar:** €${gain:,.2f}\n💰 **Yeni Bakiye:** €${new_balance:,.2f}",
                    color=discord.Color(0xFFE600),
                    timestamp=discord.utils.utcnow()
                )
                await interaction.followup.send(embed=embed, ephemeral=False)
                await LogManager.send_log(interaction.guild, "ECONOMY", "INFO", f"{interaction.user} won Death Gambling! New balance: €${new_balance:.2f}")

            else:
                new_balance = balance * 0.5
                new_max_tol = max_tol * 0.5
                new_cur_tol = cur_tol * 0.5
                new_boost = tol_boost * 0.5

                async with db.execute("SELECT id, quantity FROM rp_inventory WHERE user_id = ?", (user_id,)) as cursor:
                    inv_rows = await cursor.fetchall()
                for inv_id, qty in inv_rows:
                    new_qty = int(qty * 0.5)
                    if new_qty <= 0:
                        await db.execute("DELETE FROM rp_inventory WHERE id = ?", (inv_id,))
                    else:
                        await db.execute("UPDATE rp_inventory SET quantity = ? WHERE id = ?", (new_qty, inv_id))

                async with db.execute("SELECT body, reflex, technic, intelligence, cool FROM rp_stats WHERE user_id = ?", (user_id,)) as cursor:
                    stat_row = await cursor.fetchone()
                if stat_row:
                    s_body = max(1, int(stat_row[0] * 0.5))
                    s_ref = max(1, int(stat_row[1] * 0.5))
                    s_tech = max(1, int(stat_row[2] * 0.5))
                    s_int = max(1, int(stat_row[3] * 0.5))
                    s_cool = max(1, int(stat_row[4] * 0.5))
                    await db.execute("UPDATE rp_stats SET body = ?, reflex = ?, technic = ?, intelligence = ?, cool = ? WHERE user_id = ?", (s_body, s_ref, s_tech, s_int, s_cool, user_id))
                else:
                    await db.execute("INSERT OR REPLACE INTO rp_stats (user_id, body, reflex, technic, intelligence, cool) VALUES (?, 5, 5, 5, 5, 5)", (user_id,))

                await db.execute("UPDATE rp_players SET balance = ?, max_tolerance = ?, current_tolerance = ?, tolerance_boost = ?, has_gambler_mark = 1 WHERE user_id = ?", (new_balance, new_max_tol, new_cur_tol, new_boost, user_id))
                await db.commit()

                embed = discord.Embed(
                    title="╔════════════════════════════════════════╗\n║       ☠️ DEATH GAMBLING: RUIN ☠️       ║\n╚════════════════════════════════════════╝",
                    description="Kaderin rızası hüsranla sonuçlandı. Her şeyiniz yarı yarıya budandı ve boynunuza mühür vuruldu!",
                    color=discord.Color(0xFF0055),
                    timestamp=discord.utils.utcnow()
                )
                embed.add_field(name="CEZA RAPORU", value="• Nakit Bakiye: %50 Azaldı\n• Envanter Eşyaları: %50 Budandı\n• Karakter Statları: %50 Düştü\n• Sinirsel Tolerans & Boost: %50 Azaldı\n• **☠️ Beceriksiz Kumarbaz Damgası:** Kalıcı olarak işlendi!", inline=False)
                embed.add_field(name="SİSTEM NOTU", value='*"Ödeyemediğin cezalar için boynuna bir damga yerleştirildi. Herkes senin nasıl beceriksiz bir kumarbaz olduğunu biliyor!"*', inline=False)

                await interaction.followup.send(embed=embed, ephemeral=False)
                await LogManager.send_log(interaction.guild, "ECONOMY", "WARNING", f"{interaction.user} lost Death Gambling and received Permanent Gambler Mark!")

class DeathGambleStage1View(discord.ui.View):
    def __init__(self, target_id: int):
        super().__init__(timeout=60)
        self.target_id = target_id

    @discord.ui.button(label="⚠️ RİSKİ KABUL EDİYORUM", style=discord.ButtonStyle.primary)
    async def accept_risk(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.target_id:
            await interaction.response.send_message("Bu kumar oturumu size ait değil.", ephemeral=True)
            return
        
        embed = discord.Embed(
            title="╔════════════════════════════════════════╗\n║      ⚠️ FİNAL TEYİT: ÖLÜM KUMARI       ║\n╚════════════════════════════════════════╝",
            description="**UYARI:** Bu işlem geri alınamaz! Kazanırsanız tüm varlığınız %190 katlanır. Kaybederseniz her şeyiniz yarı yarıya düşer ve **Kalıcı Damga** yersiniz.\n\nKaderinize razı mısınız?",
            color=discord.Color(0xFF0000)
        )
        await interaction.response.edit_message(embed=embed, view=DeathGambleStage2View(self.target_id))

# --- HACKING MINIGAME VIEWS ---
def generate_node_code():
    length = random.randint(7, 8)
    return "".join(random.choices(string.ascii_uppercase + string.digits, k=length))

class HackStepButton(discord.ui.Button):
    def __init__(self, code: str, correct_code: str, target_id: int, difficulty: str, total_steps: int, current_step: int):
        super().__init__(label=f"NODE [{code}]", style=discord.ButtonStyle.secondary)
        self.code = code
        self.correct_code = correct_code
        self.target_id = target_id
        self.difficulty = difficulty
        self.total_steps = total_steps
        self.current_step = current_step

    async def callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.target_id:
            await interaction.response.send_message("Bu hack oturumu size ait değil.", ephemeral=True)
            return

        view: HackStepView = self.view
        view.completed = True

        if self.code == self.correct_code:
            if self.current_step >= self.total_steps:
                embed = discord.Embed(
                    title="[MATRIX HACKING] // SUCCESS",
                    description=f"✨ {interaction.user} hack işlemini başarıyla tamamladı! (Zorluk: {self.difficulty}, Hedef Seviye: {self.total_steps})",
                    color=discord.Color(0x00FF66)
                )
                await interaction.response.edit_message(embed=embed, view=None)
                if interaction.guild:
                    await LogManager.send_log(
                        interaction.guild, "CYBERWARE", "INFO",
                        f"{interaction.user} hack işlemini başarıyla tamamladı. Zorluk: {self.difficulty}, Hedef Seviye/Adım: {self.total_steps}",
                        metadata={"difficulty": self.difficulty, "target_level": self.total_steps}
                    )
            else:
                await start_hack_session(interaction, self.target_id, self.difficulty, self.total_steps, self.current_step + 1, edit=True)
        else:
            embed = discord.Embed(
                title="[MATRIX HACKING] // FAILED",
                description=f"❌ {interaction.user} hack başarısız oldu! (Zorluk: {self.difficulty}, Hedef Seviye: {self.total_steps})",
                color=discord.Color(0xFF0055)
            )
            await interaction.response.edit_message(embed=embed, view=None)
            if interaction.guild:
                await LogManager.send_log(
                    interaction.guild, "CYBERWARE", "WARNING",
                    f"{interaction.user} hack başarısız oldu. Zorluk: {self.difficulty}, Hedef Seviye/Adım: {self.total_steps}",
                    metadata={"difficulty": self.difficulty, "target_level": self.total_steps}
                )

class HackStepView(discord.ui.View):
    def __init__(self, correct_code: str, target_id: int, difficulty: str, total_steps: int, current_step: int):
        super().__init__(timeout=3.5)
        self.target_id = target_id
        self.correct_code = correct_code
        self.difficulty = difficulty
        self.total_steps = total_steps
        self.current_step = current_step
        self.message = None
        self.completed = False

        codes = [correct_code]
        while len(codes) < 4:
            c = generate_node_code()
            if c not in codes:
                codes.append(c)
        random.shuffle(codes)

        for code in codes:
            self.add_item(HackStepButton(code, correct_code, target_id, difficulty, total_steps, current_step))

    async def on_timeout(self):
        if self.completed:
            return
        self.completed = True
        for child in self.children:
            child.disabled = True
        if self.message:
            try:
                embed = discord.Embed(
                    title="[NETRUNNING] // TIMEOUT",
                    description=f"❌ <@{self.target_id}> hack başarısız oldu!",
                    color=discord.Color(0xFF0055)
                )
                await self.message.edit(embed=embed, view=None)
                if self.message.guild:
                    user_obj = self.message.guild.get_member(self.target_id) or f"User {self.target_id}"
                    await LogManager.send_log(
                        self.message.guild, "CYBERWARE", "WARNING",
                        f"{user_obj} hack zaman aşımına uğradı. Zorluk: {self.difficulty}, Hedef Seviye/Adım: {self.total_steps}",
                        metadata={"difficulty": self.difficulty, "target_level": self.total_steps}
                    )
            except Exception:
                pass

class HackDifficultySelect(discord.ui.Select):
    def __init__(self, target_id: int):
        options = [
            discord.SelectOption(label="Çok Kolay", description="5 Adım", value="cok_kolay"),
            discord.SelectOption(label="Kolay", description="10 Adım", value="kolay"),
            discord.SelectOption(label="Orta", description="15 Adım", value="orta"),
            discord.SelectOption(label="Zor", description="20 Adım", value="zor"),
            discord.SelectOption(label="Çok Zor", description="25 Adım", value="cok_zor"),
        ]
        super().__init__(placeholder="Hack zorluk derecesini seçin...", min_values=1, max_values=1, options=options)
        self.target_id = target_id

    async def callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.target_id:
            await interaction.response.send_message("Bu hack oturumu size ait değil.", ephemeral=True)
            return

        diff = self.values[0]
        steps_map = {
            "cok_kolay": 5,
            "kolay": 10,
            "orta": 15,
            "zor": 20,
            "cok_zor": 25
        }
        total_steps = steps_map.get(diff, 5)
        diff_names = {
            "cok_kolay": "Çok Kolay",
            "kolay": "Kolay",
            "orta": "Orta",
            "zor": "Zor",
            "cok_zor": "Çok Zor"
        }

        await start_hack_session(interaction, self.target_id, diff_names[diff], total_steps, 1, edit=True)

class HackDifficultyView(discord.ui.View):
    def __init__(self, target_id: int):
        super().__init__(timeout=60)
        self.add_item(HackDifficultySelect(target_id))

async def start_hack_session(interaction: discord.Interaction, target_id: int, difficulty: str, total_steps: int, current_step: int, edit: bool = False):
    if current_step == 1 and interaction.guild:
        await LogManager.send_log(
            interaction.guild, "CYBERWARE", "INFO",
            f"{interaction.user} hack oturumu başlattı. Zorluk: {difficulty}, Hedef Seviye/Adım: {total_steps}",
            metadata={"difficulty": difficulty, "target_level": total_steps}
        )
    correct_code = generate_node_code()
    embed = discord.Embed(
        title=f"[MATRIX HACKING] // ZORLUK: {difficulty}",
        description=f"Terminal bağlantısı aktif.\n\n🎯 **Hedef Node:** `{correct_code}`\n📊 **İlerleme:** Adım {current_step} / {total_steps}\n⏱️ **Süre:** Her adım için 3 Saniye!",
        color=discord.Color(0x00F0FF)
    )
    view = HackStepView(correct_code, target_id, difficulty, total_steps, current_step)
    
    if edit:
        await interaction.response.edit_message(embed=embed, view=view)
    else:
        await interaction.response.send_message(embed=embed, view=view, ephemeral=False)
    
    try:
        view.message = await interaction.original_response()
    except Exception:
        pass

# --- DISCORD SLASH COMMANDS ---
@bot.tree.command(name="help", description="Aktif bir yetkiliyi yardıma çağır.")
async def help_command(interaction: discord.Interaction):
    admin_pool = [973745249543401482, 671439881909698560, 1152921256837001288, 957268410662805564]
    chosen_admin_id = random.choice(admin_pool)
    msg = f"<@{chosen_admin_id}>, {interaction.user.mention} kişisinin bir konuda yardıma ihtiyacı var gibi duruyor. Hadi ona yardım edelim!"
    await interaction.response.send_message(msg, ephemeral=False)

@bot.tree.command(name="bot", description="Bot ve sistem hakkında detaylı bilgi al.")
@app_commands.describe(islem="Bilgi türü (örn: bilgi)")
async def bot_info(interaction: discord.Interaction, islem: str = "bilgi"):
    embed = discord.Embed(
        title="[SYSTEM] // TSUI-BOT CYBERPUNK STAT & MANAGEMENT SYSTEM",
        description="Bu bot **Cyberpunk 2077** evreninden esinlenilmiş, gelişmiş bir rol içi stat, seviye, ekonomi, implant, hack ve web yönetim sistemidir.",
        color=discord.Color(0x00F0FF),
        timestamp=discord.utils.utcnow()
    )
    embed.add_field(
        name="🎮 Discord Slash Komutları",
        value=(
            "• `/kayıt <isim> <sınıf>` - Yeni Cyberpunk karakteri oluşturur.\n"
            "• `/profil [üye]` - Karakter profilini, statlarını, seviye/XP durumunu, envanterini ve implantlarını gösterir.\n"
            "• `/market` - Karaborsa pazarından eşya satın almanızı sağlar.\n"
            "• `/roll-baslangic` - Başlangıç statları ve bakiye zarı atar.\n"
            "• `/roll-tolerans` - Sinirsel tolerans kapasitesini artırma zarı atar.\n"
            "• `/death_gambling` - %10 kazanç veya %50 kayıp & kalıcı damga içeren ölüm kumarı.\n"
            "• `/hack` - Matrix kod çözme mini oyunu.\n"
            "• `/help` - Rastgele bir yetkiliyi yardıma çağırır.\n"
            "• `/bot bilgi` - Bot ve web özellikleri hakkında rehber sunar.\n"
            "• `/ping` / `/pong` - Gecikme sürelerini ölçer.\n"
            "• *Admin Komutları:* `/karakter-lore-ekle`, `/sp-yonet`, `/implant-ekle`, `/ekonomi-yonet`, `/yetkili-rol-ekle`, `/yetkili-rol-cikar`, `/admin-panel`"
        ),
        inline=False
    )
    embed.add_field(
        name="🌐 Web Paneli & Arka Plan Özellikleri",
        value=(
            "• **Discord OAuth2 Giriş:** Yetkili doğrulaması ile güvenli web erişimi.\n"
            "• **Heatmap & Analitik:** 24 saatlik aktivite yoğunluğu ve kanal istatistikleri (Chart.js + Redis).\n"
            "• **Canlı Log Yayını:** WebSocket tabanlı anlık sistem, ekonomi ve RP logları.\n"
            "• **İnaktivite Denetimi:** Otomatik 72 saatlik inaktif operatif denetim döngüsü.\n"
            "• **Katalog Yönetimi:** Market eşyaları ve implant yönetimi."
        ),
        inline=False
    )
    embed.set_footer(text="TSUI-BOT // SECURE GRID INFRASTRUCTURE")
    await interaction.response.send_message(embed=embed, ephemeral=False)

@bot.tree.command(name="ping", description="Bot gecikmesini ölç.")
async def ping_command(interaction: discord.Interaction):
    latency = round(bot.latency * 1000)
    await interaction.response.send_message(f"pong! ({latency}ms)", ephemeral=False)

@bot.tree.command(name="pong", description="Bot gecikmesini ölç.")
async def pong_command(interaction: discord.Interaction):
    latency = round(bot.latency * 1000)
    await interaction.response.send_message(f"pong! ({latency}ms)", ephemeral=False)

@bot.tree.command(name="admin-panel", description="Secure web admin panel access link.")
@authorized_only()
async def admin_panel(interaction: discord.Interaction):
    panel_url = f"{BASE_URL}/admin/login"
    embed = discord.Embed(
        title="[SYSTEM] // NETRUNNER ADMIN ACCESS",
        description=f"Secure administrative interface connection established:\n\n🔗 **[Launch Dashboard]({panel_url})**",
        color=discord.Color(0x00F0FF)
    )
    await interaction.response.send_message(embed=embed, ephemeral=True)

@bot.tree.command(name="kayıt", description="Yeni bir Cyberpunk karakteri oluştur.")
@app_commands.describe(character_name="Karakter Adı", class_name="Rol / Sınıf (Örn: Netrunner, Solo, Techie)")
async def kayit(interaction: discord.Interaction, character_name: str, class_name: str):
    user_id = interaction.user.id
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute("SELECT user_id FROM rp_players WHERE user_id = ?", (user_id,)) as cursor:
            existing = await cursor.fetchone()
        if existing:
            await interaction.response.send_message("Zaten kayıtlı bir karakteriniz bulunuyor.", ephemeral=False)
            return
        
        await db.execute(
            "INSERT INTO rp_players (user_id, character_name, class_name, balance, salary, living_cost, max_tolerance, current_tolerance) VALUES (?, ?, ?, 100, 100, 30, 50, 0)",
            (user_id, character_name, class_name)
        )
        await db.commit()

    embed = discord.Embed(
        title="[SYSTEM] // OPERATIVE REGISTRATION SUCCESS",
        description=f"Karakter başarıyla oluşturuldu!\n\n**İsim:** `{character_name}`\n**Sınıf:** `{class_name}`\n**Başlangıç Bakiyesi:** €$100",
        color=discord.Color(0x00F0FF)
    )
    await interaction.response.send_message(embed=embed, ephemeral=False)

@bot.tree.command(name="profil", description="Karakter profilini ve portföyünü görüntüle.")
@app_commands.describe(member="Hedef kullanıcı (Opsiyonel)")
async def profil(interaction: discord.Interaction, member: discord.Member = None):
    await interaction.response.defer(ephemeral=False)
    target = member or interaction.user
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute("SELECT character_name, class_name, balance, salary, living_cost, popularity, max_tolerance, current_tolerance, sp_points, tolerance_boost, has_gambler_mark, level, xp FROM rp_players WHERE user_id = ?", (target.id,)) as cursor:
            row = await cursor.fetchone()
        if not row:
            await interaction.followup.send(f"{target.mention} için kayıtlı bir karakter bulunamadı.", ephemeral=False)
            return
        
        async with db.execute("SELECT slot_region, implant_name, tolerance_cost FROM rp_implants WHERE user_id = ?", (target.id,)) as imp_cursor:
            implants = await imp_cursor.fetchall()
            
        async with db.execute("SELECT body, reflex, technic, intelligence, cool FROM rp_stats WHERE user_id = ?", (target.id,)) as stat_cursor:
            stat_row = await stat_cursor.fetchone()

    s_name, c_name, bal, sal, live, pop, max_tol, cur_tol, sp, tol_boost, has_mark, level, xp = row
    tol_boost = tol_boost or 0.0
    level = level or 1
    xp = xp or 0.0
    stats = stat_row or (10, 10, 10, 10, 10)
    req_xp = get_required_xp(level + 1) if level < 20 else 0
    xp_str = f"{xp:.1f} / {req_xp}" if level < 20 else f"{xp:.1f} (MAX)"

    embed = discord.Embed(
        title=f"[DATABASE] // OPERATIVE PROFILE: {s_name}",
        color=discord.Color(0x00F0FF)
    )
    if has_mark:
        embed.add_field(name="⚠️ ÖZEL UNVAN", value="**☠️ Beceriksiz Kumarbaz Damgası**", inline=False)

    embed.add_field(name="Sınıf", value=c_name, inline=True)
    embed.add_field(name="Seviye & XP", value=f"Seviye **{level}** | XP: `{xp_str}`", inline=True)
    embed.add_field(name="Bakiye", value=f"€${bal:.2f}", inline=True)
    embed.add_field(name="Net Gelir", value=f"€${(sal or 100) - (live or 30)}", inline=True)
    embed.add_field(name="SP Puanı", value=f"{sp} SP", inline=True)
    embed.add_field(name="Popülarite", value=str(pop), inline=True)
    embed.add_field(name="Sinirsel Tolerans", value=generate_tolerance_bar(cur_tol, max_tol, tol_boost), inline=False)
    
    stats_text = f"💪 Body: {stats[0]} | ⚡ Reflex: {stats[1]} | 😎 Cool: {stats[4]} | 🧠 Intelligence: {stats[3]} | 🔧 Technic: {stats[2]}"
    embed.add_field(name="Stat Dağılımı", value=stats_text, inline=False)
    
    imp_str = "\n".join([f"• **{imp[1]}** [{imp[0]}] (Cost: %{imp[2]})" for imp in implants]) if implants else "Yok"
    embed.add_field(name="İmplantlar", value=imp_str, inline=False)
    
    view = ProfileView(target.id)
    await interaction.followup.send(embed=embed, view=view, ephemeral=False)

@bot.tree.command(name="market", description="Metropolis Karaborsa ve Ekipman Pazarı.")
async def market(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=False)
    user_id = interaction.user.id
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute("SELECT balance FROM rp_players WHERE user_id = ?", (user_id,)) as cursor:
            p_row = await cursor.fetchone()
        balance = p_row[0] if p_row else 0.0

        async with db.execute("SELECT id, item_name, description, price, category, stock FROM market_items WHERE stock > 0") as cursor:
            items = await cursor.fetchall()

    embed = discord.Embed(
        title="[MARKET] // METROPOLIS BLACK MARKET CATALOG",
        description=f"Güncel Bakiyeniz: **€${balance:.2f}**\nAşağıdaki menüden dilediğiniz ekipmanı seçerek anında satın alabilirsiniz.",
        color=discord.Color(0xFFE600)
    )
    if items:
        catalog_desc = "\n".join([f"• **{it[1]}** - €${it[3]:.2f} | Stok: {it[5]} | *{it[2]}*" for it in items[:10]])
        embed.add_field(name="Öne Çıkan Ürünler", value=catalog_desc, inline=False)
        view = MarketView(items)
    else:
        embed.add_field(name="Ürün Kataloğu", value="Şu anda satışta ürün bulunmuyor.", inline=False)
        view = None

    await interaction.followup.send(embed=embed, view=view, ephemeral=False)

@bot.tree.command(name="roll-baslangic", description="Karakter başlangıç statları için zar at.")
async def roll_baslangic(interaction: discord.Interaction):
    user_id = interaction.user.id
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute("SELECT roll_baslangic_done FROM rp_players WHERE user_id = ?", (user_id,)) as cursor:
            row = await cursor.fetchone()
        if not row:
            await interaction.response.send_message("Önce `/kayıt` komutu ile karakter oluşturmalısınız.", ephemeral=False)
            return
        if row[0] == 1:
            await interaction.response.send_message("Başlangıç zarlarını zaten attınız.", ephemeral=False)
            return

        bonus_balance = random.randint(50, 250)
        bonus_sp = random.randint(10, 30)
        
        await db.execute("UPDATE rp_players SET balance = balance + ?, sp_points = sp_points + ?, roll_baslangic_done = 1 WHERE user_id = ?", (bonus_balance, bonus_sp, user_id))
        await db.commit()

    embed = discord.Embed(
        title="[SYSTEM] // INITIAL ROLL MATRIX",
        description=f"Zarlar atıldı!\n\n💰 **Ekstra Bakiye:** €${bonus_balance}\n⚡ **Ekstra SP:** +{bonus_sp}",
        color=discord.Color(0xFFE600)
    )
    await interaction.response.send_message(embed=embed, ephemeral=False)

@bot.tree.command(name="roll-tolerans", description="Sinirsel tolerans zarı at.")
async def roll_tolerans(interaction: discord.Interaction):
    user_id = interaction.user.id
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute("SELECT max_tolerance, roll_tolerans_done, has_used_tolerance FROM rp_players WHERE user_id = ?", (user_id,)) as cursor:
            row = await cursor.fetchone()
        if not row:
            await interaction.response.send_message("Önce `/kayıt` komutu ile karakter oluşturmalısınız.", ephemeral=False)
            return
        if row[2] == 1 or row[1] == 1:
            await interaction.response.send_message("Bu komutu karakteriniz için yalnızca bir kez kullanabilirsiniz.", ephemeral=False)
            return
        
        tol_roll = random.randint(10, 40)
        new_max = row[0] + tol_roll

        await db.execute("UPDATE rp_players SET max_tolerance = ?, roll_tolerans_done = 1, has_used_tolerance = 1 WHERE user_id = ?", (new_max, user_id))
        await db.commit()

    embed = discord.Embed(
        title="[SYSTEM] // NEURAL TOLERANCE EXPANSION",
        description=f"Sinirsel kapasite testi tamamlandı!\n\n🧠 **Yeni Max Tolerans:** %{new_max}",
        color=discord.Color(0xFF0055)
    )
    await interaction.response.send_message(embed=embed, ephemeral=False)

@bot.tree.command(name="death_gambling", description="Tüm varlığınızı riske atacağınız yüksek riskli ölüm kumarı.")
async def death_gambling(interaction: discord.Interaction):
    user_id = interaction.user.id
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute("SELECT balance, character_name FROM rp_players WHERE user_id = ?", (user_id,)) as cursor:
            row = await cursor.fetchone()
        if not row:
            await interaction.response.send_message("Önce `/kayıt` komutu ile karakter oluşturmalısınız.", ephemeral=True)
            return
        if (row[0] or 0.0) <= 0:
            await interaction.response.send_message("Kumar oynamak için nakit bakiyeniz bulunmuyor (€$0).", ephemeral=True)
            return

    embed = discord.Embed(
        title="╔════════════════════════════════════════╗\n║          ☠️ ÖLÜM KUMARI (HIGH STAKES)  ║\n╚════════════════════════════════════════╝",
        description=f"Operative **{row[1]}**, ölüm kumarı masasına oturdunuz.\n\n• **Kazanma (%10 Şans):** Tüm servetiniz %190 oranında katlanır.\n• **Kaybetme (%90 Şans):** Her şeyiniz (%50 bakiye, envanter, stat, tolerans) yarı yarıya düşer ve **☠️ Beceriksiz Kumarbaz Damgası** alırsınız.\n\nDevam etmek için aşağıdaki uyarı butonuna tıklayın.",
        color=discord.Color(0xFF0055)
    )
    await interaction.response.send_message(embed=embed, view=DeathGambleStage1View(user_id), ephemeral=False)

@bot.tree.command(name="hack", description="Ana terminale bağlanarak matrix kod çözme mini oyunu.")
async def hack(interaction: discord.Interaction):
    user_id = interaction.user.id
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute("SELECT character_name FROM rp_players WHERE user_id = ?", (user_id,)) as cursor:
            row = await cursor.fetchone()
        if not row:
            await interaction.response.send_message("Önce `/kayıt` komutu ile karakter oluşturmalısınız.", ephemeral=True)
            return

    embed = discord.Embed(
        title="[MATRIX HACKING] // ZORLUK SEÇİMİ",
        description="Terminal bağlantısı kuruldu. Lütfen hack zorluk derecesini seçin:",
        color=discord.Color(0x00F0FF)
    )
    await interaction.response.send_message(embed=embed, view=HackDifficultyView(user_id), ephemeral=False)

@bot.tree.command(name="karakter-lore-ekle", description="Karaktere lore geçmişi ekle (Admin).")
@authorized_only()
@app_commands.describe(member="Hedef karakter", sub_title="Hikaye Başlığı", content="Hikaye içeriği")
async def karakter_lore_ekle(interaction: discord.Interaction, member: discord.Member, sub_title: str, content: str):
    timestamp = datetime.datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute(
            "INSERT INTO character_lore (user_id, sub_title, content, added_by, timestamp) VALUES (?, ?, ?, ?, ?)",
            (member.id, sub_title, content, str(interaction.user), timestamp)
        )
        await db.commit()
    await interaction.response.send_message(f"{member.mention} için lore kaydı başarıyla eklendi.", ephemeral=True)

@bot.tree.command(name="sp-yonet", description="Kullanıcıya SP puanı ekle veya çıkar (Admin).")
@authorized_only()
@app_commands.describe(member="Hedef kullanıcı", amount="Eklenecek/Çıkarılacak Miktar")
async def sp_yonet(interaction: discord.Interaction, member: discord.Member, amount: float):
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("UPDATE rp_players SET sp_points = sp_points + ? WHERE user_id = ?", (amount, member.id))
        await db.commit()
    await interaction.response.send_message(f"{member.mention} için SP puanı güncellendi ({amount:+g}).", ephemeral=True)

@bot.tree.command(name="implant-ekle", description="Karaktere implant taktır (Admin).")
@authorized_only()
@app_commands.describe(member="Hedef kullanıcı", slot_region="Bölge (Örn: Ocular System)", implant_name="İmplant Adı", tolerance_cost="Tolerans Maliyeti")
async def implant_ekle(interaction: discord.Interaction, member: discord.Member, slot_region: str, implant_name: str, tolerance_cost: float):
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute(
            "INSERT INTO rp_implants (user_id, slot_region, implant_name, tolerance_cost) VALUES (?, ?, ?, ?)",
            (member.id, slot_region, implant_name, tolerance_cost)
        )
        await db.execute("UPDATE rp_players SET current_tolerance = current_tolerance + ? WHERE user_id = ?", (tolerance_cost, member.id))
        await db.commit()
    await interaction.response.send_message(f"{member.mention} kullanıcısına **{implant_name}** implantı takıldı.", ephemeral=True)

@bot.tree.command(name="ekonomi-yonet", description="Karakter bütçesini yönet (Admin).")
@authorized_only()
@app_commands.describe(member="Hedef kullanıcı", amount="Eklenecek/Çıkarılacak €$ Miktarı")
async def ekonomi_yonet(interaction: discord.Interaction, member: discord.Member, amount: float):
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("UPDATE rp_players SET balance = balance + ? WHERE user_id = ?", (amount, member.id))
        await db.commit()
    await interaction.response.send_message(f"{member.mention} bakiyesi güncellendi (€${amount:+,.2f}).", ephemeral=True)

@bot.tree.command(name="yetkili-rol-ekle", description="Yönetim paneli için yetkili rol ekle (Owner).")
@hardcore_owner_only()
@app_commands.describe(role="Yetkili verilecek Discord rolü")
async def yetkili_rol_ekle(interaction: discord.Interaction, role: discord.Role):
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("INSERT OR IGNORE INTO authorized_roles (role_id) VALUES (?)", (role.id,))
        await db.commit()
    await interaction.response.send_message(f"@{role.name} yetkili rollere eklendi.", ephemeral=True)

@bot.tree.command(name="yetkili-rol-cikar", description="Yetkili rolü kaldır (Owner).")
@hardcore_owner_only()
@app_commands.describe(role="Kaldırılacak Discord rolü")
async def yetkili_rol_cikar(interaction: discord.Interaction, role: discord.Role):
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("DELETE FROM authorized_roles WHERE role_id = ?", (role.id,))
        await db.commit()
    await interaction.response.send_message(f"@{role.name} yetkili rollerden çıkarıldı.", ephemeral=True)


# --- FASTAPI WEB ADMIN PANEL & UI/UX ---
app = FastAPI(title="Cyberpunk Text RP Admin Panel")

limiter = Limiter(key_func=get_remote_address)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

@app.middleware("http")
async def shutdown_middleware(request: Request, call_next):
    if GLOBAL_SHUTDOWN:
        return HTMLResponse("UYARI SHUTDOWN by devpact", status_code=503)
    return await call_next(request)
app.state.bot = bot
app.state.guild_id = GUILD_ID
app.state.redis = redis_client
app.include_router(router)

app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")

@app.get("/", response_class=RedirectResponse)
async def root():
    return RedirectResponse(url="/admin/login", status_code=status.HTTP_303_SEE_OTHER)

ACTIVE_SESSIONS = set()

def check_auth(request: Request):
    token = request.cookies.get("session_token")
    if not token:
        raise HTTPException(status_code=status.HTTP_303_SEE_OTHER, headers={"Location": "/admin/login"})
    try:
        if token in ACTIVE_SESSIONS:
            return True
        jwt.decode(token, JWT_SECRET_KEY, algorithms=["HS256"])
        return True
    except Exception:
        raise HTTPException(status_code=status.HTTP_303_SEE_OTHER, headers={"Location": "/admin/login"})

@app.get("/admin/auth/discord")
async def discord_login():
    discord_auth_url = f"https://discord.com/api/oauth2/authorize?client_id={DISCORD_CLIENT_ID}&redirect_uri={__import__('urllib.parse').parse.quote(DISCORD_REDIRECT_URI)}&response_type=code&scope=identify%20guilds"
    return RedirectResponse(url=discord_auth_url, status_code=status.HTTP_303_SEE_OTHER)

@app.get("/admin/auth/callback")
async def discord_callback(code: str):
    token_url = "https://discord.com/api/oauth2/token"
    data = {
        "client_id": DISCORD_CLIENT_ID,
        "client_secret": DISCORD_CLIENT_SECRET,
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": DISCORD_REDIRECT_URI,
    }
    headers = {"Content-Type": "application/x-www-form-urlencoded"}
    async with httpx.AsyncClient() as client:
        resp = await client.post(token_url, data=data, headers=headers)
        if resp.status_code != 200:
            raise HTTPException(status_code=400, detail="Discord OAuth2 token exchange failed.")
        token_data = resp.json()
        access_token = token_data.get("access_token")

        user_resp = await client.get("https://discord.com/api/users/@me", headers={"Authorization": f"Bearer {access_token}"})
        if user_resp.status_code != 200:
            raise HTTPException(status_code=400, detail="Failed to fetch Discord user info.")
        user_info = user_resp.json()
        discord_user_id = int(user_info.get("id"))

    guild = bot.get_guild(GUILD_ID)
    member = None
    if guild:
        member = guild.get_member(discord_user_id)
        if not member:
            try:
                member = await guild.fetch_member(discord_user_id)
            except Exception:
                pass

    is_auth = False
    if member:
        is_auth = await is_user_authorized(member)
    elif discord_user_id == HARDCORE_OWNER_ID or discord_user_id in DEFAULT_OWNERS or discord_user_id in DEFAULT_ADMINS:
        is_auth = True

    if not is_auth:
        raise HTTPException(status_code=403, detail="Access Denied: You do not have administrative roles in the Discord guild.")

    payload = {"sub": str(discord_user_id), "username": user_info.get("username"), "exp": datetime.datetime.utcnow() + datetime.timedelta(days=1)}
    jwt_token = jwt.encode(payload, JWT_SECRET_KEY, algorithm="HS256")

    response = RedirectResponse(url="/admin/dashboard", status_code=status.HTTP_303_SEE_OTHER)
    response.set_cookie(key="session_token", value=jwt_token, httponly=True, secure=False)
    return response

@app.websocket("/ws/logs")
async def websocket_logs(websocket: WebSocket):
    if GLOBAL_SHUTDOWN:
        await websocket.accept()
        await websocket.send_text("UYARI SHUTDOWN by devpact")
        await websocket.close()
        return
    await manager.connect(websocket)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        manager.disconnect(websocket)

@app.get("/admin/login", response_class=HTMLResponse)
async def login_get(request: Request):
    return templates.TemplateResponse(request=request, name="login.html", context={"error": None})

@app.post("/admin/login", response_class=HTMLResponse)
@limiter.limit("10/minute")
async def login_post(request: Request, username: str = Form(...), password: str = Form(...)):
    pwd_hash = hashlib.sha256(password.encode()).hexdigest()
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute("SELECT id FROM admin_users WHERE username = ? AND password_hash = ?", (username, pwd_hash)) as cursor:
            user = await cursor.fetchone()

    if user:
        payload = {"sub": username, "exp": datetime.datetime.utcnow() + datetime.timedelta(days=1)}
        jwt_token = jwt.encode(payload, JWT_SECRET_KEY, algorithm="HS256")
        response = RedirectResponse(url="/admin/dashboard", status_code=status.HTTP_303_SEE_OTHER)
        response.set_cookie(key="session_token", value=jwt_token, httponly=True)
        return response
    
    return templates.TemplateResponse(request=request, name="login.html", context={"error": "Access Denied: Invalid operator credentials!"}, status_code=400)

@app.get("/admin/logout")
async def logout(request: Request):
    response = RedirectResponse(url="/admin/login", status_code=status.HTTP_303_SEE_OTHER)
    response.delete_cookie(key="session_token")
    return response

@app.get("/admin/dashboard", response_class=HTMLResponse)
async def dashboard(request: Request, search: str = None, class_filter: str = None, implant_filter: str = None, page: int = 1, limit: int = 20):
    check_auth(request)
    
    async with aiosqlite.connect(DB_NAME) as db:
        query = "SELECT user_id, character_name, class_name, balance, salary, living_cost, popularity, max_tolerance, current_tolerance, sp_points, has_gambler_mark FROM rp_players WHERE 1=1"
        params = []
        if search:
            query += " AND (character_name LIKE ? OR user_id LIKE ?)"
            params.extend([f"%{search}%", f"%{search}%"])
        if class_filter:
            query += " AND class_name = ?"
            params.append(class_filter)
            
        async with db.execute(query, params) as cursor:
            rows = await cursor.fetchall()
            
        all_players = []
        for row in rows:
            u_id = row[0]
            imp_query = "SELECT slot_region, implant_name, tolerance_cost FROM rp_implants WHERE user_id = ?"
            imp_params = [u_id]
            if implant_filter:
                imp_query += " AND implant_name LIKE ?"
                imp_params.append(f"%{implant_filter}%")
                
            async with db.execute(imp_query, imp_params) as imp_cursor:
                implants = await imp_cursor.fetchall()
                
            if implant_filter and not implants:
                continue
                
            all_players.append({
                "user_id": row[0],
                "character_name": row[1],
                "class_name": row[2],
                "balance": row[3],
                "salary": row[4],
                "living_cost": row[5],
                "popularity": row[6],
                "max_tolerance": row[7],
                "current_tolerance": row[8],
                "sp_points": row[9],
                "has_gambler_mark": row[10],
                "implants": implants
            })

    total_count = len(all_players)
    total_pages = max(1, (total_count + limit - 1) // limit)
    page = max(1, min(page, total_pages))
    start_idx = (page - 1) * limit
    end_idx = start_idx + limit
    players = all_players[start_idx:end_idx]

    return templates.TemplateResponse(request=request, name="dashboard.html", context={
        "players": players,
        "search": search,
        "class_filter": class_filter,
        "implant_filter": implant_filter,
        "page": page,
        "total_pages": total_pages,
        "total_count": total_count,
        "limit": limit
    })

@app.post("/admin/player/update", response_class=RedirectResponse)
@limiter.limit("20/minute")
async def player_update(request: Request, user_id: int = Form(...), balance_add: float = Form(0.0), sp_add: float = Form(0.0)):
    check_auth(request)
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute("SELECT balance, sp_points FROM rp_players WHERE user_id = ?", (user_id,)) as cursor:
            row = await cursor.fetchone()
        if row:
            new_bal = row[0] + balance_add
            new_sp = (row[1] or 0) + sp_add
            await db.execute("UPDATE rp_players SET balance = ?, sp_points = ? WHERE user_id = ?", (new_bal, new_sp, user_id))
            await db.commit()
            
    return RedirectResponse(url="/admin/dashboard", status_code=status.HTTP_303_SEE_OTHER)

@app.get("/admin/lore", response_class=HTMLResponse)
async def lore_page(request: Request):
    check_auth(request)
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute("SELECT id, user_id, sub_title, content, added_by, timestamp FROM character_lore ORDER BY id DESC") as cursor:
            rows = await cursor.fetchall()
        lores = [{"id": r[0], "user_id": r[1], "sub_title": r[2], "content": r[3], "added_by": r[4], "timestamp": r[5]} for r in rows]
    
    return templates.TemplateResponse(request=request, name="lore.html", context={"lores": lores})

@app.get("/admin/channels", response_class=HTMLResponse)
async def channels_page(request: Request):
    check_auth(request)
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute("SELECT channel_id, channel_name, is_rp_enabled FROM rp_channels") as cursor:
            rows = await cursor.fetchall()
        channels = [{"channel_id": r[0], "channel_name": r[1], "is_rp_enabled": r[2]} for r in rows]
    return templates.TemplateResponse(request=request, name="channels.html", context={"channels": channels})

@app.get("/admin/logs", response_class=HTMLResponse)
async def logs_page(request: Request):
    check_auth(request)
    return templates.TemplateResponse(request=request, name="logs.html", context={})

@app.get("/admin/implants", response_class=HTMLResponse)
async def implants_catalog_page(request: Request):
    check_auth(request)
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute("SELECT id, user_id, slot_region, implant_name, tolerance_cost FROM rp_implants ORDER BY id DESC") as cursor:
            rows = await cursor.fetchall()
        implants = [{"id": r[0], "user_id": r[1], "slot_region": r[2], "implant_name": r[3], "tolerance_cost": r[4]} for r in rows]
    return templates.TemplateResponse(request=request, name="implants.html", context={"implants": implants})

@app.get("/admin/market", response_class=HTMLResponse)
async def admin_market_page(request: Request):
    check_auth(request)
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute("SELECT id, item_name, description, price, category, stock FROM market_items ORDER BY id DESC") as cursor:
            rows = await cursor.fetchall()
        items = [{"id": r[0], "item_name": r[1], "description": r[2], "price": r[3], "category": r[4], "stock": r[5]} for r in rows]
    return templates.TemplateResponse(request=request, name="market.html", context={"items": items})

@app.post("/admin/market/add", response_class=RedirectResponse)
async def admin_market_add(request: Request, item_name: str = Form(...), description: str = Form(...), price: float = Form(...), category: str = Form(...), stock: int = Form(10)):
    check_auth(request)
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute(
            "INSERT INTO market_items (item_name, description, price, category, stock) VALUES (?, ?, ?, ?, ?)",
            (item_name, description, price, category, stock)
        )
        await db.commit()
    return RedirectResponse(url="/admin/market", status_code=status.HTTP_303_SEE_OTHER)

@app.post("/admin/market/edit", response_class=RedirectResponse)
async def admin_market_edit(request: Request, item_id: int = Form(...), price: float = Form(...), stock: int = Form(...)):
    check_auth(request)
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("UPDATE market_items SET price = ?, stock = ? WHERE id = ?", (price, stock, item_id))
        await db.commit()
    return RedirectResponse(url="/admin/market", status_code=status.HTTP_303_SEE_OTHER)

@app.post("/admin/market/delete", response_class=RedirectResponse)
async def admin_market_delete(request: Request, item_id: int = Form(...)):
    check_auth(request)
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("DELETE FROM market_items WHERE id = ?", (item_id,))
        await db.commit()
    return RedirectResponse(url="/admin/market", status_code=status.HTTP_303_SEE_OTHER)

@app.post("/admin/player/tolerance-boost", response_class=RedirectResponse)
async def admin_tolerance_boost(request: Request, user_id: int = Form(...), boost_add: float = Form(...)):
    check_auth(request)
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute("SELECT tolerance_boost FROM rp_players WHERE user_id = ?", (user_id,)) as cursor:
            row = await cursor.fetchone()
        if row:
            current_boost = row[0] or 0.0
            new_boost = current_boost + boost_add
            await db.execute("UPDATE rp_players SET tolerance_boost = ? WHERE user_id = ?", (new_boost, user_id))
            await db.commit()
    return RedirectResponse(url=f"/admin/player/{user_id}", status_code=status.HTTP_303_SEE_OTHER)

@app.post("/admin/player/kill", response_class=RedirectResponse)
@app.post("/api/user/kill", response_class=RedirectResponse)
async def player_kill(request: Request, user_id: int = Form(...)):
    check_auth(request)
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("DELETE FROM rp_players WHERE user_id = ?", (user_id,))
        await db.execute("DELETE FROM rp_implants WHERE user_id = ?", (user_id,))
        await db.execute("DELETE FROM rp_inventory WHERE user_id = ?", (user_id,))
        await db.execute("DELETE FROM rp_stats WHERE user_id = ?", (user_id,))
        await db.execute("DELETE FROM character_lore WHERE user_id = ?", (user_id,))
        await db.execute("DELETE FROM user_last_active WHERE user_id = ?", (user_id,))
        await db.execute("DELETE FROM daily_sp_tracker WHERE user_id = ?", (user_id,))
        await db.commit()
    return RedirectResponse(url="/admin/dashboard", status_code=status.HTTP_303_SEE_OTHER)

@app.get("/admin/player/{user_id}", response_class=HTMLResponse)
async def player_detail_page(request: Request, user_id: int):
    check_auth(request)
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute("SELECT user_id, character_name, class_name, balance, salary, living_cost, popularity, max_tolerance, current_tolerance, roll_baslangic_done, roll_tolerans_done, sp_points, tolerance_boost, has_gambler_mark FROM rp_players WHERE user_id = ?", (user_id,)) as cursor:
            row = await cursor.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Player not found")
        
        async with db.execute("SELECT slot_region, implant_name, tolerance_cost FROM rp_implants WHERE user_id = ?", (user_id,)) as imp_cursor:
            implants = await imp_cursor.fetchall()

        async with db.execute("SELECT body, reflex, technic, intelligence, cool FROM rp_stats WHERE user_id = ?", (user_id,)) as stat_cursor:
            stat_row = await stat_cursor.fetchone()

        stats = stat_row or (10, 10, 10, 10, 10)
        c_name = row[2] or "Solo"
        rank, total_stat, p_val, s_val, c_info = calculate_class_rank(c_name, stats)
        class_skills_dict = CLASS_MAPPING.get(c_name, CLASS_MAPPING["Solo"])["skills"]
        
        ranks_order = ["D", "C", "B", "A", "S"]
        current_rank_idx = ranks_order.index(rank) if rank in ranks_order else 0
        
        processed_skills = []
        for r in ranks_order:
            unlocked = ranks_order.index(r) <= current_rank_idx
            processed_skills.append({
                "rank": r,
                "skills": class_skills_dict.get(r, []),
                "unlocked": unlocked
            })

        popularity = row[6] or 0.0
        street_cred_pct = min(100.0, max(0.0, popularity))

        player = {
            "user_id": row[0],
            "character_name": row[1],
            "class_name": row[2],
            "balance": row[3],
            "salary": row[4],
            "living_cost": row[5],
            "popularity": row[6],
            "max_tolerance": row[7],
            "current_tolerance": row[8],
            "roll_baslangic_done": row[9],
            "roll_tolerans_done": row[10],
            "sp_points": row[11],
            "tolerance_boost": row[12] if len(row) > 12 else 0.0,
            "has_gambler_mark": row[13] if len(row) > 13 else 0,
            "implants": implants,
            "class_rank": rank,
            "total_stat": total_stat,
            "primary_stat_name": c_info["primary"],
            "primary_stat_val": p_val,
            "secondary_stat_name": c_info["secondary"],
            "secondary_stat_val": s_val,
            "processed_skills": processed_skills,
            "street_cred_pct": street_cred_pct
        }
    return templates.TemplateResponse(request=request, name="player_detail.html", context={"player": player})

def get_current_admin(request: Request) -> str:
    token = request.cookies.get("session_token")
    if not token:
        return "Anonymous"
    try:
        payload = jwt.decode(token, JWT_SECRET_KEY, algorithms=["HS256"])
        return payload.get("sub", "Unknown Admin")
    except Exception:
        return "Unknown Admin"

@app.middleware("http")
async def audit_middleware(request: Request, call_next):
    if request.url.path.startswith("/admin/") and request.url.path not in ["/admin/login", "/admin/auth/discord", "/admin/auth/callback"]:
        client_ip = request.client.host if request.client else "Unknown"
        admin_user = get_current_admin(request)
        timestamp = datetime.datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
        action = f"{request.method} {request.url.path}"
        details = f"Query: {request.url.query}"
        
        try:
            async with aiosqlite.connect(DB_NAME) as db:
                await db.execute(
                    "INSERT INTO admin_audit_logs (timestamp, admin_username, ip_address, action, details) VALUES (?, ?, ?, ?, ?)",
                    (timestamp, admin_user, client_ip, action, details)
                )
                await db.commit()
        except Exception:
            pass
            
    response = await call_next(request)
    return response

OWNER_PORT = 8001

owner_app = FastAPI(title="Cyberpunk Owner Panel")
owner_app.mount("/static", StaticFiles(directory="static"), name="static")

@owner_app.get("/", response_class=RedirectResponse)
async def owner_root():
    return RedirectResponse(url="/owner/dashboard", status_code=status.HTTP_303_SEE_OTHER)

@owner_app.get("/owner/dashboard", response_class=HTMLResponse)
async def owner_dashboard(request: Request):
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute("SELECT id, username, role FROM admin_users") as cursor:
            admins = await cursor.fetchall()
        async with db.execute("SELECT timestamp, admin_username, ip_address, action, details FROM admin_audit_logs ORDER BY id DESC LIMIT 200") as cursor:
            logs = await cursor.fetchall()
    return templates.TemplateResponse(request=request, name="owner_dashboard.html", context={"admins": admins, "logs": logs})

@owner_app.post("/owner/admins/add", response_class=RedirectResponse)
async def owner_admins_add(request: Request, username: str = Form(...), password: str = Form(...)):
    pwd_hash = hashlib.sha256(password.encode()).hexdigest()
    async with aiosqlite.connect(DB_NAME) as db:
        try:
            await db.execute("INSERT OR REPLACE INTO admin_users (username, password_hash, role) VALUES (?, ?, 'Admin')", (username, pwd_hash))
            await db.commit()
        except Exception:
            pass
    return RedirectResponse(url="/owner/dashboard", status_code=status.HTTP_303_SEE_OTHER)

@owner_app.post("/owner/admins/delete", response_class=RedirectResponse)
async def owner_admins_delete(request: Request, admin_id: int = Form(...)):
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("DELETE FROM admin_users WHERE id = ?", (admin_id,))
        await db.commit()
    return RedirectResponse(url="/owner/dashboard", status_code=status.HTTP_303_SEE_OTHER)

async def run_web_server():
    config_admin = uvicorn.Config(app, host="0.0.0.0", port=WEB_PORT, log_level="info")
    config_owner = uvicorn.Config(owner_app, host="0.0.0.0", port=OWNER_PORT, log_level="info")
    
    server_admin = uvicorn.Server(config_admin)
    server_owner = uvicorn.Server(config_owner)
    
    await asyncio.gather(
        server_admin.serve(),
        server_owner.serve()
    )

async def main():
    await asyncio.gather(
        bot.start(BOT_TOKEN),
        run_web_server()
    )

if __name__ == "__main__":
    asyncio.run(main())
