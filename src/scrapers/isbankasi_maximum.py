
import sys
import os
import time
import re
import uuid
import traceback
from datetime import datetime
from typing import Optional, Dict, Any, List
from urllib.parse import urljoin
from bs4 import BeautifulSoup

# Path setup - same as ziraat.py
current_dir = os.path.dirname(os.path.abspath(__file__))  # src/scrapers
project_root = os.path.dirname(os.path.dirname(current_dir))  # project root
if project_root not in sys.path:
    sys.path.insert(0, project_root)
# Also add src dir for relative imports
src_dir = os.path.dirname(current_dir)
if src_dir not in sys.path:
    sys.path.insert(1, src_dir)

print(f"[DEBUG] project_root: {project_root}")
print(f"[DEBUG] sys.path[:3]: {sys.path[:3]}")

# Load Env - same pattern as ziraat.py
try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception as e:
    print(f"[DEBUG] dotenv load failed: {e}")
try:
    with open(os.path.join(project_root, '.env'), 'r') as f:
        for line in f:
            if line.strip() and not line.startswith('#') and '=' in line:
                k, v = line.strip().split('=', 1)
                if k not in os.environ:
                    os.environ[k] = v.strip('"\'')
except Exception:
    pass

from sqlalchemy import create_engine, Column, Integer, String, Boolean, DateTime, Date, Numeric, Text, ForeignKey
from sqlalchemy.orm import sessionmaker, relationship, declarative_base
from sqlalchemy.dialects.postgresql import UUID

DATABASE_URL = os.environ.get("DATABASE_URL")
print(f"[DEBUG] DATABASE_URL set: {'YES' if DATABASE_URL else 'NO'}")

# AIParser is lazy-imported in __init__ to avoid google.generativeai hang
AIParser = None




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
    "Market & Gıda": "Market",
    "Giyim & Aksesuar": "Giyim",
    "Restoran & Kafe": "Restoran & Kafe",
    "Seyahat": "Seyahat",
    "Turizm & Konaklama": "Seyahat",
    "Elektronik": "Elektronik",
    "Mobilya & Dekorasyon": "Mobilya & Dekorasyon",
    "Kozmetik & Sağlık": "Kozmetik & Sağlık",
    "E-Ticaret": "E-Ticaret",
    "Otomotiv": "Otomotiv",
    "Sigorta": "Sigorta",
    "Eğitim": "Eğitim",
    "Diğer": "Diğer",
}


class IsbankMaximumScraper:
    """İşbankası Maximum card campaign scraper - Playwright based"""

    BASE_URL = "https://www.maximum.com.tr"
    CAMPAIGNS_URL = "https://www.maximum.com.tr/kampanyalar"
    BANK_NAME = "İşbankası"
    CARD_SLUG = "maximum-card"  # seed.ts'deki gerçek slug

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
        # Search with multiple slug/name variants
        bank = self.db.query(Bank).filter(
            Bank.slug.in_([
                'i-sbankasi',   # gerçek DB slug
                'isbank',       # seed.ts slug
                'isbankasi', 'is-bankasi', 'turkiye-is-bankasi',
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
                'maximum-card', 'maximum', 'isbank-maximum',
                'isbankasi-maximum', 'maximumcard',
            ])
        ).first()
        if not card:
            card = self.db.query(Card).filter(
                Card.name.ilike('%Maximum%'),
                Card.bank_id == bank.id
            ).first()
        if not card:
            print(f"⚠️  Card 'maximum-card' not found, creating...")
            card = Card(bank_id=bank.id, name='Maximum Card', slug='maximum-card', is_active=True)
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
                args=[
                    "--no-sandbox", "--disable-setuid-sandbox",
                    "--disable-dev-shm-usage", "--disable-gpu",
                    "--window-size=1920,1080",
                    "--disable-blink-features=AutomationControlled",
                    "--disable-extensions",
                    "--disable-web-security",
                ]
            )
            context = self.browser.new_context(
                viewport={"width": 1920, "height": 1080},
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
                locale="tr-TR",
                timezone_id="Europe/Istanbul",
                extra_http_headers={
                    "Accept-Language": "tr-TR,tr;q=0.9,en;q=0.8",
                    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
                }
            )
            # Disable navigator.webdriver flag
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
        for attempt, wait in enumerate(["domcontentloaded", "commit", "load"]):
            try:
                print(f"   🔄 Attempt {attempt+1}/3 (wait_until={wait})...")
                self.page.goto(self.CAMPAIGNS_URL, wait_until=wait, timeout=120000)
                print(f"   ✅ Page loaded ({wait})")
                break
            except Exception as e:
                print(f"   ⚠️ Attempt {attempt+1} failed: {e}")
                if attempt == 2:
                    print("   ❌ All attempts failed, continuing with current page state")
        time.sleep(5)


        scroll_count = 0
        while True:
            soup = BeautifulSoup(self.page.content(), "html.parser")
            current = len([
                a for a in soup.find_all("a", href=True)
                if "/kampanyalar/" in a["href"] and "arsiv" not in a["href"]
                and "gecmis" not in a["href"] and len(a["href"]) > 20
            ])
            if limit and current >= limit:
                break

            btn = self.page.query_selector("a.CampAllShow")
            if btn and btn.is_visible():
                btn.scroll_into_view_if_needed()
                time.sleep(1)
                btn.click()
                time.sleep(2.5)
                scroll_count += 1
                print(f"   ⏬ Loaded more campaigns (round {scroll_count})...")
            else:
                print("   ℹ️ No more 'Load More' button.")
                break

            if scroll_count > 50:
                break

        soup = BeautifulSoup(self.page.content(), "html.parser")
        
        excluded_suffixes = [
            "-kampanyalari",
            "-kampanyalar",
            "premium-kampanyalar",
            "tum-kampanyalar"
        ]
        
        excluded_paths = [
            "/kampanyalar/seyahat",
            "/kampanyalar/turizm",
            "/kampanyalar/akaryakit",
            "/kampanyalar/giyim-aksesuar",
            "/kampanyalar/market",
            "/kampanyalar/elektronik",
            "/kampanyalar/beyaz-esya",
            "/kampanyalar/mobilya-dekorasyon",
            "/kampanyalar/egitim-kirtasiye",
            "/kampanyalar/online-alisveris",
            "/kampanyalar/otomotiv",
            "/kampanyalar/vergi-odemeleri",
            "/kampanyalar/maximum-mobil",
            "/kampanyalar/diger",
            "/kampanyalar/yeme-icme",
            "/kampanyalar/maximum-pati-kart",
            "/kampanyalar/arac-kiralama",
            "/kampanyalar/bankamatik",
            "bireysel", "ticari", "diger-kampanyalar",
            "movenpick", "arsivi", "ozel-bankacilik"
        ]

        all_links = []
        expired_links = []
        for a in soup.find_all("a", href=True):
            href = a["href"].lower()
            
            if (
                ("/kampanyalar/" in href or "kampanyalar/" in href)
                and "arsiv" not in href
                and "gecmis" not in href
                and "past" not in href
            ):
                is_exact_category = any(href.endswith(path) for path in excluded_paths)
                is_category_suffix = any(href.endswith(suffix) for suffix in excluded_suffixes)
                is_common_page = "ozellikler" in href or "basvuru" in href or href.endswith("/kampanyalar")
                
                if not is_exact_category and not is_category_suffix and not is_common_page and len(href) > 25:
                    full_url = urljoin(self.BASE_URL, a["href"])
                    
                    # Sona ermiş kampanya tespiti
                    parent_text = ""
                    parent = a.find_parent("div", class_="card") or a.find_parent("div", class_="campaign-card") or a.find_parent("div", class_="opportunity-result") or a.parent
                    if parent:
                        parent_text = parent.get_text(separator=" ", strip=True).lower()
                        
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
                
            self.page.evaluate("window.scrollTo(0, 600)")
            time.sleep(1.5)

            soup = BeautifulSoup(self.page.content(), "html.parser")
            title_el = soup.select_one("h1.gradient-title-text") or soup.find("h1")
            title = self._clean(title_el.text) if title_el else "Başlık Yok"

            if "gecmis" in url or "geçmiş" in title.lower():
                return None

            # 404 kontrolü - Geçersiz/silinmiş sayfa filtresi
            page_text = soup.get_text()
            if any(phrase in page_text for phrase in [
                "Üzgünüz, aradığınız sayfayı bulamadık",
                "404",
                "Sayfa Bulunamadı",
                "Bu kampanya sona ermiştir",
            ]):
                print(f"   ⏭️  Skipped (404/expired page): {url}")
                return None

            # Date
            date_text = ""
            for sel in ["span[id$='KampanyaTarihleri']", ".campaign-date", ".date"]:
                el = soup.select_one(sel)
                if el:
                    date_text = self._clean(el.text)
                    break

            # Skip expired
            end_iso = self._parse_date(date_text, is_end=True)
            if end_iso:
                try:
                    if datetime.strptime(end_iso, "%Y-%m-%d") < datetime.now():
                        return None
                except Exception:
                    pass

            # Participation
            participation_text = ""
            for sel in ["span[id$='KatilimSekli']"]:
                el = soup.select_one(sel)
                if el:
                    participation_text = self._clean(el.text)
                    break

            # Description / Conditions
            desc_el = soup.select_one("span[id$='CampaignDescription']")
            conditions = []
            full_text = ""
            if desc_el:
                for br in desc_el.find_all("br"):
                    br.replace_with("\n")
                raw = desc_el.get_text()
                conditions = [self._clean(l) for l in raw.split("\n") if len(self._clean(l)) > 15]
                full_text = " ".join(conditions)
            else:
                full_text = self._clean(soup.get_text())[:1500]

            if participation_text:
                full_text += f"\nKATILIM ŞEKLİ: {participation_text}"

            # Image
            image_url = None
            img_el = soup.select_one("img[id$='CampaignImage']")
            if img_el:
                src = img_el.get("data-original") or img_el.get("data-src") or img_el.get("src")
                if src and not src.startswith("data:"):
                    image_url = urljoin(self.BASE_URL, src)

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
        
        # Check dd.mm.yyyy format first: 1.1.2026 - 31.12.2026
        pattern_dot = r"(\d{1,2})\.(\d{1,2})\.(\d{4})\s*-\s*(\d{1,2})\.(\d{1,2})\.(\d{4})"
        match_dot = re.search(pattern_dot, text)
        if match_dot:
            d1, m1, y1, d2, m2, y2 = match_dot.groups()
            if is_end:
                return f"{y2}-{m2.zfill(2)}-{d2.zfill(2)}"
            return f"{y1}-{m1.zfill(2)}-{d1.zfill(2)}"
            
        months = {
            "ocak": "01", "şubat": "02", "mart": "03", "nisan": "04",
            "mayıs": "05", "haziran": "06", "temmuz": "07", "ağustos": "08",
            "eylül": "09", "ekim": "10", "kasım": "11", "aralık": "12",
        }
        try:
            pattern = r"(\d{1,2})\s*([a-zğüşıöç]+)?\s*-\s*(\d{1,2})\s*([a-zğüşıöç]+)\s*(\d{4})"
            match = re.search(pattern, text)
            if match:
                day1, month1, day2, month2, year = match.groups()
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
        base = re.sub(r'[^a-z0-9]+', '-', re.sub(
            r'[şğüöçıŞĞÜÖÇİ]',
            lambda m: 'sgupcisgupci'['şğüöçıŞĞÜÖÇİ'.index(m.group())],
            title.lower()
        )).strip('-')
        slug = base
        counter = 1
        while self.db.query(Campaign).filter(Campaign.slug == slug).first():
            slug = f"{base}-{counter}"
            counter += 1
        return slug

    def _process_campaign(self, url: str) -> str:
        existing = self.db.query(Campaign).filter(
            Campaign.tracking_url == url,
            Campaign.card_id == self.card_id
        ).first()
        if existing:
            print(f"   ⏭️  Skipped (Already exists)")
            return "skipped"

        print(f"🔍 Processing: {url}")
        data = self._extract_campaign_data(url)
        if not data:
            print("   ⏭️  Skipped (expired/invalid)")
            return "skipped"

        try:
            ai_data = self.parser.parse_campaign_data(
                raw_text=data["full_text"],
                bank_name=self.BANK_NAME,
                title=data["title"],
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
            db_sector_name = SECTOR_MAP.get(ai_cat, "Diğer")
            sector = self.db.query(Sector).filter(Sector.name == db_sector_name).first()
            if not sector:
                sector = self.db.query(Sector).filter(Sector.slug == 'diger').first()

            start_date = None
            end_date = None
            if ai_data.get("start_date"):
                try:
                    start_date = datetime.strptime(ai_data["start_date"], "%Y-%m-%d")
                except Exception:
                    pass
            if ai_data.get("end_date"):
                try:
                    end_date = datetime.strptime(ai_data["end_date"], "%Y-%m-%d")
                except Exception:
                    pass
            if not start_date:
                sd = self._parse_date(data["date_text"], is_end=False)
                if sd:
                    try:
                        start_date = datetime.strptime(sd, "%Y-%m-%d")
                    except Exception:
                        pass
            if not end_date:
                ed = self._parse_date(data["date_text"], is_end=True)
                if ed:
                    try:
                        end_date = datetime.strptime(ed, "%Y-%m-%d")
                    except Exception:
                        pass

            conds = ai_data.get("conditions", [])
            part = ai_data.get("participation")
            if part and "Detayları İnceleyin" not in part:
                conds.insert(0, f"KATILIM: {part}")
            final_conditions = "\n".join(conds)

            eligible = ", ".join(ai_data.get("cards", [])) or None

            campaign = Campaign(
                card_id=self.card_id,
                sector_id=sector.id if sector else None,
                slug=slug,
                title=formatted_title,
                description=ai_data.get("description") or data["title"][:200],
                reward_text=ai_data.get("reward_text"),
                reward_value=ai_data.get("reward_value"),
                reward_type=ai_data.get("reward_type"),
                conditions=final_conditions,
                eligible_cards=eligible,
                image_url=data["image_url"],
                start_date=start_date,
                end_date=end_date,
                is_active=True,
                tracking_url=url,
                created_at=datetime.utcnow(),
                updated_at=datetime.utcnow(),
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
            print("🚀 Starting İşbankası Maximum Scraper (Playwright)...")
            from src.utils.logger_utils import log_scraper_execution
            self._start_browser()
            
            # Close DB session to prevent idle connection timeout during long Playwright scroll
            if self.db:
                self.db.commit()
                self.db.close()
                
            active_urls, expired_urls = self._fetch_campaign_urls(limit=limit)
            
            # Evaluate expired campaigns logic
            # Use a new short-lived session context for this specific update
            with self.engine.connect() as conn:
                from sqlalchemy.orm import Session as SASession
                with SASession(bind=conn) as temp_db:
                    if expired_urls:
                        print(f"🛑 Found {len(expired_urls)} expired campaigns on list page. Checking DB for early end...")
                        for e_url in expired_urls:
                            try:
                                existing = temp_db.query(Campaign).filter(
                                    Campaign.tracking_url == e_url,
                                    Campaign.card_id == self.card_id,
                                    Campaign.is_active == True
                                ).first()
                                if existing:
                                    print(f"   🛑 Desactivating expired campaign in DB: {existing.title}")
                                    existing.is_active = False
                                    temp_db.commit()
                            except Exception as e:
                                temp_db.rollback()
                                print(f"   ⚠️ Could not update expired campaign {e_url}: {e}")
                        
            urls = active_urls

            # Reopen DB for main loop
            Session = sessionmaker(bind=self.engine)
            self.db = Session()

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
                        error_details.append({"url": url, "error": "Process campaign returned error string"})
                except Exception as e:
                    print(f"❌ Error: {e}")
                    if self.db:
                        self.db.rollback()
                    failed += 1
                    error_details.append({"url": url, "error": str(e)})
                time.sleep(1.5)

            print(f"\n🏁 Finished. {len(urls)} found, {success} saved, {skipped} skipped, {failed} errors")
            
            status = "SUCCESS"
            if failed > 0:
                status = "PARTIAL" if (success > 0 or skipped > 0) else "FAILED"
                
            log_scraper_execution(
                db=self.db,
                scraper_name="isbankasi_maximum",
                status=status,
                total_found=len(urls),
                total_saved=success,
                total_skipped=skipped,
                total_failed=failed,
                error_details={"errors": error_details} if error_details else None
            )
            
        except Exception as e:
            print(f"❌ Scraper error: {e}")
            
            status = "FAILED"
            Session = sessionmaker(bind=self.engine)
            err_db = Session()
            from src.utils.logger_utils import log_scraper_execution
            try:
                log_scraper_execution(
                    db=err_db,
                    scraper_name="isbankasi_maximum",
                    status=status,
                    total_found=0,
                    total_saved=0,
                    total_skipped=0,
                    total_failed=1,
                    error_details={"error": str(e)}
                )
            except:
                pass
            finally:
                err_db.close()
                
            raise
        finally:
            self._stop_browser()
            if hasattr(self, 'db') and self.db:
                self.db.close()


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=None, help="Limit the number of campaigns to scrape")
    args = parser.parse_args()
    
    scraper = IsbankMaximumScraper()
    scraper.run(limit=args.limit)
