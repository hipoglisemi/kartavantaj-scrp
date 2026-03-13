# pyre-ignore-all-errors
# type: ignore

"""
Masterpass Plus Scraper
Powered by Playwright
"""

import os
import sys
import time
import re
import uuid
import traceback
import json
from datetime import datetime
from typing import Optional, Dict, Any, List
from urllib.parse import urljoin
from bs4 import BeautifulSoup # type: ignore

# Fix sys.path to ensure src is discoverable
current_dir = os.path.dirname(os.path.abspath(__file__))
# Check if we are in src/scrapers or root
if "src" in current_dir:
    project_root = os.path.dirname(os.path.dirname(current_dir))
else:
    project_root = current_dir

if project_root not in sys.path:
    sys.path.insert(0, project_root)

from dotenv import load_dotenv # type: ignore
load_dotenv(os.path.join(project_root, '.env'))

from sqlalchemy import create_engine, text, func # type: ignore
from sqlalchemy.orm import sessionmaker # type: ignore

# Import unified models and database session
from src.database import engine, get_db_session # type: ignore
from src.models import Bank, Card, Sector, Brand, Campaign, CampaignBrand # type: ignore
from src.utils.logger_utils import log_scraper_execution # type: ignore

# AIParser is lazy-imported in __init__ to avoid google.generativeai hang
AIParser = None

class MasterpassScraper:
    """Masterpass Plus scraper - Playwright based"""

    BASE_URL = "https://www.masterpassturkiye.com"
    CAMPAIGNS_URL = "https://www.masterpassturkiye.com/masterpassplus"
    BANK_NAME = "Mastercard"
    BANK_SLUG = "mastercard"

    def __init__(self):
        self.engine = engine
        self.db = get_db_session()
        self.card_id = None
        
        # Lazy import of AIParser
        try:
            from src.services.ai_parser import AIParser as _AIParser # type: ignore
        except ImportError:
            from services.ai_parser import AIParser as _AIParser # type: ignore
        self.parser = _AIParser()

        self.page = None
        self.browser = None
        self.playwright = None
        self._init_card()

    def _init_card(self):
        bank = self.db.query(Bank).filter(Bank.slug == self.BANK_SLUG).first()
        if not bank:
            print(f"⚠️  Masterpass not found in DB, creating...")
            bank = Bank(name=self.BANK_NAME, slug=self.BANK_SLUG)
            self.db.add(bank)
            self.db.commit()
        print(f"✅ Bank: {bank.name} (ID: {bank.id})")

        card = self.db.query(Card).filter(Card.slug == 'mastercard').first()
        if not card:
            print(f"⚠️  Card 'mastercard' not found, creating...")
            card = Card(bank_id=bank.id, name='Mastercard', slug='mastercard', is_active=True)
            self.db.add(card)
            self.db.commit()
        self.card_id = card.id
        print(f"✅ Card: {card.name} (ID: {self.card_id})")

    def _start_browser(self):
        from playwright.sync_api import sync_playwright # type: ignore
        self.playwright = sync_playwright().start() # type: ignore
        
        is_ci = os.getenv("GITHUB_ACTIONS") == "true" or os.getenv("CI") == "true"
        connected = False

        if not is_ci:
            try:
                print("   🔌 Attempting to connect to local Chrome debug instance at http://localhost:9222...")
                self.browser = self.playwright.chromium.connect_over_cdp("http://localhost:9222") # type: ignore
                connected = True
                print("   ✅ Connected to local existing Chrome instance")
                
                if len(self.browser.contexts) > 0: # type: ignore
                    context = self.browser.contexts[0] # type: ignore
                else:
                    context = self.browser.new_context() # type: ignore
                    
                self.page = context.new_page()
                self.page.set_default_timeout(120000) # type: ignore
                return
            except Exception as e:
                pass
                
        if not connected:
            self.browser = self.playwright.chromium.launch( # type: ignore
                headless=True,
                args=["--no-sandbox", "--disable-setuid-sandbox",
                      "--disable-dev-shm-usage", "--disable-gpu", "--window-size=1920,1080",
                      "--disable-blink-features=AutomationControlled"]
            )
            context = self.browser.new_context( # type: ignore
                viewport={"width": 1920, "height": 1080},
                user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                locale="tr-TR",
                timezone_id="Europe/Istanbul"
            )
            context.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
            self.page = context.new_page()
            self.page.set_default_timeout(120000) # type: ignore

    def _stop_browser(self):
        try:
            if self.page: self.page.close() # type: ignore
            if self.browser: self.browser.close() # type: ignore
            if self.playwright: self.playwright.stop() # type: ignore
        except Exception:
            pass

    def _fetch_campaign_urls(self, limit: Optional[int] = None) -> List[str]:
        print(f"📥 Fetching campaign list from {self.CAMPAIGNS_URL} ...")
        self.page.goto(self.CAMPAIGNS_URL, wait_until="networkidle", timeout=120000) # type: ignore
        time.sleep(3)

        soup = BeautifulSoup(self.page.content(), "html.parser") # type: ignore
        
        a_tags = soup.find_all('a', href=True)
        all_links = []
        
        for a in a_tags:
            href = a['href']
            if '/masterpassplus/' in href and href != '/masterpassplus' and href != '/masterpassplus/':
                if not href.startswith('http'):
                    full_url = urljoin(self.BASE_URL, href)
                else:
                    full_url = href
                all_links.append(full_url)

        unique_urls = list(dict.fromkeys(all_links))
        if limit:
            unique_urls = unique_urls[:limit] # type: ignore
            
        print(f"✅ Found {len(unique_urls)} campaigns")
        return unique_urls

    def _extract_campaign_data(self, url: str) -> Optional[Dict[str, Any]]:
        try:
            success = False
            for attempt in range(2):
                try:
                    self.page.goto(url, wait_until="networkidle", timeout=60000) # type: ignore
                    time.sleep(2)
                    success = True
                    break
                except Exception as e:
                    print(f"      ⚠️ Detail load attempt {attempt+1}/2 failed: {e}. Retrying...")
                    time.sleep(3)
            
            if not success:
                print(f"      ❌ Could not load detail page: {url}")
                return None
                
            soup = BeautifulSoup(self.page.content(), "html.parser") # type: ignore
            
            # Title
            title_el = soup.find('h1')
            title = self._clean(title_el.text) if title_el else "Başlık Yok"

            # Image
            image_url = None
            imgs = soup.find_all('img')
            for img in imgs:
                src = img.get('src')
                if src and ('kampanya' in src.lower() or 'offer' in src.lower() or 'campaign' in src.lower() or 'detail' in src.lower()):
                    if not src.startswith('data:'):
                        image_url = urljoin(self.BASE_URL, src)
                        break
            
            # Content Extraction
            conditions = []
            for div in soup.find_all('div'):
                text = div.get_text(separator='\\n').strip()
                if ('Koşul' in text or len(text) > 300) and 'Anasayfa' not in text[:50] and len(text) < 10000:
                    lines = [self._clean(t) for t in text.split('\\n') if len(self._clean(t)) > 10]
                    conditions.extend(lines)
                    break # Usually there's only one main long div
            
            if not conditions:
                print("   ⚠️ Could not easily find conditions block, checking p tags")
                for p in soup.find_all('p'):
                    if len(p.text) > 20: conditions.append(self._clean(p.text))

            full_text = f"Title: {title}\\n\\n" + "\\n".join(conditions)

            return {
                "title": title, 
                "image_url": image_url,
                "full_text": full_text,
                "conditions": conditions, 
                "source_url": url,
            } # type: ignore
        except Exception as e:
            print(f"   ⚠️ Error extracting {url}: {e}")
            return None

    def _clean(self, text: str) -> str:
        if not text:
            return ""
        return re.sub(r"\s+", " ", text.replace("\n", " ").replace("\r", "")).strip()

    def _to_title_case(self, text: str) -> str:
        if not text: return ""
        replacements = {"I": "ı", "İ": "i"}
        lower_text = text
        for k, v in replacements.items(): lower_text = lower_text.replace(k, v)
        lower_text = lower_text.lower()
        words = lower_text.split()
        capitalized = []
        for word in words:
            if not word: continue
            if word[0] == 'i': capitalized.append('İ' + word[1:]) # type: ignore
            elif word[0] == 'ı': capitalized.append('I' + word[1:]) # type: ignore
            else: capitalized.append(word.capitalize())
        return " ".join(capitalized)

    def _get_or_create_slug(self, title: str) -> str:
        base = re.sub(r'[^a-z0-9]+', '-', title.lower()).strip('-')
        slug = base
        counter = 1
        while self.db.query(Campaign).filter(Campaign.slug == slug).first():
            slug = f"{base}-{counter}"
            counter += 1
        return slug

    def _filter_conditions(self, conditions: List[str]) -> List[str]:
        blacklist = [
            "değişiklik yapma hakkı", 
            "saklı tutar", 
            "sorumlu tutulamaz", 
            "sorumluluk kabul edilmez",
            "durdurma hakkına sahiptir",
            "kullanım koşulları"
        ]
        clean = []
        for c in conditions:
            c_lower = c.lower()
            if any(b in c_lower for b in blacklist):
                continue
            clean.append(c)
        return clean

    def _process_campaign(self, url: str, force: bool = False) -> str:
        # --- 1. DB Check FIRST (Early Exit) ---
        if not force:
            existing = self.db.query(Campaign).filter(
                Campaign.tracking_url == url,
                Campaign.card_id == self.card_id
            ).first()
            if existing:
                print(f"      ⏭️  Skipped (Already exists)")
                return "skipped"

        print(f"   🔍 Processing: {url}")
        
        data = self._extract_campaign_data(url)
        if not data:
            print("      ⏭️  Skipped (Parse Error)")
            return "skipped"

        try:
            ai_data = self.parser.parse_campaign_data(
                raw_text=data["full_text"], 
                bank_name="Masterpass", 
                title=data["title"],
                tracking_url=url,
                force=force
            ) or {}
        except Exception as e:
            self.db.rollback()
            print(f"      ⚠️ AI parse error: {e}")
            ai_data = {}
            
        print(f"      🧠 AI Data: {json.dumps(ai_data, ensure_ascii=False)}")

        try:
            raw_title = ai_data.get("title") or data.get("title") or ""
            formatted_title = self._to_title_case(raw_title)
            slug = self._get_or_create_slug(formatted_title)
            
            ai_cat = ai_data.get("sector", "Diğer") # type: ignore
            sector = self.db.query(Sector).filter(Sector.slug == ai_cat).first() # type: ignore
            if not sector:
                sector = self.db.query(Sector).filter(Sector.slug == 'diger').first()

            start_date, end_date = None, None
            for key in ["start_date", "end_date"]:
                val = ai_data.get(key) # type: ignore
                if val:
                    try:
                        dt = datetime.strptime(val, "%Y-%m-%d")
                        if key == "start_date":
                            start_date = dt
                        else:
                            end_date = dt
                    except Exception:
                        pass

            conds = ai_data.get("conditions", []) # type: ignore
            if isinstance(conds, str):
                conds = [c.strip() for c in conds.split("\n") if c.strip()]
            part = ai_data.get("participation") # type: ignore
            if part:
                conds.insert(0, f"KATILIM: {part}")
                
            conds = self._filter_conditions(conds)
            
            # Prevent empty text formatting array issues by using newlines instead of string literals
            final_conditions = "\n".join(conds) if conds else "\n".join(data["conditions"])

            cards_raw = ai_data.get("cards", []) # type: ignore
            if isinstance(cards_raw, str):
                cards_raw = [c.strip() for c in cards_raw.split(",") if c.strip()]
            eligible_cards_str = ", ".join(cards_raw) or "Mastercard"

            existing = self.db.query(Campaign).filter(
                Campaign.tracking_url == url,
                Campaign.card_id == self.card_id
            ).first()

            if existing:
                print(f"      🔄 Updating existing campaign: {existing.title}")
                existing.sector_id = sector.id if sector else None # type: ignore
                existing.title = formatted_title
                existing.description = ai_data.get("description") or formatted_title # type: ignore
                existing.reward_text = ai_data.get("reward_text") # type: ignore
                existing.reward_value = ai_data.get("reward_value") # type: ignore
                existing.reward_type = ai_data.get("reward_type") # type: ignore
                existing.conditions = final_conditions
                existing.eligible_cards = eligible_cards_str
                if data["image_url"]:
                    existing.image_url = data["image_url"]
                existing.start_date = start_date or existing.start_date
                existing.end_date = end_date or existing.end_date
                existing.updated_at = func.now()
                self.db.commit()
                campaign = existing
            else:
                campaign = Campaign( # type: ignore
                    card_id=self.card_id, sector_id=sector.id if sector else None,
                    slug=slug, title=formatted_title,
                    description=ai_data.get("description") or formatted_title, # type: ignore
                    reward_text=ai_data.get("reward_text"), # type: ignore
                    reward_value=ai_data.get("reward_value"), # type: ignore
                    reward_type=ai_data.get("reward_type"), # type: ignore
                    conditions=final_conditions,
                    eligible_cards=eligible_cards_str,
                    image_url=data.get("image_url"),
                    start_date=start_date, end_date=end_date,
                    is_active=True, tracking_url=url,
                    created_at=func.now(), updated_at=func.now(),
                )
                self.db.add(campaign)
                self.db.commit()
            print(f"      ✅ Saved")

            # Brands
            for b_name in ai_data.get("brands", []): # type: ignore
                if len(b_name) < 2:
                    continue
                # Hardcoded ignore because AI hallucinated sometimes
                if b_name.lower() in ["masterpass", "mastercard"]: # type: ignore
                    continue
                    
                b_slug = re.sub(r'[^a-z0-9]+', '-', b_name.lower()).strip('-') # type: ignore

                try:
                    brand = self.db.query(Brand).filter(
                        (Brand.slug == b_slug) | (Brand.name.ilike(b_name))
                    ).first()
                    if not brand:
                        brand = Brand(name=self._to_title_case(b_name), slug=b_slug) # type: ignore
                        self.db.add(brand)
                        self.db.commit()
                except Exception as e:
                    self.db.rollback()
                    print(f"      ⚠️ Brand save failed for {b_name}: {e}")
                    continue

                try:
                    link = self.db.query(CampaignBrand).filter(
                        CampaignBrand.campaign_id == campaign.id,
                        CampaignBrand.brand_id == brand.id
                    ).first()
                    if not link:
                        self.db.add(CampaignBrand(campaign_id=campaign.id, brand_id=brand.id))
                        self.db.commit()
                except Exception as e:
                    self.db.rollback()
                    print(f"      ⚠️ CampaignBrand link failed: {e}")
                    continue

            return "saved"
        except Exception as e:
            self.db.rollback()
            print(f"      ❌ Save failed: {e}")
            return "error"

    def run(self, limit: Optional[int] = None, urls: Optional[List[str]] = None, force: bool = False):
        """Main entry point."""
        specific_urls = urls
        try:
            print("🚀 Starting Masterpass Scraper (Playwright)...")
            self._start_browser()
            
            if self.db:
                self.db.commit()
                # We do NOT close the DB here otherwise we can't save later
                
            urls = self._fetch_campaign_urls(limit=limit)
            
            # Filter if specific URLs provided
            if specific_urls:
                 urls = [u for u in urls if u in specific_urls]

            print(f"   🎯 Processing {len(urls)} campaigns...")
            
            success, skipped, failed = 0, 0, 0
            for i, url in enumerate(urls, 1):
                print(f"\n[{i}/{len(urls)}] {url}")
                try:
                    res = self._process_campaign(url, force=force)
                    if res == "saved":
                        success += 1 # type: ignore
                    elif res == "skipped":
                        skipped += 1 # type: ignore
                    else:
                        failed += 1 # type: ignore
                except Exception as e:
                    print(f"      ❌ Failed to process: {e}")
                    if self.db:
                        self.db.rollback()
                    failed += 1 # type: ignore
                time.sleep(1)
            print(f"\n🏁 Finished. {len(urls)} found, {success} saved, {skipped} skipped, {failed} errors")
            
            # Log successful or partial execution
            if self.db:
                log_scraper_execution(
                    db=self.db,
                    scraper_name="masterpass",
                    status="SUCCESS" if failed == 0 else ("PARTIAL" if success > 0 else "FAILED"),
                    total_found=len(urls),
                    total_saved=success,
                    total_skipped=skipped,
                    total_failed=failed
                )
                
        except Exception as e:
            print(f"❌ Scraper error: {e}")
            if self.db:
                error_details = {"traceback": traceback.format_exc(), "error": str(e)}
                log_scraper_execution(
                    db=self.db,
                    scraper_name="masterpass",
                    status="FAILED",
                    error_details=error_details
                )
            raise
        finally:
            self._stop_browser()
            if self.db:
                self.db.close()


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, help="Limit campaigns")
    parser.add_argument("--force", action="store_true", help="Force update")
    parser.add_argument("--urls", nargs='*', help="Specific URLs to scrape")
    args = parser.parse_args()
    
    scraper = MasterpassScraper()
    scraper.run(limit=args.limit, urls=args.urls, force=args.force)
