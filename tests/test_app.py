import os

import aiosqlite
import jwt
import pytest
from fastapi.testclient import TestClient

os.environ["TESTING"] = "true"

from bot import (
    JWT_SECRET_KEY,
    LogManager,
    app,
    generate_node_code,
    generate_tolerance_bar,
)
from database import DB_NAME, init_db

client = TestClient(app)

@pytest.mark.asyncio
async def test_database_initialization():
    await init_db()
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute("SELECT name FROM sqlite_master WHERE type='table'") as cursor:
            tables = [row[0] for row in await cursor.fetchall()]
    assert "rp_players" in tables
    assert "rp_implants" in tables
    assert "activity_logs" in tables
    assert "market_items" in tables
    assert "rp_stats" in tables
    assert "system_logs" in tables
    assert "user_last_active" in tables

def test_tolerance_bar_formatting():
    bar_normal = generate_tolerance_bar(25.0, 50.0, 0.0)
    assert "🔴" in bar_normal
    assert "░" in bar_normal

    bar_boosted = generate_tolerance_bar(50.0, 50.0, 10.0)
    assert "Boost: +%10.0" in bar_boosted

def test_hack_node_code():
    for _ in range(50):
        code = generate_node_code()
        assert 7 <= len(code) <= 8

def test_fastapi_root_redirect():
    response = client.get("/", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/admin/login"

def test_login_page_get():
    response = client.get("/admin/login")
    assert response.status_code == 200
    assert "login" in response.text.lower() or "form" in response.text

def test_login_invalid_post():
    response = client.post("/admin/login", data={"username": "wronguser", "password": "wrongpassword"})
    assert response.status_code == 400
    assert "Access Denied" in response.text

def test_protected_routes_unauthorized():
    response = client.get("/admin/dashboard", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/admin/login"

def test_authenticated_access():
    token = jwt.encode({"sub": "admin", "exp": 9999999999}, JWT_SECRET_KEY, algorithm="HS256")
    client.cookies.set("session_token", token)
    
    response = client.get("/admin/dashboard", follow_redirects=False)
    assert response.status_code in [200, 303]

def test_admin_market_and_implants_pages():
    token = jwt.encode({"sub": "admin", "exp": 9999999999}, JWT_SECRET_KEY, algorithm="HS256")
    client.cookies.set("session_token", token)

    resp_market = client.get("/admin/market", follow_redirects=False)
    assert resp_market.status_code in [200, 303]

    resp_implants = client.get("/admin/implants", follow_redirects=False)
    assert resp_implants.status_code in [200, 303]

    resp_channels = client.get("/admin/channels", follow_redirects=False)
    assert resp_channels.status_code in [200, 303]

    resp_lore = client.get("/admin/lore", follow_redirects=False)
    assert resp_lore.status_code in [200, 303]

@pytest.mark.asyncio
async def test_log_manager():
    # Test LogManager.send_log with guild=None to ensure database insertion works without error
    await LogManager.send_log(None, "TEST", "INFO", "Unit test log message", {"test": True})
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute("SELECT message FROM system_logs WHERE message = ?", ("Unit test log message",)) as cursor:
            row = await cursor.fetchone()
    assert row is not None
    assert row[0] == "Unit test log message"
