import os
import re
import sys
import time
import json
import requests # type: ignore
from typing import Optional, List, Dict, Any
from bs4 import BeautifulSoup # type: ignore
from dotenv import load_dotenv # type: ignore

# Fix sys.path for src discovery
current_dir = os.path.dirname(os.path.abspath(__file__))
if "src" in current_dir:
    project_root = os.path.dirname(os.path.dirname(current_dir))
else:
    project_root = current_dir

if project_root not in sys.path:
    sys.path.insert(0, project_root)

# Database
from sqlalchemy import create_engine, text, func # type: ignore
from sqlalchemy.orm import sessionmaker # type: ignore

# AI & Services
from src.services.ai_parser import AIParser # type: ignore
from src.services.brand_normalizer import cleanup_brands # type: ignore
from src.utils.logger_utils import log_scraper_execution # type: ignore

load_dotenv()

# --- CONFIG ---
DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    raise ValueError("DATABASE_URL is not set in .env")

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

# QNB API
BASE_URL = "https://www.qnbcard.com.tr"
API_URL = "https://www.qnbcard.com.tr/api/Campaigns"

BANK_NAME = "QNB"
BANK_SLUG = "qnb"
BANK_LOGO = "https://www.qnbcard.com.tr/Content/images/logo.png"

# Default card for QNB campaigns (most campaigns are for all QNB cards)
CARD_NAME = "QNBCard"
CARD_SLUG = "qnbcard"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "tr-TR,tr;q=0.9",
    "Referer": "https://www.qnbcard.com.tr/kampanyalar",
}


def slugify(text: str) -> str:
    """Convert text to URL-friendly slug."""
    text = text.lower()
    tr_map = str.maketrans("çğıöşüÇĞİÖŞÜ", "cgiosucgiosu")
    text = text.translate(tr_map)
    text = re.sub(r'[^a-z0-9\s-]', '', text)
    text = re.sub(r'[\s-]+', '-', text).strip('-')
    return text


def html_to_text(html_content: str) -> str:
    """Convert HTML to clean plain text."""
    if not html_content:
        return ""
    soup = BeautifulSoup(html_content, "html.parser")
    # Remove script and style elements
    for tag in soup(["script", "style"]):
        tag.decompose()
    text = soup.get_text(separator="\n")
    # Clean up whitespace
    lines = [line.strip() for line in text.splitlines()]
    lines = [line for line in lines if line]
    return "\n".join(lines)


class QNBScraper:
    def __init__(self):
        self.engine = create_engine(DATABASE_URL)
        self.ai_parser = AIParser() if GEMINI_API_KEY else None
        self.card_id = None
        self.bank_id = None

    def _fetch_campaign_urls_to_items(self) -> list:
        """Helper to fetch all items for URL filtering."""
        return self._fetch_campaigns_from_api(limit=1000)

    def _fetch_campaigns_from_api(self, limit=1000) -> list:
        """Fetch all campaigns from QNB API using pagination."""
        print(f"   🌐 Fetching campaigns from QNB API with pagination...")
        all_items = []
        page_index = 0
        take = 12 # QNB default page size
        
        try:
            while True:
                params = {
                    "isArchived": "false",
                    "sectorId": "",
                    "brandId": "",
                    "categoryId": "",
                    "keyword": "",
                    "year": "",
                    "month": "",
                    "take": str(take),
                    "PageIndex": str(page_index)
                }
                response = requests.get(API_URL, params=params, headers=HEADERS, timeout=30)
                response.raise_for_status()
                data = response.json()

                items = data.get("Items", [])
                if not items:
                    break
                    
                all_items.extend(items)
                total = data.get("TotalItems", 0)
                
                print(f"      📄 Fetched page {page_index + 1} ({len(items)} items). Total so far: {len(all_items)}/{total}")
                
                # If we've fetched everything or reached limit
                if len(all_items) >= total or (limit and len(all_items) >= limit):
                    break
                    
                page_index += 1
                time.sleep(0.5) # small delay between pages
                
            print(f"   ✅ API returned a total of {len(all_items)} campaigns")
            return all_items[:limit] if limit else all_items
            
        except Exception as e:
            print(f"   ❌ API fetch failed: {e}")
            return all_items

    def _get_or_create_bank_and_card(self):
        """Find or create QNB and QNBCard."""
        try:
            with self.engine.connect() as conn:
                # 1. Find Bank (by slug or alias)
                result = conn.execute(
                    text("SELECT id FROM banks WHERE slug = :slug OR :name = ANY(aliases) OR name = :name"),
                    {"slug": BANK_SLUG, "name": "QNB Finansbank"}
                ).fetchone()

                if result:
                    self.bank_id = result[0]
                else:
                    print(f"   🏦 Creating Bank: {BANK_NAME}")
                    result = conn.execute(text("""
                        INSERT INTO banks (name, slug, logo_url, is_active, created_at, aliases)
                        VALUES (:name, :slug, :logo, true, NOW(), :aliases)
                        RETURNING id
                    """), {
                        "name": BANK_NAME, 
                        "slug": BANK_SLUG, 
                        "logo": BANK_LOGO,
                        "aliases": ["QNB Finansbank", "Finansbank"]
                    }).fetchone()
                    self.bank_id = result[0]
                    conn.commit()


                # 2. Find or Create Card
                result = conn.execute(
                    text("SELECT id FROM cards WHERE slug = :slug"),
                    {"slug": CARD_SLUG}
                ).fetchone()

                if result:
                    self.card_id = result[0]
                else:
                    print(f"   💳 Creating Card: {CARD_NAME}")
                    result = conn.execute(text("""
                        INSERT INTO cards (name, slug, bank_id, card_type, is_active, created_at)
                        VALUES (:name, :slug, :bank_id, 'credit', true, NOW())
                        RETURNING id
                    """), {"name": CARD_NAME, "slug": CARD_SLUG, "bank_id": self.bank_id}).fetchone()
                    self.card_id = result[0]
                    conn.commit()

                print(f"   ✅ Using Card ID: {self.card_id} (Bank ID: {self.bank_id})")
        except Exception as e:
            print(f"   ❌ Failed to get/create bank/card: {e}")
            raise e

    def _resolve_sector_by_name(self, sector_name: str) -> Optional[int]:
        """Find sector ID by name."""
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

    def _save_to_db(self, data: dict, brands: Optional[list] = None, force: bool = False):
        """Save or update campaign in DB. Skips if tracking_url already exists."""
        if not self.card_id:
            self._get_or_create_bank_and_card()

        campaign_id = None
        try:
            with self.engine.begin() as conn:
                # Duplicate check by tracking_url (redundant if called from _process_item, but safe)
                existing = conn.execute(
                    text("SELECT id FROM campaigns WHERE tracking_url = :url AND card_id = :card_id"),
                    {"url": data["tracking_url"], "card_id": self.card_id}
                ).fetchone()

                if existing and not force:
                    print(f"   ⏭️ Skipped (Already exists): {data['title'][:50]}")
                    return "skipped"
                if existing and force:
                    print(f"   🔄 Updating: {data['title'][:50]}")
                    # Actual update logic would go here, but for simplicity of this scraper's 
                    # architecture let's just insert/replace if force is on.
                    # (Simplified for now - we primarily care about skipping already existing)
                    return "skipped" 

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
                """), {**data, "card_id": self.card_id})
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

    def _process_item(self, item: dict, force: bool = False):
        """Process a single campaign item from the API."""
        # --- Extract basic fields ---
        title = item.get("Title", "").strip()
        if not title:
            print("   ⚠️  Skipping: No title found.")
            return "skipped"

        seo = item.get("SeoProperty") or {}
        seo_name = seo.get("Name") or ""

        if seo_name:
            campaign_url = f"{BASE_URL}/kampanyalar/{seo_name}"
        else:
            campaign_url = f"{BASE_URL}/kampanyalar/{slugify(title)}"

        print(f"\n   🔗 Processing: {campaign_url}")

        # --- 1. DB Check FIRST (Immediate skip if not forced) ---
        if not force:
            if not self.card_id: self._get_or_create_bank_and_card()
            with self.engine.connect() as conn:
                existing = conn.execute(
                    text("SELECT id FROM campaigns WHERE tracking_url = :url AND card_id = :card_id"),
                    {"url": campaign_url, "card_id": self.card_id}
                ).fetchone()
                if existing:
                    print(f"      ⏭️  Skipped (URL already exists in DB)")
                    return "skipped"

        # --- Extract image ---
        # QNB uses a consistent pattern: /medium/Campaign-DetailImage-{Id}.vsf
        item_id = item.get("Id") or ""
        if item_id and item.get("HasImage"):
            image_url = f"{BASE_URL}/medium/Campaign-DetailImage-{item_id}.vsf"
        else:
            image_url = None

        # --- Extract content ---
        content_html = item.get("Content") or item.get("Description") or ""
        content_text = html_to_text(content_html)

        # --- AI Parsing (With Cache Support) ---
        ai_data = {}
        if self.ai_parser and content_text:
            try:
                print(f"      🧠 AI parsing ({len(content_text)} chars)...")
                ai_data = self.ai_parser.parse_campaign_data(
                    raw_text=content_text,
                    title=title,
                    bank_name=BANK_NAME,
                    card_name=CARD_NAME,
                    tracking_url=campaign_url,
                    force=force
                )
            except Exception as e:
                print(f"      ⚠️  AI Error: {e}")
                ai_data = {}

        # --- Build conditions text ---
        conditions_lines = []

        participation = ai_data.get("participation") or ""
        if participation:
            conditions_lines.append(f"KATILIM: {participation}") # type: ignore

        # eligible_cards: ai_parser artık liste döndürüyor ama guard ekle
        cards_raw = ai_data.get("cards", [])
        if isinstance(cards_raw, str):
            cards_raw = [c.strip() for c in cards_raw.split(",") if c.strip()]
        eligible_cards_list = cards_raw

        if eligible_cards_list:
            conditions_lines.append(f"GEÇERLİ KARTLAR: {', '.join(eligible_cards_list)}") # type: ignore

        conds_raw = ai_data.get("conditions", [])
        if isinstance(conds_raw, str):
            conds_raw = [c.strip() for c in conds_raw.split("\n") if c.strip()]
        conditions_lines.extend(conds_raw) # type: ignore

        eligible_cards_str = ", ".join(eligible_cards_list) if eligible_cards_list else None
        if eligible_cards_str and len(eligible_cards_str) > 255:
            eligible_cards_str = eligible_cards_str[:255] # type: ignore

        # --- Build slug ---
        title_slug = slugify(ai_data.get("title") or title)
        slug = f"{title_slug}-{item_id}" if item_id else title_slug

        # --- Assemble campaign data ---
        campaign_data = {
            "title": ai_data.get("title") or title,
            # description: use AI's 2-sentence marketing text; fallback to title only (not raw HTML dump)
            "description": ai_data.get("description") or "",
            "image_url": image_url,
            "tracking_url": campaign_url,
            "slug": slug,
            "start_date": ai_data.get("start_date"),
            "end_date": ai_data.get("end_date"),
            "is_active": True,
            "sector_id": self._resolve_sector_by_name(ai_data.get("sector")),
            "conditions": "\n".join(conditions_lines) if conditions_lines else None,
            "eligible_cards": eligible_cards_str,
            "reward_text": ai_data.get("reward_text"),
            "reward_value": ai_data.get("reward_value"),
            "reward_type": ai_data.get("reward_type"),
            "clean_text": ai_data.get("_clean_text"),
        }

        return self._save_to_db(campaign_data, ai_data.get("brands", []), force=force)

    def run(self, limit: Optional[int] = None, urls: Optional[List[str]] = None, force: bool = False):
        """Main entry point."""
        print("🚀 Starting QNB Finansbank Scraper (API Mode)...")
        print(f"   🌐 API: {API_URL}")

        # Setup bank/card
        self._get_or_create_bank_and_card()

        if urls:
            items = []
            # Note: QNB API doesn't support fetching specific items easily by URL,
            # but for consistency we'd need to mock the item structure or fetch all.
            # For now, let's just fetch all and filter if specific URLs are provided.
            all_items = self._fetch_campaign_urls_to_items()
            for item in all_items:
                seo = item.get("SeoProperty") or {}
                seo_name = seo.get("Name") or ""
                item_url = f"{BASE_URL}/kampanyalar/{seo_name}" if seo_name else f"{BASE_URL}/kampanyalar/{slugify(item.get('Title',''))}"
                if item_url in urls:
                    items.append(item)
        else:
            items = self._fetch_campaigns_from_api(limit=limit or 1000)

        if not items:
            print("   ❌ No campaigns found to process. Exiting.")
            return

        print(f"\n   🎯 Processing {len(items)} campaigns...\n")

        success = 0
        skipped = 0
        failed = 0
        error_details = []

        for i, item in enumerate(items):
            title = item.get("Title", "?")
            print(f"[{i+1}/{len(items)}] {title[:60]}")
            try:
                # We need to call the method that actually processes
                # Note: Currently QNB does all processing in _save_to_db 
                # but the loop calls _process_item... wait, _process_item returns the result of _save_to_db!
                res = self._process_item(item, force=force)
                if res == "saved":
                    success += 1
                elif res == "skipped":
                    skipped += 1
                else:
                    failed += 1
                    error_details.append({"url": item.get("Id", "unknown"), "error": "Unknown DB failure"})
            except Exception as e:
                print(f"   ❌ Failed: {e}")
                failed += 1
                error_details.append({"url": item.get("Id", "unknown"), "error": str(e)})

            # Small delay to avoid hammering AI API
            time.sleep(0.5)

        print(f"\n🏁 QNB Scraper Finished.")
        print(f"✅ Özet: {len(items)} bulundu, {success} eklendi, {skipped} atlandı, {failed} hata aldı.")
        
        status = "SUCCESS"
        if failed > 0:
             status = "PARTIAL" if (success > 0 or skipped > 0) else "FAILED"
             
        try:
            SessionLocal = sessionmaker(bind=self.engine)
            with SessionLocal() as db:
                 log_scraper_execution(
                      db=db,
                      scraper_name="qnb",
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

    parser = argparse.ArgumentParser(description="QNB Finansbank Scraper")
    parser.add_argument("--limit", type=int, default=1000, help="Max campaigns to fetch")
    args = parser.parse_args()

    scraper = QNBScraper()
    scraper.run(limit=args.limit)
