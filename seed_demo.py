"""
Optional demo data so every screen has something to show. Run after seed_data.py and seed_admin.py:

    python seed_demo.py

Demo logins (password for all: demo1234):
    resident:  shin@presyowatch.demo   (log in as "Resident/Consumer")
    vendor:    kenje@presyowatch.demo  (log in as "Market Vendor")
    LGU staff: liza@presyowatch.demo
"""
import random
from datetime import date, datetime, timedelta

from models import Listing, Market, PriceAlertRule, PriceReport, Product, Review, User
from utils.spike_detection import create_report

PASSWORD = "demo1234"

if User.first(email="shin@presyowatch.demo"):
    print("Demo data already exists.")
    raise SystemExit

markets = {m.market_name: m for m in Market.all()}
products = {p.product_name: p for p in Product.all()}
if not markets or not products:
    raise SystemExit("Run seed_data.py first.")
carbon = markets["Carbon Market"]
lapu = markets["Lapu-Lapu City Public Market"]


def make_user(**kw):
    user = User(**kw)
    user.set_password(PASSWORD)
    return user.save()


resident = make_user(full_name="Shin Amaroto", email="shin@presyowatch.demo", city="Cebu City",
                     address="Lahug", contact_number="+63 917 123 4567", subscription_status="active")
lgu = make_user(full_name="Engr. Liza Cruz", email="liza@presyowatch.demo", role="lgu_staff", city="Cebu City",
                org_name="Municipality of Cebu City", division="Price Monitoring Division",
                contact_person="Engr. Liza Cruz, Division Head", contact_number="+63 32 255 1234",
                address="Cebu City, Cebu", subscription_status="active")

now = datetime.utcnow()
vendors = [
    make_user(full_name="Kenje Rise Herrera", email="kenje@presyowatch.demo", role="vendor", city="Cebu City",
              business_name="Herrera Quality Meats", business_address="Stall 20-B", market_id=carbon.market_id,
              contact_number="+63 918 765 4321", is_verified_vendor=True, vendor_verified_at=now - timedelta(days=3)),
    make_user(full_name="Gian Licardo", email="gian@presyowatch.demo", role="vendor", city="Cebu City",
              business_name="Licardo's Fresh Produce", business_address="Stall 24-B", market_id=carbon.market_id,
              is_verified_vendor=True, vendor_verified_at=now - timedelta(days=5)),
    make_user(full_name="Ning Cruz", email="ning@presyowatch.demo", role="vendor", city="Lapu-Lapu City",
              business_name="Ningning Rice Trader", market_id=lapu.market_id, vendor_status="pending"),
    make_user(full_name="Jude Enniong", email="jude@presyowatch.demo", role="vendor", city="Lapu-Lapu City",
              business_name="Emmanuel Vegetables", market_id=lapu.market_id, vendor_status="suspended",
              is_active_flag=False),
]
herrera, licardo = vendors[0], vendors[1]

for vendor, items in {
    herrera: [("Pork Liempo", "Meat", 340, "Pork (Kasim)", "in_stock"), ("Chicken (Whole)", "Poultry", 195, "Chicken (Whole)", "in_stock"),
              ("Beef Brisket", "Meat", 385, "Beef (Brisket)", "low_stock")],
    licardo: [("White Rice (NSIC Rc 222)", "Grains", 52, "Rice (Well-Milled)", "in_stock"), ("Tomato", "Vegetables", 78, "Tomato", "in_stock"),
              ("Galunggong", "Fish", 165, "Galunggong", "in_stock"), ("Garlic", "Vegetables", 120, "Garlic", "out_of_stock")],
}.items():
    for name, category, price, product, stock in items:
        Listing(vendor_id=vendor.user_id, name=name, category=category, price=price, prev_price=price + random.choice([-3, 3]),
                stock_status=stock, product_id=products[product].product_id).save()

for vendor, rating, text in [
    (herrera, 5, "Ang bait ng vendor! Tama ang presyo, sariwa ang isda. Babalik talaga ako!"),
    (herrera, 4, "Good quality of meat. A bit pricey pero worth it."),
    (licardo, 5, "Best bangus in Carbon! Always fresh and fairly priced."),
]:
    Review(vendor_id=vendor.user_id, user_id=resident.user_id, user_name="Frank Ocean", rating=rating, comment=text).save()

srp = {"Rice (Well-Milled)": 48, "Egg (Medium)": 7.5, "Chicken (Whole)": 190, "Pork (Kasim)": 320, "Beef (Brisket)": 380,
       "Tilapia": 140, "Galunggong": 160, "Tomato": 80, "Onion (Red)": 90, "Garlic": 110}
random.seed(7)
for days_ago in range(0, 8):
    for name, base in srp.items():
        PriceReport(
            user_id=resident.user_id, product_id=products[name].product_id, market_id=random.choice([carbon, lapu]).market_id,
            price=round(base * random.uniform(0.95, 1.08), 2), report_date=date.today() - timedelta(days=days_ago),
            status="verified", verified_by=lgu.user_id, verified_at=now - timedelta(days=days_ago, hours=1),
            created_at=now - timedelta(days=days_ago, hours=2), vendor_name=random.choice(["Aling Nena", "Mang Ben", None]),
        ).save()

create_report(resident.user_id, products["Pork (Kasim)"].product_id, carbon.market_id, 420, date.today(), vendor_name="Stall 12")
create_report(resident.user_id, products["Galunggong"].product_id, carbon.market_id, 195, date.today())
create_report(resident.user_id, products["Egg (Medium)"].product_id, lapu.market_id, 7.6, date.today())
PriceAlertRule(user_id=resident.user_id, product_id=products["Rice (Well-Milled)"].product_id, direction="below", threshold=50).save()
PriceAlertRule(user_id=resident.user_id, product_id=products["Pork (Kasim)"].product_id, direction="above", threshold=400).save()

print("Demo data created. Logins (password demo1234): shin@ / kenje@ / liza@presyowatch.demo")
