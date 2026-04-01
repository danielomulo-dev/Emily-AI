"""
MongoDB Migration Script: Bahrain → Virginia
Copies all collections from the old cluster to the new one.
"""
import certifi
from pymongo import MongoClient

# ── Old cluster (Bahrain) ──
OLD_URI = "mongodb+srv://danielomulo_db_user:WjaEHyHIqRrKp9ic@cluster-1.og8yd3o.mongodb.net/emily_brain_db?retryWrites=true&w=majority&serverSelectionTimeoutMS=60000&connectTimeoutMS=60000"

# ── New cluster (Virginia) ──
NEW_URI = "mongodb+srv://emily_bot:ABBQJraqDQ4r1Fdy@emily-cluster.ecqxug2.mongodb.net/emily_brain_db?retryWrites=true&w=majority"

# All collections used by Emily
COLLECTIONS = [
    "anniversaries",
    "app_tokens",
    "budget_limits",
    "budgets",
    "digest_log",
    "dm_report_log",
    "goals",
    "gratitude",
    "income",
    "investment_alerts",
    "journal",
    "message_log",
    "movie_ratings",
    "movie_settings",
    "movie_suggestions",
    "notes",
    "nudge_log",
    "portfolios",
    "reminders",
    "saved_playlists",
    "scripture_log",
    "sent_news",
    "server_settings",
    "sleep",
    "todos",
    "users",
    "watchlists",
    "watchparties",
    "watchparty_contacts",
]

def migrate():
    print("Connecting to OLD cluster (Bahrain)...")
    old_client = MongoClient(OLD_URI, tlsCAFile=certifi.where())
    old_client.admin.command('ping')
    old_db = old_client["emily_brain_db"]
    print("✅ Connected to old cluster")

    print("Connecting to NEW cluster (Virginia)...")
    new_client = MongoClient(NEW_URI, tlsCAFile=certifi.where())
    new_client.admin.command('ping')
    new_db = new_client["emily_brain_db"]
    print("✅ Connected to new cluster")

    total_docs = 0
    for col_name in COLLECTIONS:
        old_col = old_db[col_name]
        new_col = new_db[col_name]

        docs = list(old_col.find())
        count = len(docs)

        if count == 0:
            print(f"  ⬜ {col_name}: empty, skipping")
            continue

        # Remove _id to avoid duplicate key errors if re-running
        for doc in docs:
            doc.pop("_id", None)

        new_col.insert_many(docs)
        total_docs += count
        print(f"  ✅ {col_name}: {count} documents migrated")

    print(f"\n🎉 Migration complete! {total_docs} total documents moved.")
    
    old_client.close()
    new_client.close()

if __name__ == "__main__":
    migrate()
