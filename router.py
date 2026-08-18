# Dosya Konumu: /router.py
from fastapi import APIRouter, Request, Form, HTTPException, status
from fastapi.responses import JSONResponse
import aiosqlite
import discord
from database import DB_NAME

router = APIRouter(prefix="/admin")

@router.get("/api/heatmap")
async def api_heatmap(request: Request):
    """Returns 24-hour activity distribution and channel stats for Heatmap & Chart.js with Redis caching"""
    redis = getattr(request.app.state, "redis", None)
    if redis:
        try:
            cached = await redis.get("heatmap_cache")
            if cached:
                return JSONResponse(json.loads(cached))
        except Exception:
            pass

    async with aiosqlite.connect(DB_NAME) as db:
        hourly_data = {str(h): 0 for h in range(24)}
        async with db.execute("SELECT hour, COUNT(*) FROM activity_logs GROUP BY hour") as cursor:
            rows = await cursor.fetchall()
            for r in rows:
                if r[0] is not None:
                    hourly_data[str(r[0])] = r[1]

        channels_data = []
        async with db.execute("SELECT channel_name, COUNT(*) as cnt FROM activity_logs GROUP BY channel_id ORDER BY cnt DESC LIMIT 10") as cursor:
            c_rows = await cursor.fetchall()
            for cr in c_rows:
                channels_data.append({"channel_name": cr[0], "count": cr[1]})

        async with db.execute("SELECT is_rp_channel, COUNT(*) FROM activity_logs GROUP BY is_rp_channel") as cursor:
            rp_rows = await cursor.fetchall()
            rp_counts = {str(r[0]): r[1] for r in rp_rows}

    data = {
        "hourly": hourly_data,
        "channels": channels_data,
        "rp_breakdown": rp_counts
    }

    if redis:
        try:
            import json
            await redis.setex("heatmap_cache", 30, json.dumps(data))
        except Exception:
            pass

    return JSONResponse(data)

@router.post("/broadcast/send")
async def broadcast_send(
    request: Request,
    target_type: str = Form(...),  # 'all', 'role', 'user'
    target_id: str = Form(None),   # role_id or user_id if applicable
    title: str = Form(...),
    content: str = Form(...)
):
    """Web panel broadcast DM sending center"""
    bot = request.app.state.bot
    guild_id = request.app.state.guild_id

    guild = bot.get_guild(guild_id)
    if not guild:
        raise HTTPException(status_code=400, detail="Discord guild not found.")

    embed = discord.Embed(
        title=f"[BROADCAST] // {title.upper()}",
        description=f"```prolog\n{content}\n```",
        color=discord.Color(0x00F0FF),
        timestamp=discord.utils.utcnow()
    )
    embed.set_footer(text="NETRUNNER ADMIN BROADCAST CENTER")

    success_count = 0
    failure_count = 0
    failed_users = []

    targets = []
    if target_type == "all":
        targets = guild.members
    elif target_type == "role" and target_id:
        role = guild.get_role(int(target_id))
        if role:
            targets = role.members
    elif target_type == "user" and target_id:
        member = guild.get_member(int(target_id))
        if member:
            targets = [member]

    for member in targets:
        if member.bot:
            continue
        try:
            await member.send(embed=embed)
            success_count += 1
        except Exception:
            failure_count += 1
            failed_users.append({"id": str(member.id), "username": str(member)})

    return JSONResponse({
        "status": "success",
        "success_count": success_count,
        "failure_count": failure_count,
        "total_attempted": success_count + failure_count,
        "failed_users": failed_users
    })
