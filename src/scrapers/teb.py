import os
import re
import sys
import time
import json
import requests
from typing import Optional
from bs4 import BeautifulSoup
from dotenv import load_dotenv
from datetime import datetime

# Ensure src is in path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from sqlalchemy import create_engine, text
from services.ai_parser import AIParser
from services.brand_normalizer import cleanup_brands

load_dotenv()

# --- CONFIG ---
DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    raise ValueError("DATABASE_URL is not set in .env")

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

BASE_URL = "https://www.teb.com.tr"
API_URL = "https://www.teb.com.tr/teb/asmx/TebPublicServiceProxy.asmx/GetFirsatlarWeb"

BANK_NAME = "TEB"
BANK_SLUG = "teb"
BANK_LOGO = "https://www.teb.com.tr/Content/images/teb-logo.png"

# Card definitions — mapped from webCategory field
CARD_DEFINITIONS = {
    "kredi karti": {"name": "TEB Kredi Kartı", "slug": "teb-kredi-karti"},
    "banka karti": {"name": "TEB Banka Kartı", "slug": "teb-banka-karti"},
    "cepteteb":    {"name": "CEPTETEB",         "slug": "cepteteb"},
    "visa":        {"name": "TEB VISA",          "slug": "teb-visa"},
    "default":     {"name": "TEB Genel",         "slug": "teb-genel"},
}

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Content-Type": "application/json; charset=utf-8",
    "Referer": "https://www.teb.com.tr/sizin-icin/kampanyalar/",
}


def slugify(text: str) -> str:
    text = text.lower()
    tr_map = str.maketrans("çğıöşüÇĞİÖŞÜ", "cgiosucgiosu")
    text = text.translate(tr_map)
    text = re.sub(r'[^a-z0-9\s-]', '', text)
    text = re.sub(r'[\s-]+', '-', text).strip('-')
    return text


def html_to_text(html_content: str) -> str:
    if not html_content:
        return ""
    soup = BeautifulSoup(html_content, "html.parser")
    for tag in soup(["script", "style"]):
        tag.decompose()
    lines = [l.strip() for l in soup.get_text(separator="\n").splitlines() if l.strip()]
    return "\n".join(lines)


def parse_teb_date(date_str: str) -> Optional[str]:
    """Convert TEB date format '2026-02-01T00:00:00.000+0300' to 'YYYY-MM-DD'."""
    if not date_str:
        return None
    try:
        return date_str[:10]  # Just take YYYY-MM-DD
    except Exception:
        return None


def resolve_card_from_category(web_category: str) -> dict:
    """Map webCategory string to card definition."""
    if not web_category:
        return CARD_DEFINITIONS["default"]
    cat_lower = web_category.lower()
    if "cepteteb" in cat_lower:
        return CARD_DEFINITIONS["cepteteb"]
    if "visa" in cat_lower:
        return CARD_DEFINITIONS["visa"]
    if "kredi" in cat_lower:
        return CARD_DEFINITIONS["kredi karti"]
    if "banka" in cat_lower:
        return CARD_DEFINITIONS["banka karti"]
    return CARD_DEFINITIONS["default"]


class TEBScraper:
    def __init__(self):
        self.engine = create_engine(DATABASE_URL)
        self.ai_parser = AIParser() if GEMINI_API_KEY else None
        self.bank_id = None
        self._card_cache = {}  # slug -> card_id

    def _fetch_campaigns(self) -> list:
        """Fetch all campaigns from TEB API in a single POST request."""
        print("   🌐 Fetching campaigns from TEB API...")
        try:
            response = requests.post(API_URL, headers=HEADERS, json={}, timeout=30)
            response.raise_for_status()
            outer = response.json()
            items = json.loads(outer["d"])
            print(f"   ✅ API returned {len(items)} campaigns")
            return items
        except Exception as e:
            print(f"   ❌ API fetch failed: {e}")
            return []

    def _get_or_create_bank(self):
        """Find or create TEB bank."""
        try:
            with self.engine.connect() as conn:
                result = conn.execute(
                    text("SELECT id FROM banks WHERE slug = :slug"),
                    {"slug": BANK_SLUG}
                ).fetchone()
                if result:
                    self.bank_id = result[0]
                else:
                    print(f"   🏦 Creating Bank: {BANK_NAME}")
                    result = conn.execute(text("""
                        INSERT INTO banks (name, slug, logo_url, is_active, created_at)
                        VALUES (:name, :slug, :logo, true, NOW())
                        RETURNING id
                    """), {"name": BANK_NAME, "slug": BANK_SLUG, "logo": BANK_LOGO}).fetchone()
                    self.bank_id = result[0]
                    conn.commit()
                print(f"   ✅ Bank ID: {self.bank_id}")
        except Exception as e:
            print(f"   ❌ Bank setup failed: {e}")
            raise

    def _get_or_create_card(self, card_def: dict) -> int:
        """Find or create a card, cached by slug."""
        slug = card_def["slug"]
        if slug in self._card_cache:
            return self._card_cache[slug]
        try:
            with self.engine.connect() as conn:
                result = conn.execute(
                    text("SELECT id FROM cards WHERE slug = :slug"),
                    {"slug": slug}
                ).fetchone()
                if result:
                    card_id = result[0]
                else:
                    print(f"   💳 Creating Card: {card_def['name']}")
                    result = conn.execute(text("""
                        INSERT INTO cards (name, slug, bank_id, card_type, is_active, created_at)
                        VALUES (:name, :slug, :bank_id, 'credit', true, NOW())
                        RETURNING id
                    """), {"name": card_def["name"], "slug": slug, "bank_id": self.bank_id}).fetchone()
                    card_id = result[0]
                    conn.commit()
                self._card_cache[slug] = card_id
                return card_id
        except Exception as e:
            print(f"   ❌ Card setup failed: {e}")
            raise

    def _resolve_sector_by_name(self, sector_name: str) -> Optional[int]:
        if not sector_name:
            return None
        try:
            with self.engine.connect() as conn:
                result = conn.execute(
                    text("SELECT id FROM sectors WHERE name ILIKE :name LIMIT 1"),
                    {"name": f"%{sector_name}%"}
                ).fetchone()
                return result[0] if result else None
        except Exception:
            return None

    def _save_to_db(self, data: dict, brands: list = None):
        """Save or update campaign in DB."""
        campaign_id = None
        try:
            with self.engine.begin() as conn:
                existing = conn.execute(
                    text("SELECT id FROM campaigns WHERE tracking_url = :url"),
                    {"url": data["tracking_url"]}
                ).fetchone()

                if existing:
                    print(f"   ⏭️ Skipped (Already exists, preserving manual edits): {data['title'][:50]}")
                    return "skipped"
                else:
                    print(f"   ✨ Creating: {data['title'][:50]}")
                    result = conn.execute(text("""
                        INSERT INTO campaigns (
                            title, description, slug, image_url, tracking_url, is_active,
                            sector_id, card_id, start_date, end_date, conditions,
                            eligible_cards, reward_text, reward_value, reward_type, clean_text,
                            created_at, updated_at
                        )
                        VALUES (
                            :title, :description, :slug, :image_url, :tracking_url, true,
                            :sector_id, :card_id, :start_date, :end_date, :conditions,
                            :eligible_cards, :reward_text, :reward_value, :reward_type, :clean_text,
                            NOW(), NOW()
                        )
                        RETURNING id
                    """), {**data})
                    campaign_id = result.fetchone()[0]

                # Save brands
                if brands and campaign_id:
                    clean_brands = cleanup_brands(brands)
                    for brand_name in clean_brands:
                        brand_result = conn.execute(
                            text("SELECT id FROM brands WHERE name = :name"),
                            {"name": brand_name}
                        ).fetchone()
                        if brand_result:
                            brand_id = brand_result[0]
                        else:
                            brand_slug = f"{slugify(brand_name)}-{int(time.time())}"
                            brand_result = conn.execute(text("""
                                INSERT INTO brands (name, slug, is_active, created_at)
                                VALUES (:name, :slug, true, NOW())
                                RETURNING id
                            """), {"name": brand_name, "slug": brand_slug})
                            brand_id = brand_result.fetchone()[0]
                            print(f"      ✨ Created Brand: {brand_name}")

                        existing_link = conn.execute(text("""
                            SELECT 1 FROM campaign_brands
                            WHERE campaign_id = :campaign_id AND brand_id = CAST(:brand_id AS uuid)
                        """), {"campaign_id": campaign_id, "brand_id": brand_id}).fetchone()

                        if not existing_link:
                            conn.execute(text("""
                                INSERT INTO campaign_brands (campaign_id, brand_id)
                                VALUES (:campaign_id, CAST(:brand_id AS uuid))
                            """), {"campaign_id": campaign_id, "brand_id": brand_id})
                            print(f"      🔗 Linked Brand: {brand_name}")

            return "saved"
        except Exception as e:
            print(f"   ❌ DB Error: {e}")
            return "error"

    def _process_item(self, item: dict, card_id: int, card_name: str):
        """Process a single campaign item from the API."""
        title = (item.get("title") or "").strip()
        if not title:
            print("   ⚠️  Skipping: No title.")
            return "skipped"

        tracking_url = item.get("weblink") or ""
        if not tracking_url:
            print(f"   ⚠️  Skipping: No weblink for '{title[:40]}'")
            return "skipped"

        # Database Pre-check (Skip Logic)
        try:
            with self.engine.connect() as conn:
                existing = conn.execute(
                    text("SELECT id FROM campaigns WHERE tracking_url = :url"),
                    {"url": tracking_url}
                ).fetchone()
                if existing:
                    print(f"   ⏭️ Skipped (Already exists): {tracking_url}")
                    return "skipped"
        except Exception as e:
            print(f"   ⚠️ DB Pre-check error: {e}")

        # Image: prefer rollup (larger detail image), fallback to page image
        image_url = (
            item.get("publishingRollupImageUrl") or
            item.get("publishingPageImageUrl") or
            None
        )

        # Dates come directly from API — no AI needed
        start_date = parse_teb_date(item.get("startDate"))
        end_date = parse_teb_date(item.get("endDate"))

        # Content for AI parsing
        content_html = item.get("content") or ""
        content_text = html_to_text(content_html)

        # Enrich content with campaignCode for AI participation parsing
        campaign_code = item.get("campaignCode") or ""
        if campaign_code and campaign_code not in content_text:
            content_text = f"Kampanya Kodu: {campaign_code}\n" + content_text

        # Sector hint from API
        api_sector = (item.get("sectors") or "").replace(";", "").strip()

        # AI Parsing
        ai_data = {}
        if self.ai_parser and content_text:
            try:
                print(f"      🧠 AI parsing ({len(content_text)} chars)...")
                ai_data = self.ai_parser.parse_campaign_data(
                    raw_text=content_text,
                    title=title,
                    bank_name=BANK_NAME,
                    card_name=card_name,
                )
            except Exception as e:
                print(f"      ⚠️  AI Error: {e}")
                ai_data = {}

        # Build conditions
        conditions_lines = []
        participation = ai_data.get("participation") or ""
        if participation:
            conditions_lines.append(f"KATILIM: {participation}")

        # eligible_cards: ai_parser listesi döndürür ama guard ekle
        cards_raw = ai_data.get("cards", [])
        if isinstance(cards_raw, str):
            cards_raw = [c.strip() for c in cards_raw.split(",") if c.strip()]
        eligible_cards_list = cards_raw

        if eligible_cards_list:
            conditions_lines.append(f"GEÇERLİ KARTLAR: {', '.join(eligible_cards_list)}")

        # conditions listesi
        conds_raw = ai_data.get("conditions", [])
        if isinstance(conds_raw, str):
            conds_raw = [c.strip() for c in conds_raw.split("\n") if c.strip()]
        conditions_lines.extend(conds_raw)

        eligible_cards_str = ", ".join(eligible_cards_list) if eligible_cards_list else None
        if eligible_cards_str and len(eligible_cards_str) > 255:
            eligible_cards_str = eligible_cards_str[:255]

        # Slug: use weblink path segment + timestamp for uniqueness
        link_slug = slugify(tracking_url.rstrip("/").split("/")[-1] or title)
        slug = f"{link_slug}-{int(time.time())}" if not link_slug else link_slug

        # Sector: prefer AI result, fallback to API hint
        sector_name = ai_data.get("sector") or api_sector
        sector_id = self._resolve_sector_by_name(sector_name)

        campaign_data = {
            "title": ai_data.get("title") or title,
            "description": ai_data.get("description") or "",
            "image_url": image_url,
            "tracking_url": tracking_url,
            "slug": slug,
            # Dates from API directly (more reliable than AI)
            "start_date": start_date,
            "end_date": end_date,
            "is_active": True,
            "sector_id": sector_id,
            "card_id": card_id,
            "conditions": "\n".join(conditions_lines) if conditions_lines else None,
            "eligible_cards": eligible_cards_str,
            "reward_text": ai_data.get("reward_text"),
            "reward_value": ai_data.get("reward_value"),
            "reward_type": ai_data.get("reward_type"),
                            "clean_text": ai_data.get("_clean_text"),
        }

        return self._save_to_db(campaign_data, ai_data.get("brands", []))

    def run(self, limit: int = 1000, card_filter: str = "all"):
        """
        Main entry point.
        card_filter: 'kredi' | 'banka' | 'cepteteb' | 'visa' | 'all'
        """
        print("🚀 Starting TEB Scraper (API Mode)...")
        print(f"   🌐 API: {API_URL}")

        self._get_or_create_bank()

        items = self._fetch_campaigns()
        if not items:
            print("   ❌ No campaigns found. Exiting.")
            return

        # Filter by card type if requested
        if card_filter != "all":
            before = len(items)
            items = [
                i for i in items
                if card_filter.lower() in (i.get("webCategory") or "").lower()
            ]
            print(f"   🔍 Filtered to {len(items)} campaigns (from {before}) by '{card_filter}'")

        items = items[:limit]
        print(f"\n   🎯 Processing {len(items)} campaigns...\n")

        success = skipped = failed = 0
        error_details = []

        for idx, item in enumerate(items):
            title = item.get("title", "?")[:60]
            print(f"[{idx+1}/{len(items)}] {title}")

            # Determine card from webCategory
            web_category = item.get("webCategory") or ""
            card_def = resolve_card_from_category(web_category)
            try:
                card_id = self._get_or_create_card(card_def)
            except Exception as e:
                print(f"   ❌ Card error: {e}")
                failed += 1
                error_details.append({"url": item.get("weblink", "unknown"), "error": f"Card error: {str(e)}"})
                continue

            try:
                # Same issue as QNB: _process_item actually needs to return the result of _save_to_db, but it is already doing that.
                res = self._process_item(item, card_id, card_def["name"])
                if res == "saved":
                    success += 1
                elif res == "skipped":
                    skipped += 1
                else:
                    failed += 1
                    error_details.append({"url": item.get("weblink", "unknown"), "error": "Unknown DB failure"})
            except Exception as e:
                print(f"   ❌ Failed: {e}")
                failed += 1
                error_details.append({"url": item.get("weblink", "unknown"), "error": str(e)})

            time.sleep(0.5)

        print(f"\n🏁 TEB Scraper Finished.")
        print(f"✅ Özet: {len(items)} bulundu, {success} eklendi, {skipped} atlandı, {failed} hata aldı.")
        
        status = "SUCCESS"
        if failed > 0:
             status = "PARTIAL" if (success > 0 or skipped > 0) else "FAILED"
             
        try:
            from src.utils.logger_utils import log_scraper_execution
            from sqlalchemy.orm import sessionmaker
            SessionLocal = sessionmaker(bind=self.engine)
            with SessionLocal() as db:
                 log_scraper_execution(
                      db=db,
                      scraper_name="teb",
                      status=status,
                      total_found=len(items),
                      total_saved=success,
                      total_skipped=skipped,
                      total_failed=failed,
                      error_details={"errors": error_details} if error_details else None
                 )
        except Exception as le:
             print(f"⚠️ Could not save scraper log: {le}")


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="TEB Scraper")
    parser.add_argument("--limit", type=int, default=1000, help="Max campaigns to process")
    parser.add_argument(
        "--card-filter", type=str, default="all",
        help="Filter by card type: kredi | banka | cepteteb | visa | all"
    )
    args = parser.parse_args()

    scraper = TEBScraper()
    scraper.run(limit=args.limit, card_filter=args.card_filter)
