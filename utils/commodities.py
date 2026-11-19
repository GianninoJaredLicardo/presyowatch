"""
Common Philippine market commodities, pre-registered so vendors can pick them from a list.

Based on the goods the DA (Bantay Presyo) and DTI (basic necessities and prime commodities)
monitor in public markets. Added automatically when the app starts; existing commodities
with the same name are left untouched, so this is safe to run again.
"""
import os

from flask import current_app

# Bump when the list below changes, so new items get added on the next start.
LIST_VERSION = 1

# (category, [(name, unit), ...])
COMMON_COMMODITIES = [
    ("Rice & Corn", [
        ("Regular Milled Rice", "kg"), ("Well-Milled Rice", "kg"), ("Premium Rice", "kg"),
        ("Special Rice (Dinorado)", "kg"), ("Jasmine Rice", "kg"), ("Glutinous Rice (Malagkit)", "kg"),
        ("Brown Rice", "kg"), ("NFA Rice", "kg"), ("Corn Grits (White)", "kg"), ("Corn Grits (Yellow)", "kg"),
    ]),
    ("Fish & Seafood", [
        ("Galunggong (Round Scad)", "kg"), ("Tilapia", "kg"), ("Bangus (Milkfish)", "kg"),
        ("Tamban (Sardines)", "kg"), ("Alumahan (Mackerel)", "kg"), ("Tulingan (Bullet Tuna)", "kg"),
        ("Tuna (Tambakol)", "kg"), ("Dalagang Bukid (Fusilier)", "kg"), ("Matambaka (Bigeye Scad)", "kg"),
        ("Pusit (Squid)", "kg"), ("Hipon (Shrimp)", "kg"), ("Alimango (Mud Crab)", "kg"),
        ("Tahong (Mussels)", "kg"), ("Danggit (Dried)", "kg"), ("Tuyo (Dried Herring)", "kg"),
    ]),
    ("Pork", [
        ("Pork Kasim (Shoulder)", "kg"), ("Pork Liempo (Belly)", "kg"), ("Pork Pigue (Ham)", "kg"),
        ("Pork Chop", "kg"), ("Pork Ribs", "kg"), ("Pork Pata (Hock)", "kg"), ("Ground Pork", "kg"),
        ("Pork Lard (Taba)", "kg"),
    ]),
    ("Beef", [
        ("Beef Brisket", "kg"), ("Beef Rump", "kg"), ("Beef Shank (Bulalo)", "kg"),
        ("Beef Tenderloin", "kg"), ("Ground Beef", "kg"),
    ]),
    ("Poultry & Eggs", [
        ("Whole Chicken (Dressed)", "kg"), ("Chicken Breast", "kg"), ("Chicken Leg Quarter", "kg"),
        ("Chicken Wings", "kg"), ("Chicken Liver", "kg"), ("Egg (Medium)", "pc"), ("Egg (Large)", "pc"),
        ("Duck Egg (Itlog na Pula)", "pc"), ("Quail Egg", "pc"),
    ]),
    ("Vegetables", [
        ("Ampalaya (Bitter Gourd)", "kg"), ("Sitaw (String Beans)", "kg"), ("Kalabasa (Squash)", "kg"),
        ("Talong (Eggplant)", "kg"), ("Kamatis (Tomato)", "kg"), ("Pechay Tagalog", "kg"),
        ("Pechay Baguio (Napa Cabbage)", "kg"), ("Repolyo (Cabbage)", "kg"), ("Carrots", "kg"),
        ("Patatas (Potato)", "kg"), ("Sayote (Chayote)", "kg"), ("Okra", "kg"), ("Upo (Bottle Gourd)", "kg"),
        ("Kangkong (Water Spinach)", "bundle"), ("Malunggay (Moringa)", "bundle"), ("Bell Pepper", "kg"),
        ("Baguio Beans", "kg"), ("Broccoli", "kg"), ("Cauliflower", "kg"), ("Camote (Sweet Potato)", "kg"),
        ("Gabi (Taro)", "kg"), ("Mung Beans (Monggo)", "kg"),
    ]),
    ("Spices", [
        ("Red Onion (Sibuyas Pula)", "kg"), ("White Onion (Sibuyas Puti)", "kg"), ("Garlic (Bawang) Local", "kg"),
        ("Garlic (Bawang) Imported", "kg"), ("Ginger (Luya)", "kg"), ("Siling Labuyo (Bird's Eye Chili)", "kg"),
        ("Siling Haba (Finger Chili)", "kg"), ("Calamansi", "kg"), ("Spring Onion (Dahon ng Sibuyas)", "bundle"),
        ("Lemongrass (Tanglad)", "bundle"),
    ]),
    ("Fruits", [
        ("Banana Lakatan", "kg"), ("Banana Latundan", "kg"), ("Banana Saba", "kg"), ("Mango (Carabao)", "kg"),
        ("Papaya", "kg"), ("Pineapple", "pc"), ("Watermelon", "kg"), ("Pomelo", "pc"), ("Coconut (Buko)", "pc"),
        ("Apple", "pc"), ("Orange", "pc"),
    ]),
    ("Sugar, Oil & Condiments", [
        ("Refined Sugar", "kg"), ("Washed Sugar", "kg"), ("Brown Sugar", "kg"),
        ("Cooking Oil (Palm) 1L", "bottle"), ("Cooking Oil (Coconut) 1L", "bottle"), ("Iodized Salt", "kg"),
        ("Soy Sauce 1L", "bottle"), ("Vinegar 1L", "bottle"), ("Fish Sauce (Patis) 750ml", "bottle"),
        ("Bagoong (Shrimp Paste)", "jar"),
    ]),
    ("Canned & Processed", [
        ("Canned Sardines 155g", "can"), ("Canned Tuna 155g", "can"), ("Corned Beef 150g", "can"),
        ("Meat Loaf 150g", "can"), ("Luncheon Meat 340g", "can"), ("Instant Noodles", "pack"),
        ("Pandesal", "pc"), ("Loaf Bread (Tasty)", "loaf"), ("Hotdog 1kg", "pack"), ("Longganisa", "kg"),
        ("Tocino", "kg"),
    ]),
    ("Dairy & Beverages", [
        ("Evaporated Milk 370ml", "can"), ("Condensed Milk 300ml", "can"), ("Powdered Milk 150g", "pack"),
        ("3-in-1 Coffee", "sachet"), ("Instant Coffee 50g", "jar"), ("Bottled Water 500ml", "bottle"),
    ]),
    ("Household", [
        ("Laundry Soap (Bar)", "bar"), ("Detergent Powder 1kg", "pack"), ("Bath Soap", "bar"),
        ("Candles", "pc"), ("LPG 11kg", "tank"),
    ]),
]

CATEGORIES = [c for c, _ in COMMON_COMMODITIES]


def ensure_default_commodities():
    """Add any missing common commodities. Runs once per LIST_VERSION; never raises."""
    if os.environ.get("PRESYOWATCH_SKIP_COMMODITY_SEED"):
        return 0
    try:
        from extensions import get_db
        from models import Product

        # One marker per list version. create() fails if it already exists, so with several
        # server workers starting at once only one of them adds the commodities.
        marker = get_db().collection("settings").document(f"commodities_v{LIST_VERSION}")
        try:
            marker.create({"version": LIST_VERSION, "status": "adding"})
        except Exception:
            return 0  # already done (or being done by another worker)
        existing = {(p.product_name or "").strip().lower() for p in Product.all()}
        added = 0
        for category, items in COMMON_COMMODITIES:
            for name, unit in items:
                if name.lower() not in existing:
                    Product(product_name=name, unit=unit, category=category).save()
                    existing.add(name.lower())
                    added += 1
        marker.set({"version": LIST_VERSION, "status": "done", "added": added})
        if added:
            current_app.logger.info("Registered %d common commodities.", added)
        return added
    except Exception:
        current_app.logger.exception("Could not register the common commodities list.")
        return 0
