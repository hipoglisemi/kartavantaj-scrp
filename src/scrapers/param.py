# pyre-ignore-all-errors
# type: ignore

"""
ParamKart Scraper
Powered by Playwright (Handles Lazy Loading via Scrolling)
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


SECTOR_MAP = {
    "Market & Gıda": "Market", "Giyim & Aksesuar": "Giyim",
    "Restoran & Kafe": "Restoran & Kafe", "Seyahat": "Seyahat",
    "Turizm & Konaklama": "Seyahat", "Elektronik": "Elektronik",
    "Mobilya & Dekorasyon": "Mobilya & Dekorasyon",
    "Kozmetik & Sağlık": "Kozmetik & Sağlık", "E-Ticaret": "E-Ticaret",
    "Otomotiv": "Otomotiv", "Sigorta": "Sigorta", "Eğitim": "Eğitim",
    "Kültür & Sanat": "Kültür & Sanat", "Eğlence": "Kültür & Sanat",
    "Diğer": "Diğer",
}

class ParamScraper:
    """Param scraper - Playwright based (Handles infinite scroll on list and details)"""

    BASE_URL = "https://param.com.tr"
    CAMPAIGNS_URL = "https://param.com.tr/tum-avantajlar"
    BANK_NAME = "Param"
    BANK_SLUG = "param"

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
            print(f"⚠️  Param not found in DB, creating...")
            # We don't have a guaranteed stable logo for param right now, but we can set a basic one if needed
            bank = Bank(name=self.BANK_NAME, slug=self.BANK_SLUG)
            self.db.add(bank)
            self.db.commit()
        print(f"✅ Bank: {bank.name} (ID: {bank.id})")

        card = self.db.query(Card).filter(Card.slug == 'paramkart').first()
        if not card:
            print(f"⚠️  Card 'paramkart' not found, creating...")
            card = Card(bank_id=bank.id, name='ParamKart', slug='paramkart', is_active=True)
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
            if self.browser:
                self.browser.close() # type: ignore
            if self.playwright:
                self.playwright.stop() # type: ignore
        except Exception:
            pass

    def _fetch_campaign_urls(self, limit: Optional[int] = None) -> List[str]:
        print(f"📥 Fetching campaign list from {self.CAMPAIGNS_URL} ...")
        self.page.goto(self.CAMPAIGNS_URL, wait_until="domcontentloaded", timeout=120000) # type: ignore
        time.sleep(5)

        print("   ⏬ Scrolling down to load all campaigns...")
        last_height = self.page.evaluate("document.body.scrollHeight") # type: ignore
        scroll_count = 0
        while scroll_count < 30:
            self.page.evaluate("window.scrollTo(0, document.body.scrollHeight);") # type: ignore
            time.sleep(2.5)
            new_height = self.page.evaluate("document.body.scrollHeight") # type: ignore
            if new_height == last_height:
                break
            last_height = new_height
            scroll_count += 1
            if limit:
                # Early break if limit reached
                soup = BeautifulSoup(self.page.content(), "html.parser") # type: ignore
                count = len(set([a['href'] for a in soup.select('a[href^="/avantajlar/"]') if a['href'] != '/avantajlar/'])) # type: ignore
                if count >= (limit or 0): # type: ignore
                    break

        print(f"   ⏬ Scrolled {scroll_count} times.")
        
        soup = BeautifulSoup(self.page.content(), "html.parser") # type: ignore
        all_links = []
        
        for a in soup.select('a[href^="/avantajlar/"]'):
            href = a['href']
            if href != '/avantajlar/' and "tum-avantajlar" not in href:
                full_url = urljoin(self.BASE_URL, href)
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
                    self.page.goto(url, wait_until="domcontentloaded", timeout=60000) # type: ignore
                    success = True
                    break
                except Exception as e:
                    print(f"      ⚠️ Detail load attempt {attempt+1}/2 failed: {e}. Retrying...")
                    time.sleep(3)
            
            if not success:
                print(f"      ❌ Could not load detail page: {url}")
                return None
                
            # Click accept cookies banner if it exists and blocks view
            try:
                btn = self.page.query_selector('button[id*="cookie-accept"], button[class*="cookie"]') # type: ignore
                if btn and btn.is_visible(): # type: ignore
                    btn.click() # type: ignore
            except:
                pass

            # Scroll to bottom of detail page as user requested to trigger lazy loading of content
            print("      ⏬ Scrolling detail page...")
            self.page.evaluate("""async () => { # type: ignore
                await new Promise((resolve) => {
                    let totalHeight = 0;
                    let distance = 300;
                    let timer = setInterval(() => {
                        let scrollHeight = document.body.scrollHeight;
                        window.scrollBy(0, distance);
                        totalHeight += distance;
                        if(totalHeight >= scrollHeight){
                            clearInterval(timer);
                            resolve();
                        }
                    }, 100);
                });
            }""")
            time.sleep(1.5)

            soup = BeautifulSoup(self.page.content(), "html.parser") # type: ignore
            
            # Title
            title_el = soup.find('h1')
            title = self._clean(title_el.text) if title_el else "Başlık Yok"

            # Image
            image_url = None
            img_el = soup.select_one("img.img-fluid, .avantaj-resim img, img.hero-image")
            if img_el and img_el.get("src"):
                image_url = urljoin(self.BASE_URL, img_el["src"])
            
            # Date extract from full text later if needed, but sometimes it is in specific fields
            
            # Content Extraction
            conditions = []
            
            # Param often uses lists for "Temel Bilgi" and "Genel Bilgi"
            for ul in soup.find_all('ul'):
                for li in ul.find_all('li'):
                    text = self._clean(li.text)
                    if text and len(text) > 10 and not text.startswith("Bizi Takip Edin"):
                        conditions.append(text)
            
            # If nothing found in lists, look for paragraphs under specific sections
            if not conditions:
                for p in soup.find_all('p'):
                    text = self._clean(p.text)
                    if text and len(text) > 20 and "Bizi Takip Edin" not in text:
                        conditions.append(text)
                        
            # Assemble full text for AI
            full_text = f"Title: {title}\\n\\n" + "\\n".join(conditions)

            return {
                "title": title, 
                "image_url": image_url,
                "full_text": full_text,
                "conditions": conditions, 
                "source_url": url,
            }
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
            "yazım hataları", 
            "sorumlu tutulamaz", 
            "sorumluluk kabul edilmez",
            "durdurma hakkına sahiptir"
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
                Campaign.tracking_url == url, Campaign.card_id == self.card_id
            ).first()
            
            if existing:
                print("   ⏭️  Skipped (Already exists)")
                return "skipped"

        print(f"🔍 Processing: {url}")
        data = self._extract_campaign_data(url)
        if not data:
            print("   ⏭️  Skipped (Parse Error)")
            return "skipped"

        try:
            ai_data = self.parser.parse_campaign_data(
                raw_text=data["full_text"], 
                bank_name=self.BANK_NAME, 
                title=data["title"],
                tracking_url=url,
                force=force
            ) or {}
        except Exception as e:
            self.db.rollback()
            print(f"   ⚠️ AI parse error: {e}")
            ai_data = {}
            
        print(f"   🧠 AI Data: {json.dumps(ai_data, ensure_ascii=False)}")

        try:
            raw_title = ai_data.get("title") or data.get("title") or "" # type: ignore
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
            final_conditions = "\n".join(conds) if conds else "\n".join(data["conditions"])

            cards_raw = ai_data.get("cards", []) # type: ignore
            if isinstance(cards_raw, str):
                cards_raw = [c.strip() for c in cards_raw.split(",") if c.strip()]
            eligible_cards_str = ", ".join(cards_raw) or "ParamKart"

            existing = self.db.query(Campaign).filter(
                Campaign.tracking_url == url, Campaign.card_id == self.card_id
            ).first()

            if existing:
                print(f"   🔄 Updating existing campaign: {existing.title}")
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
                print(f"   ✅ Updated: {existing.title[:50]}")
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
                print(f"   ✅ Saved: {campaign.title[:50]}")

            # Brands
            for b_name in ai_data.get("brands", []): # type: ignore
                if len(b_name) < 2:
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
                    print(f"   ⚠️ Brand save failed for {b_name}: {e}")
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
                    print(f"   ⚠️ CampaignBrand link failed: {e}")
                    continue

            return "saved"
        except Exception as e:
            self.db.rollback()
            print(f"   ❌ Save failed: {e}")
            traceback.print_exc()
            return "error"

    def run(self, limit: Optional[int] = None, urls: Optional[List[str]] = None, force: bool = False):
        """Main entry point."""
        try:
            print("🚀 Starting Param Scraper (Playwright)...")
            self._start_browser()
            
            if self.db:
                self.db.commit()
                
            found_urls = self._fetch_campaign_urls(limit=limit)
            
            # Filter if specific URLs provided
            if urls:
                final_urls = [u for u in found_urls if u in urls]
            else:
                final_urls = found_urls

            print(f"   🎯 Processing {len(final_urls)} campaigns...")
            
            success, skipped, failed = 0, 0, 0
            for i, url in enumerate(final_urls, 1):
                print(f"\n[{i}/{len(final_urls)}]")
                try:
                    res = self._process_campaign(url, force=force)
                    if res == "saved":
                        success += 1 # type: ignore
                    elif res == "skipped":
                        skipped += 1 # type: ignore
                    else:
                        failed += 1 # type: ignore
                except Exception as e:
                    print(f"❌ Error: {e}")
                    if self.db:
                        self.db.rollback()
                    failed += 1 # type: ignore
                time.sleep(1)
            print(f"\n🏁 Finished. {len(final_urls)} found, {success} saved, {skipped} skipped, {failed} errors")
            
            # Log execution
            if self.db:
                log_scraper_execution(
                    db=self.db,
                    scraper_name="param",
                    status="SUCCESS" if failed == 0 else ("PARTIAL" if success > 0 else "FAILED"),
                    total_found=len(final_urls),
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
                    scraper_name="param",
                    status="FAILED",
                    error_details=error_details
                )
            raise
        finally:
            self._stop_browser()
            self.db.close()


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, help="Limit campaigns")
    parser.add_argument("--force", action="store_true", help="Force update")
    parser.add_argument("--urls", nargs='*', help="Specific URLs to scrape")
    args = parser.parse_args()
    
    scraper = ParamScraper()
    scraper.run(limit=args.limit, urls=args.urls, force=args.force)
