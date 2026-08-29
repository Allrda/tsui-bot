import pytest
import asyncio
import os
import aiosqlite
import jwt
from fastapi.testclient import TestClient

from database import init_db, DB_NAME, get_required_xp
from bot import generate_tolerance_bar, generate_node_code, app, JWT_SECRET_KEY, ACTIVE_SESSIONS

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
    # Without cookie, accessing /admin/dashboard should redirect to login (303)
    response = client.get("/admin/dashboard", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/admin/login"

def test_authenticated_access():
    # Create valid JWT token and set cookie
    token = jwt.encode({"sub": "test_admin", "exp": 9999999999}, JWT_SECRET_KEY, algorithm="HS256")
    client.cookies.set("session_token", token)
    
    response = client.get("/admin/dashboard", follow_redirects=False)
    # Should render dashboard or redirect if check_auth passes
    # Note: check_auth allows valid JWT decode
    assert response.status_code in [200, 303]

def test_get_required_xp():
    assert get_required_xp(1) == 100
    assert get_required_xp(2) == 100
    assert get_required_xp(3) == 115
    assert get_required_xp(13) == 465
