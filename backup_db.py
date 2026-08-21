# Dosya Konumu: /backup_db.py
import datetime
import os
import shutil

DB_PATH = "text_rp_database.db"
BACKUP_DIR = "backups"

def backup_database():
    if not os.path.exists(DB_PATH):
        print(f"Database {DB_PATH} not found.")
        return
    
    if not os.path.exists(BACKUP_DIR):
        os.makedirs(BACKUP_DIR)
        
    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_filename = os.path.join(BACKUP_DIR, f"text_rp_database_backup_{timestamp}.db")
    
    shutil.copy2(DB_PATH, backup_filename)
    print(f"Database successfully backed up to {backup_filename}")
    
    # Keep only last 30 backups
    backups = sorted([os.path.join(BACKUP_DIR, f) for f in os.listdir(BACKUP_DIR)])
    if len(backups) > 30:
        for old_backup in backups[:-30]:
            try:
                os.remove(old_backup)
                print(f"Removed old backup: {old_backup}")
            except Exception:
                pass

if __name__ == "__main__":
    backup_database()
