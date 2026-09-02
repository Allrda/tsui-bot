# Dosya Konumu: /database.py
import aiosqlite
import datetime
import hashlib

DB_NAME = "text_rp_database.db"

def get_required_xp(level: int) -> int:
    if level <= 1:
        return 100
    return int(round(100 * (1.15 ** (level - 2))))

async def init_db():
    async with aiosqlite.connect(DB_NAME) as db:
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
                sp_points INTEGER DEFAULT 9,
                stats TEXT DEFAULT '{}',
                traits TEXT DEFAULT '{}',
                inventory TEXT DEFAULT '{}',
                lore TEXT,
                image_url TEXT,
                has_gambler_mark INTEGER DEFAULT 0,
                has_used_tolerance INTEGER DEFAULT 0,
                level INTEGER DEFAULT 1,
                xp REAL DEFAULT 0,
                daily_rp_xp REAL DEFAULT 0,
                last_xp_date TEXT
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
        admin_hash = hashlib.sha256("tsuibot123".encode()).hexdigest()
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
        # 1. Activity Logs for Heatmap
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
        # 2. User Last Active Tracker for Inactivity
        await db.execute("""
            CREATE TABLE IF NOT EXISTS user_last_active (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                last_message_date TEXT,
                last_rp_date TEXT
            )
        """)
        # 3. Market Items Catalog
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
        # 4. User Inventory
        await db.execute("""
            CREATE TABLE IF NOT EXISTS rp_inventory (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                item_name TEXT,
                quantity INTEGER DEFAULT 1,
                details TEXT
            )
        """)
        # 5. User Stats (Body, Reflex, Technic, Intelligence, Cool)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS rp_stats (
                user_id INTEGER PRIMARY KEY,
                body INTEGER DEFAULT 10,
                reflex INTEGER DEFAULT 10,
                technic INTEGER DEFAULT 10,
                intelligence INTEGER DEFAULT 10,
                cool INTEGER DEFAULT 10
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS bot_meta (
                key TEXT PRIMARY KEY,
                value TEXT
            )
        """)
        # Performance Indexes
        await db.execute("CREATE INDEX IF NOT EXISTS idx_activity_logs_user ON activity_logs(user_id)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_activity_logs_channel ON activity_logs(channel_id)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_activity_logs_hour ON activity_logs(hour)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_rp_inventory_user ON rp_inventory(user_id)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_character_lore_user ON character_lore(user_id)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_rp_implants_user ON rp_implants(user_id)")
        for col in ["body", "reflex", "technic", "intelligence", "cool"]:
            try:
                await db.execute(f"ALTER TABLE rp_stats ADD COLUMN {col} INTEGER DEFAULT 10")
            except Exception:
                pass
        try:
            await db.execute("ALTER TABLE rp_players ADD COLUMN tolerance_boost REAL DEFAULT 0")
        except Exception:
            pass
        try:
            await db.execute("ALTER TABLE rp_players ADD COLUMN has_gambler_mark INTEGER DEFAULT 0")
        except Exception:
            pass
        try:
            await db.execute("ALTER TABLE rp_players ADD COLUMN has_used_tolerance INTEGER DEFAULT 0")
        except Exception:
            pass
        try:
            await db.execute("ALTER TABLE rp_players ADD COLUMN level INTEGER DEFAULT 1")
        except Exception:
            pass
        try:
            await db.execute("ALTER TABLE rp_players ADD COLUMN xp REAL DEFAULT 0")
        except Exception:
            pass
        try:
            await db.execute("ALTER TABLE rp_players ADD COLUMN daily_rp_xp REAL DEFAULT 0")
        except Exception:
            pass
        try:
            await db.execute("ALTER TABLE rp_players ADD COLUMN last_xp_date TEXT")
        except Exception:
            pass
        await db.commit()
