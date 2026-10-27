import os
import sys
import sqlite3
from getpass import getpass
from werkzeug.security import generate_password_hash

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "users.db")

username = input("Enter username: ").strip()
if username == "":
    sys.exit("Error: Username cannot be empty. Please try again.")
password = getpass("Enter password: ")
if len(password) < 8:
    sys.exit("Error: Password must be at least 8 characters long. Please try again.")
confirmed_password = getpass("Confirm password: ")

if password != confirmed_password:
    sys.exit("Error: Passwords do not match. Please try again.")
else:
    print("Passwords match. Proceeding with user creation...")



conn = sqlite3.connect(DB_PATH)
conn.execute("""CREATE TABLE IF NOT EXISTS
users (id INTEGER PRIMARY KEY AUTOINCREMENT,
username TEXT NOT NULL UNIQUE,
password_hash TEXT NOT NULL,
is_admin INTEGER NOT NULL DEFAULT 0)
""")

try:
    conn.execute("""INSERT INTO 
users (username, password_hash, is_admin) 
VALUES (?, ?, ?)""", (username, generate_password_hash(password), 1))

except sqlite3.IntegrityError:
    sys.exit(f"Error: Username '{username}' already exists. Please try again with a different username.")

conn.commit()
conn.close()
print(f"Admin user '{username}' created successfully.")