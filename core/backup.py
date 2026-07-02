import shutil
import os
from config import DB_PATH, BACKUP_PATH
import time

def create_backup():
    os.makedirs(BACKUP_PATH, exist_ok=True)
    if os.path.exists(DB_PATH):
        name = f"backup_{int(time.time())}.db"
        shutil.copy(DB_PATH, f"{BACKUP_PATH}/{name}")
        return name
    return None

def restore_latest():
    files = sorted(os.listdir(BACKUP_PATH))
    if not files:
        return None
    latest = files[-1]
    shutil.copy(f"{BACKUP_PATH}/{latest}", DB_PATH)
    return latest
