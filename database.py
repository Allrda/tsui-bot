# File Location: /database.py
import hashlib

import aiosqlite

DB_NAME = "text_rp_database.db"
_db_connection = None

async def get_db() -> aiosqlite.Connection:
    global _db_connection
    if _db_connection is None:
        _db_connection = await aiosqlite.connect(DB_NAME)
        await _db_connection.execute("PRAGMA journal_mode=WAL;")
        await _db_connection.execute("PRAGMA busy_timeout=5000;")
        await _db_connection.execute("PRAGMA synchronous=NORMAL;")
        await _db_connection.commit()
    return _db_connection

async def close_db():
    global _db_connection
    if _db_connection is not None:
        await _db_connection.close()
        _db_connection = None

async def init_db():
    db = await get_db()
    await db.execute("""
        CREATE TABLE IF NOT EXISTS rp_players (
            user_id INTEGER PRIMARY KEY,
            character_name TEXT,
            class_name TEXT,
            balance REAL DEFAULT 100,
            salary REAL DEFAULT 100,
            living_cost REAL DEFAULT 30,
            popularity REAL DEFAULT 0,
            max_tolerance REAL DEFAULT 0,
            current_tolerance REAL DEFAULT 0,
            roll_baslangic_done INTEGER DEFAULT 0,
            roll_tolerans_done INTEGER DEFAULT 0,
            sp_points REAL DEFAULT 0,
            stats TEXT DEFAULT '{}',
            traits TEXT DEFAULT '{}',
            inventory TEXT DEFAULT '{}',
            lore TEXT,
            image_url TEXT,
            has_gambler_mark INTEGER DEFAULT 0,
            has_used_tolerance INTEGER DEFAULT 0,
            tolerance_boost REAL DEFAULT 0
        )
    """)
    await db.execute("""
        CREATE TABLE IF NOT EXISTS rp_implants (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            slot_region TEXT,
            implant_name TEXT,
            tolerance_cost REAL
        )
    """)
    await db.execute("""
        CREATE TABLE IF NOT EXISTS authorized_roles (
            role_id INTEGER PRIMARY KEY
        )
    """)
    await db.execute("""
        CREATE TABLE IF NOT EXISTS admin_users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE,
            password_hash TEXT,
            role TEXT DEFAULT 'Admin'
        )
    """)
    await db.execute("""
        CREATE TABLE IF NOT EXISTS admin_audit_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT,
            admin_username TEXT,
            ip_address TEXT,
            action TEXT,
            details TEXT
        )
    """)
    admin_hash = hashlib.sha256(b"tsuibot123").hexdigest()
    async with db.execute("SELECT id FROM admin_users WHERE username = 'admin'") as cursor:
        adm = await cursor.fetchone()
    if not adm:
        await db.execute("INSERT INTO admin_users (username, password_hash, role) VALUES ('admin', ?, 'Admin')", (admin_hash,))
    else:
        await db.execute("UPDATE admin_users SET password_hash = ? WHERE username = 'admin'", (admin_hash,))

    await db.execute("""
        CREATE TABLE IF NOT EXISTS character_lore (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            sub_title TEXT,
            content TEXT,
            added_by TEXT,
            timestamp TEXT
        )
    """)
    await db.execute("""
        CREATE TABLE IF NOT EXISTS rp_channels (
            channel_id INTEGER PRIMARY KEY,
            channel_name TEXT,
            is_rp_enabled INTEGER DEFAULT 0
        )
    """)
    await db.execute("""
        CREATE TABLE IF NOT EXISTS daily_sp_tracker (
            user_id INTEGER,
            date_str TEXT,
            message_count INTEGER DEFAULT 0,
            words_written INTEGER DEFAULT 0,
            PRIMARY KEY(user_id, date_str)
        )
    """)
    await db.execute("""
        CREATE TABLE IF NOT EXISTS implant_catalog (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            slot_region TEXT,
            implant_name TEXT,
            tolerance_cost REAL
        )
    """)
    await db.execute("""
        CREATE TABLE IF NOT EXISTS system_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT,
            category TEXT,
            level TEXT,
            message TEXT,
            metadata TEXT
        )
    """)
    await db.execute("""
        CREATE TABLE IF NOT EXISTS activity_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            username TEXT,
            channel_id INTEGER,
            channel_name TEXT,
            hour INTEGER,
            is_rp_channel INTEGER,
            timestamp TEXT
        )
    """)
    await db.execute("""
        CREATE TABLE IF NOT EXISTS user_last_active (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            last_message_date TEXT,
            last_rp_date TEXT
        )
    """)
    await db.execute("""
        CREATE TABLE IF NOT EXISTS market_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            item_name TEXT,
            description TEXT,
            price REAL,
            category TEXT,
            stock INTEGER DEFAULT 10
        )
    """)
    await db.execute("""
        CREATE TABLE IF NOT EXISTS rp_inventory (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            item_name TEXT,
            quantity INTEGER DEFAULT 1,
            details TEXT
        )
    """)
    await db.execute("""
        CREATE TABLE IF NOT EXISTS rp_stats (
            user_id INTEGER PRIMARY KEY,
            strength INTEGER DEFAULT 10,
            reflexes INTEGER DEFAULT 10,
            technical INTEGER DEFAULT 10,
            intelligence INTEGER DEFAULT 10,
            cool INTEGER DEFAULT 10
        )
    """)
    # Performance Indexes
    await db.execute("CREATE INDEX IF NOT EXISTS idx_activity_logs_user ON activity_logs(user_id)")
    await db.execute("CREATE INDEX IF NOT EXISTS idx_activity_logs_channel ON activity_logs(channel_id)")
    await db.execute("CREATE INDEX IF NOT EXISTS idx_activity_logs_hour ON activity_logs(hour)")
    await db.execute("CREATE INDEX IF NOT EXISTS idx_rp_inventory_user ON rp_inventory(user_id)")
    await db.execute("CREATE INDEX IF NOT EXISTS idx_character_lore_user ON character_lore(user_id)")
    await db.execute("CREATE INDEX IF NOT EXISTS idx_rp_implants_user ON rp_implants(user_id)")

    for col_query in [
        "ALTER TABLE rp_players ADD COLUMN tolerance_boost REAL DEFAULT 0",
        "ALTER TABLE rp_players ADD COLUMN has_gambler_mark INTEGER DEFAULT 0",
        "ALTER TABLE rp_players ADD COLUMN has_used_tolerance INTEGER DEFAULT 0",
        "ALTER TABLE rp_players ADD COLUMN image_url TEXT"
    ]:
        try:
            await db.execute(col_query)
        except Exception:
            pass
    await db.commit()
