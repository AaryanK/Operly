import os
import sqlite3
from dotenv import load_dotenv

load_dotenv()

TENANT_ID = "13e2542a-4cfd-428d-b0b2-a81496bc7e03"
email = os.getenv("ADMIN_EMAIL", "admin@operly.local").strip().lower()

db = sqlite3.connect("operly.db")

user = db.execute(
    "SELECT id, email FROM app_users WHERE LOWER(email) = ?",
    (email,),
).fetchone()

if user is None:
    print("Admin user not found:", email)
    print("Available users:")
    for row in db.execute("SELECT id, email FROM app_users"):
        print(row)
    raise SystemExit(1)

user_id = user[0]

result = db.execute(
    "UPDATE tenant_members SET tenant_id = ? WHERE user_id = ?",
    (TENANT_ID, user_id),
)

db.commit()

print("Updated memberships:", result.rowcount)
print("Login now opens ANHITRA for:", email)
