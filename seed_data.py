"""
Run once to load the starter markets, commodities and DTI SRP baseline into Firestore:

    python seed_data.py
"""
from datetime import date

from models import Market, Product, DtiSrp

MARKETS = [
    ("Carbon Market", "Cebu City", 10.2951, 123.8969),
    ("Lapu-Lapu City Public Market", "Lapu-Lapu City", 10.3103, 123.9494),
    ("Pasil Market", "Cebu City", 10.3010, 123.9050),
    ("Mandaue City Public Market", "Cebu City", 10.3236, 123.9226),
]

# (name, unit, category, SRP)
PRODUCTS = [
    ("Rice (Well-Milled)", "kg", "Grains", 48.00),
    ("Egg (Medium)", "piece", "Poultry", 7.50),
    ("Chicken (Whole)", "kg", "Poultry", 190.00),
    ("Pork (Kasim)", "kg", "Meat", 320.00),
    ("Beef (Brisket)", "kg", "Meat", 380.00),
    ("Tilapia", "kg", "Fish", 140.00),
    ("Galunggong", "kg", "Fish", 160.00),
    ("Tomato", "kg", "Vegetables", 80.00),
    ("Onion (Red)", "kg", "Vegetables", 90.00),
    ("Garlic", "kg", "Vegetables", 110.00),
]

if Market.count() or Product.count():
    print("Markets/products already exist; nothing seeded.")
else:
    for name, city, lat, lng in MARKETS:
        Market(market_name=name, city=city, latitude=lat, longitude=lng).save()

    for name, unit, category, srp in PRODUCTS:
        product = Product(product_name=name, unit=unit, category=category).save()
        DtiSrp(
            product_id=product.product_id,
            srp_price=srp,
            bulletin_date=date(2026, 7, 1),
            source="DTI SRP Bulletin",
        ).save()

    print(f"Seeded {len(MARKETS)} markets, {len(PRODUCTS)} products and their SRP baseline.")
