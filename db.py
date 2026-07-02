import sqlite3
from config import DB_PATH

def get_conn():
    return sqlite3.connect(DB_PATH)
