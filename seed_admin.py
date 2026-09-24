"""
Run once after configuring your Firebase credentials:

    python seed_admin.py

Creates the initial administrator account:
    email:    admin@presyowatch.local
    password: Admin@123        (change this after first login!)
"""
from models import User

EMAIL = "admin@presyowatch.local"

if User.first(email=EMAIL):
    print("Admin account already exists.")
else:
    admin = User(
        full_name="System Administrator",
        email=EMAIL,
        role="admin",
        subscription_status="active",
        is_active_flag=True,
    )
    admin.set_password("Admin@123")
    admin.save()
    print(f"Admin account created: {EMAIL} / Admin@123")
