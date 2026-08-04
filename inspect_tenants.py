import sqlite3
from pathlib import Path

database = Path("operly.db").resolve()

print("\nDATABASE:", database)
print("EXISTS:", database.exists())

if not database.exists():
    print("No operly.db found in this folder.")
else:
    connection = sqlite3.connect(database)

    tables = [
        row[0]
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        )
    ]

    print("\nTABLES:")
    for table in tables:
        print(" -", table)

    if "tenants" in tables:
        tenants = connection.execute(
            "SELECT id, name, created_at FROM tenants ORDER BY created_at DESC"
        ).fetchall()

        print("\nTENANTS:", len(tenants))

        for tenant in tenants:
            print(tenant)
    else:
        print("\nThe tenants table does not exist.")

input("\nPress Enter to close...")
