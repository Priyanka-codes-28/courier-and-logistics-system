"""
Creates one demo account per role, for testing the 4 dashboards.

Run once, from your project root:
    python seed_demo_accounts.py

Safe to re-run — skips any account that already exists.
"""

from app import create_app
from models import User

DEMO_ACCOUNTS = [
    {"name": "Demo Customer",       "email": "demo.customer@courieros.com",  "phone": "9000000001", "password": "Demo@1234", "role": "customer"},
    {"name": "Demo Delivery Agent", "email": "demo.agent@courieros.com",     "phone": "9000000002", "password": "Demo@1234", "role": "delivery_agent"},
    {"name": "Demo Warehouse Staff","email": "demo.warehouse@courieros.com", "phone": "9000000003", "password": "Demo@1234", "role": "warehouse_staff"},
    {"name": "Demo Admin",          "email": "demo.admin@courieros.com",     "phone": "9000000004", "password": "Demo@1234", "role": "admin"},
]

app = create_app()

with app.app_context():
    for acc in DEMO_ACCOUNTS:
        existing = User.find_by_email(acc["email"])
        if existing:
            print(f"Skipped (already exists): {acc['email']}")
            continue

        User.create(
            name=acc["name"],
            email=acc["email"],
            phone=acc["phone"],
            password=acc["password"],
            role=acc["role"],
        )
        print(f"Created: {acc['email']}  (role={acc['role']})")

print()
print("Done. Log in with any of the emails above and password: Demo@1234")