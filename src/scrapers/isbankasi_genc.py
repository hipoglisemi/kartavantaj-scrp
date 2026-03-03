"""
İşbankası Maximum Genç Scraper
Powered by Playwright (GitHub Actions compatible, Cloudflare-resistant)
"""

import os
import sys
import time
import re
import uuid
import traceback
from datetime import datetime
from typing import Optional, Dict, Any, List
from urllib.parse import urljoin
from bs4 import BeautifulSoup

current_dir = os.path.dirname(os.path.abspath(__file__))
project_root = os.path.dirname(os.path.dirname(current_dir))
if project_root not in sys.path:
    sys.path.append(project_root)

try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(project_root, '.env'))
except Exception:
    pass
try:
    with open(os.path.join(project_root, '.env'), 'r') as f:
        for line in f:
            if line.strip() and not line.startswith('#') and '=' in line:
                k, v = line.strip().split('=', 1)
                if k not in os.environ:
                    os.environ[k] = v.strip('"\'')
except Exception:
    pass

from sqlalchemy import create_engine, Column, Integer, String, Boolean, DateTime, Date, Numeric, ForeignKey
from sqlalchemy.orm import sessionmaker, relationship, declarative_base
from sqlalchemy.dialects.postgresql import UUID

# AIParser is lazy-imported in __init__ to avoid google.generativeai hang
AIParser = None

DATABASE_URL = os.environ.get("DATABASE_URL")
Base = declarative_base()

class Bank(Base):
    __tablename__ = 'banks'
    id = Column(Integer, primary_key=True)
    name = Column(String)
    slug = Column(String)
    cards = relationship("Card", back_populates="bank")

class Card(Base):
    __tablename__ = 'cards'
    id = Column(Integer, primary_key=True)
    bank_id = Column(Integer, ForeignKey('banks.id'))
    name = Column(String)
    slug = Column(String)
    is_active = Column(Boolean, default=True)
    bank = relationship("Bank", back_populates="cards")
    campaigns = relationship("Campaign", back_populates="card")

class Sector(Base):
    __tablename__ = 'sectors'
    id = Column(Integer, primary_key=True)
    name = Column(String)
    slug = Column(String)
    campaigns = relationship("Campaign", back_populates="sector")

class Brand(Base):
    __tablename__ = 'brands'
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name = Column(String)
    slug = Column(String)
    campaigns = relationship("CampaignBrand", back_populates="brand")

class CampaignBrand(Base):
    __tablename__ = 'campaign_brands'
    campaign_id = Column(Integer, ForeignKey('campaigns.id'), primary_key=True)
    brand_id = Column(UUID(as_uuid=True), ForeignKey('brands.id'), primary_key=True)
    brand = relationship("Brand", back_populates="campaigns")
    campaign = relationship("Campaign", back_populates="brands")

class Campaign(Base):
    __tablename__ = 'campaigns'
    id = Column(Integer, primary_key=True)
    card_id = Column(Integer, ForeignKey('cards.id'))
    sector_id = Column(Integer, ForeignKey('sectors.id'))
    slug = Column(String)
    title = Column(String)
    description = Column(String)
    reward_text = Column(String)
    reward_value = Column(Numeric)
    reward_type = Column(String)
    conditions = Column(String)
    eligible_cards = Column(String)
    image_url = Column(String)
    start_date = Column(Date)
    end_date = Column(Date)
    is_active = Column(Boolean, default=True)
    tracking_url = Column(String)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)
    ai_marketing_text = Column(String)
    card = relationship("Card", back_populates="campaigns")
    sector = relationship("Sector", back_populates="campaigns")
    brands = relationship("CampaignBrand", back_populates="campaign")


SECTOR_MAP = {
    "Market & Gıda": "Market", "Giyim & Aksesuar": "Giyim",
    "Restoran & Kafe": "Restoran & Kafe", "Seyahat": "Seyahat",
    "Turizm & Konaklama": "Seyahat", "Elektronik": "Elektronik",
    "Mobilya & Dekorasyon": "Mobilya & Dekorasyon",
    "Kozmetik & Sağlık": "Kozmetik & Sağlık", "E-Ticaret": "E-Ticaret",
    "Otomotiv": "Otomotiv", "Sigorta": "Sigorta", "Eğitim": "Eğitim",
    "Diğer": "Diğer",
}


class IsbankMaximumGencScraper:
    """İşbankası Maximum Genç scraper - Playwright based"""

    BASE_URL = "https://www.maximumgenc.com.tr"
    CAMPAIGNS_URL = "https://www.maximumgenc.com.tr/kampanyalar"
    BANK_NAME = "İşbankası"
    CARD_SLUG = "maximum-genc"
    DEFAULT_IMAGE_URL = "https://www.maximumgenc.com.tr/_assets/images/logo.png"

    def __init__(self):
        if not DATABASE_URL:
            raise ValueError("DATABASE_URL is not set")
        self.engine = create_engine(DATABASE_URL, pool_pre_ping=True, pool_recycle=300)
        Session = sessionmaker(bind=self.engine)
        self.db = Session()
        
        # Lazy import of AIParser to avoid google.generativeai hanging at module import time
        try:
            from src.services.ai_parser import AIParser as _AIParser
            print("[DEBUG] AIParser lazy-imported via src.services")
        except ImportError:
            try:
                from services.ai_parser import AIParser as _AIParser
                print("[DEBUG] AIParser lazy-imported via services")
            except ImportError as e:
                print(f"[DEBUG] AIParser import FAILED: {e}")
                raise
        self.parser = _AIParser()
        print("[DEBUG] AIParser initialized")

        self.page = None
        self.browser = None
        self.playwright = None
        self._init_card()

    def _init_card(self):
        bank = self.db.query(Bank).filter(
            Bank.slug.in_([
                'i-sbankasi',   # gerçek DB slug
                'isbank', 'isbankasi', 'is-bankasi', 'turkiye-is-bankasi',
            ])
        ).first()
        if not bank:
            bank = self.db.query(Bank).filter(
                Bank.name.ilike('%İş Bank%') | Bank.name.ilike('%İşbank%')
            ).first()
        if not bank:
            print(f"⚠️  İşbankası not found in DB, creating...")
            bank = Bank(name='İş Bankası', slug='isbank')
            self.db.add(bank)
            self.db.commit()
        print(f"✅ Bank: {bank.name} (ID: {bank.id}, slug: {bank.slug})")

        card = self.db.query(Card).filter(
            Card.slug.in_([
                'maximum-genc', 'maximum-genc-card', 'isbank-maximum-genc',
                'isbankasi-maximum-genc', 'maximumgenc',
            ])
        ).first()
        if not card:
            card = self.db.query(Card).filter(
                Card.name.ilike('%Maximum Gen%'),
                Card.bank_id == bank.id
            ).first()
        if not card:
            print(f"⚠️  Card 'maximum-genc' not found, creating...")
            card = Card(bank_id=bank.id, name='Maximum Genç Card', slug='maximum-genc', is_active=True)
            self.db.add(card)
            self.db.commit()
        self.card_id = card.id
        print(f"✅ Card: {card.name} (ID: {self.card_id}, slug: {card.slug})")



    def _start_browser(self):
        from playwright.sync_api import sync_playwright
        self.playwright = sync_playwright().start()
        
        is_ci = os.getenv("GITHUB_ACTIONS") == "true" or os.getenv("CI") == "true"
        connected = False

        if not is_ci:
            try:
                print("   🔌 Attempting to connect to local Chrome debug instance at http://localhost:9222...")
                self.browser = self.playwright.chromium.connect_over_cdp("http://localhost:9222")
                connected = True
                print("   ✅ Connected to local existing Chrome instance")
                
                # Use existing context if available
                if len(self.browser.contexts) > 0:
                    context = self.browser.contexts[0]
                else:
                    context = self.browser.new_context()
                    
                self.page = context.new_page()
                self.page.set_default_timeout(120000)
                return
            except Exception as e:
                print(f"   ⚠️  Could not connect to debug Chrome, launching headless... ({e})")
                
        if not connected:
            self.browser = self.playwright.chromium.launch(
                headless=True,
                args=["--no-sandbox", "--disable-setuid-sandbox",
                      "--disable-dev-shm-usage", "--disable-gpu", "--window-size=1920,1080",
                      "--disable-blink-features=AutomationControlled",
                      "--disable-extensions", "--disable-web-security"]
            )
            context = self.browser.new_context(
                viewport={"width": 1920, "height": 1080},
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
                locale="tr-TR",
                timezone_id="Europe/Istanbul",
                extra_http_headers={"Accept-Language": "tr-TR,tr;q=0.9,en;q=0.8"}
            )
            context.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
            self.page = context.new_page()
            self.page.set_default_timeout(120000)
            print("✅ Playwright browser started.")

    def _stop_browser(self):
        try:
            if self.browser:
                self.browser.close()
            if self.playwright:
                self.playwright.stop()
        except Exception:
            pass

    def _fetch_campaign_urls(self, limit: Optional[int] = None) -> tuple[List[str], List[str]]:
        print(f"📥 Fetching campaign list from {self.CAMPAIGNS_URL}...")
        self.page.goto(self.CAMPAIGNS_URL, wait_until="domcontentloaded", timeout=120000)
        time.sleep(5)

        scroll_count = 0
        while scroll_count < 100:
            if limit:
                soup = BeautifulSoup(self.page.content(), "html.parser")
                items = soup.find_all("div", class_="item")
                count = 0
                for item in items:
                    a_tag = item.find("a", href=True)
                    if a_tag:
                        href = a_tag["href"].lower()
                        if "tum-kampanya" not in href and "/kampanyalar/" not in href and href.startswith("/"):
                            count += 1
                            
                if count >= limit:
                    break

            self.page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            time.sleep(1)

            btn = self.page.query_selector(".show-more-opportunity")
            if btn and btn.is_visible():
                btn.scroll_into_view_if_needed()
                time.sleep(1)
                try:
                    btn.click()
                except Exception:
                    self.page.evaluate("element => element.click()", btn)
                time.sleep(3)
                scroll_count += 1
                print(f"   ⏬ Loaded more campaigns (round {scroll_count})...")
            else:
                break

        soup = BeautifulSoup(self.page.content(), "html.parser")
        
        all_links = []
        expired_links = []
        
        items = soup.find_all("div", class_="item")
        for item in items:
            a_tag = item.find("a", href=True)
            if not a_tag:
                continue
                
            href = a_tag["href"].lower()
            if "tum-kampanya" not in href and "/kampanyalar/" not in href and href.startswith("/"):
                full_url = urljoin(self.BASE_URL, a_tag["href"])
                
                # Sona ermiş kampanya tespiti
                parent_text = item.get_text(separator=" ", strip=True).lower()
                
                if "sona ermiştir" in parent_text or "bitmiştir" in parent_text or "sona erdi" in parent_text or "süresi doldu" in parent_text:
                    expired_links.append(full_url)
                else:
                    all_links.append(full_url)

        unique_urls = list(dict.fromkeys(all_links))
        unique_expired = list(dict.fromkeys(expired_links))
        if limit:
            unique_urls = unique_urls[:limit]
            
        print(f"✅ Found {len(unique_urls)} active campaigns, and {len(unique_expired)} expired campaigns")
        return unique_urls, unique_expired

    def _extract_campaign_data(self, url: str) -> Optional[Dict[str, Any]]:
        try:
            success = False
            for attempt in range(3):
                try:
                    self.page.goto(url, wait_until="domcontentloaded", timeout=60000)
                    success = True
                    break
                except Exception as e:
                    print(f"      ⚠️ Detail load attempt {attempt+1}/3 failed: {e}. Retrying...")
                    time.sleep(3 + attempt * 2)
            
            if not success:
                print(f"      ❌ Could not load detail page after 3 attempts: {url}")
                return None
                
            self.page.evaluate("window.scrollTo(0, 500)")
            time.sleep(2)

            soup = BeautifulSoup(self.page.content(), "html.parser")
            title_el = soup.select_one("h1.color-purple, h1")
            title = self._clean(title_el.text) if title_el else "Başlık Yok"

            if "gecmis" in url or "geçmiş" in title.lower():
                return None

            # Image
            image_url = None
            img_el = soup.select_one(".detail-img img") or soup.select_one("section img")
            if img_el:
                src = img_el.get("data-original") or img_el.get("data-src") or img_el.get("src")
                if src and not src.startswith("data:"):
                    image_url = urljoin(self.BASE_URL, src)
            if not image_url:
                banner = soup.select_one("section.banner, div.banner")
                if banner and "style" in banner.attrs:
                    match = re.search(r"url\(['\"]?(.*?)['\"]?\)", banner["style"])
                    if match:
                        image_url = urljoin(self.BASE_URL, match.group(1))
            if not image_url:
                image_url = self.DEFAULT_IMAGE_URL

            # Date
            date_text = ""
            date_el = soup.select_one("div.mobile-date, .date, .campaign-date")
            if date_el:
                spans = date_el.find_all("span")
                if len(spans) >= 2:
                    date_text = f"{self._clean(spans[0].text)} - {self._clean(spans[1].text)}"
                else:
                    date_text = self._clean(date_el.text)
            
            if not date_text:
                full_text = soup.get_text()
                m = re.search(r"(\d{1,2}\s+\w+\s+\d{4})\s*-\s*(\d{1,2}\s+\w+\s+\d{4})", full_text)
                if m:
                    date_text = m.group(0)

            # Content
            content_div = soup.select_one("div.content-part, .detail-text, .campaign-content, section .container")
            conditions = []
            full_text = ""
            if content_div:
                raw = content_div.get_text("\n", strip=True)
                conditions = [self._clean(l) for l in raw.split("\n") if len(self._clean(l)) > 20]
                full_text = " ".join(conditions)
            else:
                full_text = self._clean(soup.get_text())[:1000]

            conditions = [c for c in conditions if not c.startswith("Copyright")]

            return {
                "title": title, "image_url": image_url,
                "date_text": date_text, "full_text": full_text,
                "conditions": conditions, "source_url": url,
            }
        except Exception as e:
            print(f"   ⚠️ Error extracting {url}: {e}")
            return None

    def _parse_date(self, date_text: str, is_end: bool = False) -> Optional[str]:
        if not date_text:
            return None
        text = date_text.replace("İ", "i").lower()
        months = {
            "ocak": "01", "şubat": "02", "mart": "03", "nisan": "04",
            "mayıs": "05", "haziran": "06", "temmuz": "07", "ağustos": "08",
            "eylül": "09", "ekim": "10", "kasım": "11", "aralık": "12",
        }
        try:
            m = re.search(r"(\d{1,2})\s*([a-zğüşıöç]+)?\s*-\s*(\d{1,2})\s*([a-zğüşıöç]+)\s*(\d{4})", text)
            if m:
                day1, month1, day2, month2, year = m.groups()
                if not month1:
                    month1 = month2
                if is_end:
                    return f"{year}-{months.get(month2, '12')}-{str(day2).zfill(2)}"
                return f"{year}-{months.get(month1, '01')}-{str(day1).zfill(2)}"
        except Exception:
            pass
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
            if word[0] == 'i': capitalized.append('İ' + word[1:])
            elif word[0] == 'ı': capitalized.append('I' + word[1:])
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

    def _process_campaign(self, url: str) -> str:
        existing = self.db.query(Campaign).filter(
            Campaign.tracking_url == url, Campaign.card_id == self.card_id
        ).first()
        if existing:
            print("   ⏭️  Skipped (Already exists)")
            return "skipped"

        print(f"🔍 Processing: {url}")
        data = self._extract_campaign_data(url)
        if not data:
            print("   ⏭️  Skipped")
            return "skipped"

        try:
            ai_data = self.parser.parse_campaign_data(
                raw_text=data["full_text"], bank_name=self.BANK_NAME
            ) or {}
        except Exception as e:
            self.db.rollback()
            print(f"   ⚠️ AI parse error: {e}")
            ai_data = {}

        try:
            raw_title = ai_data.get("title") or data.get("title") or ""
            formatted_title = self._to_title_case(raw_title)
            slug = self._get_or_create_slug(formatted_title)
            ai_cat = ai_data.get("sector", "Diğer")
            sector = self.db.query(Sector).filter(Sector.name == SECTOR_MAP.get(ai_cat, "Diğer")).first()
            if not sector:
                sector = self.db.query(Sector).filter(Sector.slug == 'diger').first()

            start_date, end_date = None, None
            for ai_key, is_end in [("start_date", False), ("end_date", True)]:
                val = ai_data.get(ai_key)
                dt = None
                if val:
                    try:
                        dt = datetime.strptime(val, "%Y-%m-%d")
                    except Exception:
                        pass
                if not dt:
                    parsed = self._parse_date(data["date_text"], is_end=is_end)
                    if parsed:
                        try:
                            dt = datetime.strptime(parsed, "%Y-%m-%d")
                        except Exception:
                            pass
                if ai_key == "start_date":
                    start_date = dt
                else:
                    end_date = dt

            conds = ai_data.get("conditions", [])
            part = ai_data.get("participation")
            if part and "Detayları İnceleyin" not in part:
                conds.insert(0, f"KATILIM: {part}")

            campaign = Campaign(
                card_id=self.card_id, sector_id=sector.id if sector else None,
                slug=slug, title=formatted_title,
                description=ai_data.get("description") or data["title"][:200],
                reward_text=ai_data.get("reward_text"),
                reward_value=ai_data.get("reward_value"),
                reward_type=ai_data.get("reward_type"),
                conditions="\n".join(conds),
                eligible_cards=", ".join(ai_data.get("cards", [])) or None,
                image_url=data["image_url"],
                start_date=start_date, end_date=end_date,
                is_active=True, tracking_url=url,
                created_at=datetime.utcnow(), updated_at=datetime.utcnow(),
            )
            self.db.add(campaign)
            self.db.commit()

            # Brands
            for b_name in ai_data.get("brands", []):
                if len(b_name) < 2:
                    continue
                b_slug = re.sub(r'[^a-z0-9]+', '-', b_name.lower()).strip('-')

                try:
                    brand = self.db.query(Brand).filter(
                        (Brand.slug == b_slug) | (Brand.name.ilike(b_name))
                    ).first()
                    if not brand:
                        brand = Brand(name=self._to_title_case(b_name), slug=b_slug)
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

            print(f"   ✅ Saved: {campaign.title[:50]}")
            return "saved"
        except Exception as e:
            self.db.rollback()
            print(f"   ❌ Save failed: {e}")
            traceback.print_exc()
            return "error"

    def run(self, limit: Optional[int] = None):
        try:
            print("🚀 Starting İşbankası Maximum Genç Scraper (Playwright)...")
            self._start_browser()
            
            # Close DB session to prevent idle connection timeout during long Playwright scroll
            if self.db:
                self.db.commit()
                self.db.close()
                
            active_urls, expired_urls = self._fetch_campaign_urls(limit=limit)
            
            # Evaluate expired campaigns logic
            if expired_urls:
                print(f"🛑 Found {len(expired_urls)} expired campaigns on list page. Checking DB for early end...")
                for e_url in expired_urls:
                    try:
                        existing = self.db.query(Campaign).filter(
                            Campaign.tracking_url == e_url,
                            Campaign.card_id == self.card_id,
                            Campaign.is_active == True
                        ).first()
                        if existing:
                            print(f"   🛑 Desactivating expired campaign in DB: {existing.title}")
                            existing.is_active = False
                            self.db.commit()
                    except Exception as e:
                        if self.db:
                            self.db.rollback()
                        print(f"   ⚠️ Could not update expired campaign {e_url}: {e}")
                        
            urls = active_urls
            success, skipped, failed = 0, 0, 0
            error_details = []
            for i, url in enumerate(urls, 1):
                print(f"\n[{i}/{len(urls)}]")
                try:
                    res = self._process_campaign(url)
                    if res == "saved":
                        success += 1
                    elif res == "skipped":
                        skipped += 1
                    else:
                        failed += 1
                        error_details.append({"url": url, "error": "Unknown DB failure"})
                except Exception as e:
                    print(f"❌ Error: {e}")
                    failed += 1
                    error_details.append({"url": url, "error": str(e)})
                time.sleep(1.5)
            print(f"\n🏁 Finished. {len(urls)} found, {success} saved, {skipped} skipped, {failed} errors")
            
            status = "SUCCESS"
            if failed > 0:
                 status = "PARTIAL" if (success > 0 or skipped > 0) else "FAILED"
                 
            try:
                from src.utils.logger_utils import log_scraper_execution
                Session = sessionmaker(bind=self.engine)
                with Session() as db:
                     log_scraper_execution(
                          db=db,
                          scraper_name="maximum-genc",
                          status=status,
                          total_found=len(urls),
                          total_saved=success,
                          total_skipped=skipped,
                          total_failed=failed,
                          error_details={"errors": error_details} if error_details else None
                     )
            except Exception as le:
                 print(f"⚠️ Could not save scraper log: {le}")
                 
        except Exception as e:
            print(f"❌ Scraper error: {e}")
            try:
                from src.utils.logger_utils import log_scraper_execution
                Session = sessionmaker(bind=self.engine)
                with Session() as db:
                     log_scraper_execution(db, "maximum-genc", "FAILED", 0, 0, 0, 1, {"error": str(e)})
            except:
                pass
            raise
        finally:
            self._stop_browser()


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=None, help="Limit the number of campaigns to scrape")
    args = parser.parse_args()
    
    scraper = IsbankMaximumGencScraper()
    scraper.run(limit=args.limit)
