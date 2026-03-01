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
        self.engine = create_engine(DATABASE_URL)
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

    def _fetch_campaign_urls(self, limit: Optional[int] = None) -> List[str]:
        print(f"📥 Fetching campaign list from {self.CAMPAIGNS_URL}...")
        self.page.goto(self.CAMPAIGNS_URL, wait_until="domcontentloaded", timeout=120000)
        time.sleep(5)

        scroll_count = 0
        while scroll_count < 100:
            if limit:
                soup = BeautifulSoup(self.page.content(), "html.parser")
                count = len([
                    a for a in soup.find_all("a", href=True)
                    if "/kampanyalar/" in a["href"] and "gecmis" not in a["href"]
                ])
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
        excluded = ["gecmis", "arsiv", "tum-kampanyalar"]

        all_links = []
        # First try .opportunity-result cards
        cards = soup.select(".opportunity-result a")
        print(f"   ℹ️ Found {len(cards)} opportunity-result links")
        for a in cards:
            href = a.get("href")
            if href and not any(ex in href for ex in excluded) and "javascript" not in href:
                all_links.append(urljoin(self.BASE_URL, href))

        # Fallback: broad search
        if not all_links:
            print("   ⚠️ No opportunity-result links, trying broad search...")
            for a in soup.find_all("a", href=True):
                href = a["href"]
                if "/kampanyalar/" in href and not any(ex in href for ex in excluded):
                    all_links.append(urljoin(self.BASE_URL, href))

        unique_urls = list(dict.fromkeys(all_links))
        if limit:
            unique_urls = unique_urls[:limit]
        print(f"✅ Found {len(unique_urls)} unique campaigns")
        return unique_urls

    def _extract_campaign_data(self, url: str) -> Optional[Dict[str, Any]]:
        try:
            self.page.goto(url, wait_until="domcontentloaded", timeout=60000)
            self.page.evaluate("window.scrollTo(0, 500)")
            time.sleep(2)

            soup = BeautifulSoup(self.page.content(), "html.parser")
            title_el = soup.select_one("h1")
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
            date_el = soup.select_one(".date, .campaign-date")
            if date_el:
                date_text = self._clean(date_el.text)
            if not date_text:
                full_text = soup.get_text()
                m = re.search(r"(\d{1,2}\s+\w+\s+\d{4})\s*-\s*(\d{1,2}\s+\w+\s+\d{4})", full_text)
                if m:
                    date_text = m.group(0)

            # Content
            content_div = soup.select_one(".detail-text, .campaign-content, section .container")
            conditions = []
            full_text = ""
            if content_div:
                raw = content_div.get_text("\n")
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
            print(f"   ⚠️ AI error: {e}")
            ai_data = {}

        try:
            slug = self._get_or_create_slug(ai_data.get("title") or data["title"])
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
                slug=slug, title=ai_data.get("title") or data["title"],
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

            for b_name in ai_data.get("brands", []):
                if len(b_name) < 2:
                    continue
                b_slug = re.sub(r'[^a-z0-9]+', '-', b_name.lower()).strip('-')
                brand = self.db.query(Brand).filter(Brand.slug == b_slug).first()
                if not brand:
                    brand = Brand(name=b_name, slug=b_slug)
                    self.db.add(brand)
                    self.db.commit()
                if not self.db.query(CampaignBrand).filter(
                    CampaignBrand.campaign_id == campaign.id,
                    CampaignBrand.brand_id == brand.id
                ).first():
                    self.db.add(CampaignBrand(campaign_id=campaign.id, brand_id=brand.id))
                    self.db.commit()

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
            urls = self._fetch_campaign_urls(limit=limit)
            success, skipped, failed = 0, 0, 0
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
                except Exception as e:
                    print(f"❌ Error: {e}")
                    failed += 1
                time.sleep(1.5)
            print(f"\n🏁 Finished. {len(urls)} found, {success} saved, {skipped} skipped, {failed} errors")
        except Exception as e:
            print(f"❌ Scraper error: {e}")
            raise
        finally:
            self._stop_browser()
            self.db.close()


if __name__ == "__main__":
    scraper = IsbankMaximumGencScraper()
    scraper.run()
