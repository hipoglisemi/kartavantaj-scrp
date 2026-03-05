"""
DB Integrity Fixer

Finds and merges duplicate banks, cards, and sectors.
Migrates all campaign references from duplicate records to the canonical one,
then deletes the duplicates.

Safe to run multiple times (idempotent).
"""

import os
import sys
from sqlalchemy import text

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.database import get_db_session


def fix_duplicate_banks(db):
    print("\n🏦 Checking for duplicate banks...")
    rows = db.execute(text("""
        SELECT name, array_agg(id ORDER BY id) as ids, COUNT(*) as cnt
        FROM banks
        GROUP BY name
        HAVING COUNT(*) > 1
    """)).fetchall()

    if not rows:
        print("   ✅ No duplicate banks found.")
        return

    for row in rows:
        name, ids, cnt = row
        keep_id = ids[0]
        dup_ids = ids[1:]
        print(f"   🔀 Bank '{name}': keeping id={keep_id}, merging {dup_ids}")

        for dup_id in dup_ids:
            # Re-point cards from duplicate bank to canonical bank
            result = db.execute(text(
                "UPDATE cards SET bank_id = :keep WHERE bank_id = :dup"
            ), {"keep": keep_id, "dup": dup_id})
            print(f"      ↳ Migrated {result.rowcount} cards from bank {dup_id} → {keep_id}")

            # Delete the duplicate bank
            db.execute(text("DELETE FROM banks WHERE id = :dup"), {"dup": dup_id})
            print(f"      🗑️  Deleted duplicate bank id={dup_id}")

    db.commit()
    print("   ✅ Bank duplicates resolved.")


def fix_duplicate_cards(db):
    print("\n💳 Checking for duplicate cards...")
    rows = db.execute(text("""
        SELECT name, bank_id, array_agg(id ORDER BY id) as ids, COUNT(*) as cnt
        FROM cards
        GROUP BY name, bank_id
        HAVING COUNT(*) > 1
    """)).fetchall()

    if not rows:
        print("   ✅ No duplicate cards found.")
        return

    for row in rows:
        name, bank_id, ids, cnt = row
        keep_id = ids[0]
        dup_ids = ids[1:]
        print(f"   🔀 Card '{name}' (bank_id={bank_id}): keeping id={keep_id}, merging {dup_ids}")

        for dup_id in dup_ids:
            # Migrate campaigns from duplicate card to canonical card
            result = db.execute(text(
                "UPDATE campaigns SET card_id = :keep WHERE card_id = :dup"
            ), {"keep": keep_id, "dup": dup_id})
            print(f"      ↳ Migrated {result.rowcount} campaigns from card {dup_id} → {keep_id}")

            db.execute(text("DELETE FROM cards WHERE id = :dup"), {"dup": dup_id})
            print(f"      🗑️  Deleted duplicate card id={dup_id}")

    db.commit()
    print("   ✅ Card duplicates resolved.")


def fix_duplicate_sectors(db):
    print("\n🏷️  Checking for duplicate sectors...")
    rows = db.execute(text("""
        SELECT slug, array_agg(id ORDER BY id) as ids, COUNT(*) as cnt
        FROM sectors
        GROUP BY slug
        HAVING COUNT(*) > 1
    """)).fetchall()

    if not rows:
        print("   ✅ No duplicate sectors found.")
        return

    for row in rows:
        slug, ids, cnt = row
        keep_id = ids[0]
        dup_ids = ids[1:]
        print(f"   🔀 Sector '{slug}': keeping id={keep_id}, merging {dup_ids}")

        for dup_id in dup_ids:
            result = db.execute(text(
                "UPDATE campaigns SET sector_id = :keep WHERE sector_id = :dup"
            ), {"keep": keep_id, "dup": dup_id})
            print(f"      ↳ Migrated {result.rowcount} campaigns from sector {dup_id} → {keep_id}")

            db.execute(text("DELETE FROM sectors WHERE id = :dup"), {"dup": dup_id})
            print(f"      🗑️  Deleted duplicate sector id={dup_id}")

    db.commit()
    print("   ✅ Sector duplicates resolved.")


def run_integrity_fix():
    print("🚀 Starting DB Integrity Fixer...")
    try:
        with get_db_session() as db:
            fix_duplicate_banks(db)
            fix_duplicate_cards(db)
            fix_duplicate_sectors(db)

        print("\n🏁 DB Integrity Fix complete.")
    except Exception as e:
        print(f"\n📛 CRITICAL ERROR: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    run_integrity_fix()
