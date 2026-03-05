
import sys
import os
import time
import re
import uuid
import traceback
from datetime import datetime
from typing import Optional, Dict, Any, List
from bs4 import BeautifulSoup
from urllib.parse import urljoin

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

from src.utils.logger_utils import log_scraper_execution

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
    __tablename__ = 'test_campaign_brands' if os.environ.get('TEST_MODE') == '1' else 'campaign_brands'
    campaign_id = Column(Integer, ForeignKey('campaigns.id'), primary_key=True)
    brand_id = Column(UUID(as_uuid=True), ForeignKey('brands.id'), primary_key=True)
    brand = relationship("Brand", back_populates="campaigns")
    campaign = relationship("Campaign", back_populates="brands")

class Campaign(Base):
    __tablename__ = 'test_campaigns' if os.environ.get('TEST_MODE') == '1' else 'campaigns'
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
        self.session = Session()
        self.headers = {
            'User-Agent': "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8',
            'Accept-Language': 'tr-TR,tr;q=0.9,en-US;q=0.8,en;q=0.7',
        }
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

    def _get_or_create_bank(self) -> int:
        bank = self.session.query(Bank).filter(
            Bank.slug.in_([
                'i-sbankasi',   # gerçek DB slug
                'isbank',       # seed.ts slug
                'isbankasi', 'is-bankasi', 'turkiye-is-bankasi',
            ])
        ).first()
        if not bank:
            bank = self.session.query(Bank).filter(
                Bank.name.ilike('%İş Bank%') | Bank.name.ilike('%İşbank%')
            ).first()
        if not bank:
            print(f"⚠️  {self.BANK_NAME} not found in DB, creating...")
            bank = Bank(name=self.BANK_NAME, slug='isbank')
            self.session.add(bank)
            self.session.commit()
        return bank.id

    def _get_or_create_card(self, bank_id: int) -> int:
        card = self.session.query(Card).filter(
            Card.slug.in_([
                'maximum-card', 'maximum', 'isbank-maximum',
                'isbankasi-maximum', 'maximumcard',
            ])
        ).first()
        if not card:
            card = self.session.query(Card).filter(
                Card.name.ilike('%Maximum%'),
                Card.bank_id == bank_id
            ).first()
        if not card:
            print(f"⚠️  Card 'maximum-card' not found, creating...")
            card = Card(bank_id=bank_id, name='Maximum Card', slug='maximum-card', is_active=True)
            self.session.add(card)
            self.session.commit()
        return card.id

    def _fetch_campaign_urls(self, limit: Optional[int] = None) -> tuple[List[str], List[str]]:
        print(f"📥 Fetching campaign list from {self.CAMPAIGNS_URL}...")
        
        import requests
        import urllib3
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
        
        # We fetch the first page. Maximum usually loads a bunch of HTML blocks, and potentially has a load-more API.
        # But for simplification and immediate WAF bypass, we fetch the main HTML.
        all_campaign_links = []
        try:
            response = requests.get(self.CAMPAIGNS_URL, headers=self.headers, verify=False, timeout=20)
            response.raise_for_status()
            soup = BeautifulSoup(response.text, "html.parser")
            
            # Extract links
            links = soup.find_all("a", href=True)
            for a in links:
                if "/kampanyalar/" in a["href"] and "arsiv" not in a["href"] and "gecmis" not in a["href"] and len(a["href"]) > 20:
                    all_campaign_links.append(a)
                    
        except Exception as e:
            print(f"   ❌ Failed to fetch campaign list: {e}")
            return [], []

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
            "movenpick", "arsivi", "ozel-bankacilik",
            "/kampanyalar/arsiv",
            "/kampanyalar/yurtdisi"
        ]

        unique_urls = []
        unique_expired = []
        seen = set()

        for a in all_campaign_links:
            href = a["href"]
            
            if href in excluded_paths: continue
            if any(href.endswith(s) for s in excluded_suffixes): continue
            
            full_url = urljoin(self.BASE_URL, href)
            if full_url in seen: continue
            seen.add(full_url)
            
            # Check for expired status based on class or text
            parent_text = ""
            parent = a.find_parent("div", class_="card") or a.find_parent("div", class_="campaign-card") or a.find_parent("div", class_="opportunity-result") or a.parent
            if parent:
                parent_text = parent.get_text(separator=" ", strip=True).lower()

            if a.find(class_="expired") or "gecmis" in href or "geçmiş" in a.text.lower() or \
               "sona ermiştir" in parent_text or "bitmiştir" in parent_text or "sona erdi" in parent_text or "süresi doldu" in parent_text:
                unique_expired.append(full_url)
            else:
                unique_urls.append(full_url)
                
        if limit:
            unique_urls = unique_urls[:limit]

        print(f"✅ Found {len(unique_urls)} active campaigns, and {len(unique_expired)} expired campaigns")
        return unique_urls, unique_expired

    def _extract_campaign_data(self, url: str) -> Optional[Dict[str, Any]]:
        try:
            success = False
            html_content = ""
            for attempt in range(3):
                try:
                    import requests
                    time.sleep(1.5 + attempt) # modest delay
                    response = requests.get(
                        url, 
                        headers=self.headers,
                        timeout=15,
                        verify=False # avoid SSL certificate verify errors just in case
                    )
                    response.raise_for_status()
                    html_content = response.text
                    success = True
                    break
                except Exception as e:
                    print(f"      ⚠️ Detail load attempt {attempt+1}/3 failed (requests): {e}. Retrying...")
                    time.sleep(3 + attempt * 2)
            
            if not success:
                print(f"      ❌ Could not load detail page after 3 attempts: {url}")
                return None
                
            import urllib3
            urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

            soup = BeautifulSoup(html_content, "html.parser")
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
                    if datetime.strptime(end_iso, "%Y-%m-%d").date() < datetime.now().date():
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
            desc_el = soup.select_one(".campaign-detail, .campaignDetail, .content, .detail-content, .editor-content")
            full_text = ""
            if desc_el:
                for br in desc_el.find_all("br"):
                    br.replace_with("\n")
                full_text = "\n".join([self._clean(l) for l in desc_el.get_text().split("\n") if len(self._clean(l)) > 0])
            else:
                full_text = self._clean(soup.get_text())[:2000]

            # Image
            image_url = None
            img_el = soup.select_one("img[id$='CampaignImage']") or soup.select_one('.campaign-detail img')
            if img_el:
                src = img_el.get("data-original") or img_el.get("data-src") or img_el.get("src")
                if src and not src.startswith("data:"):
                    image_url = urljoin(self.BASE_URL, src)

            return {
                "title": title, "image_url": image_url,
                "date_text": date_text, "full_text": full_text,
                "source_url": url,
                "raw_text": full_text # Feed clean text to AI, not entire HTML
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
        while self.session.query(Campaign).filter(Campaign.slug == slug).first():
            slug = f"{base}-{counter}"
            counter += 1
        return slug

    def _save_campaign(self, data: Dict[str, Any], bank_id: int, card_id: int) -> Optional[int]:
        try:
            raw_title = data.get("title") or ""
            formatted_title = self._to_title_case(raw_title)
            slug = self._get_or_create_slug(formatted_title)

            ai_cat = data.get("sector", "Diğer")
            db_sector_name = ai_cat
            sector = self.session.query(Sector).filter(Sector.slug == db_sector_name).first()
            if not sector:
                sector = self.session.query(Sector).filter(Sector.slug == 'diger').first()

            start_date = None
            end_date = None
            if data.get("start_date"):
                try:
                    start_date = datetime.strptime(data["start_date"], "%Y-%m-%d").date()
                except Exception:
                    pass
            if data.get("end_date"):
                try:
                    end_date = datetime.strptime(data["end_date"], "%Y-%m-%d").date()
                except Exception:
                    pass
            if not start_date:
                sd = self._parse_date(data["date_text"], is_end=False)
                if sd:
                    try:
                        start_date = datetime.strptime(sd, "%Y-%m-%d").date()
                    except Exception:
                        pass
            if not end_date:
                ed = self._parse_date(data["date_text"], is_end=True)
                if ed:
                    try:
                        end_date = datetime.strptime(ed, "%Y-%m-%d").date()
                    except Exception:
                        pass

            conds = data.get("conditions", [])
            part = data.get("participation")
            if part and "Detayları İnceleyin" not in part:
                conds.insert(0, f"KATILIM: {part}")
            final_conditions = "\n".join(conds)

            eligible = ", ".join(data.get("cards", [])) or None

            campaign = Campaign(
                card_id=card_id,
                sector_id=sector.id if sector else None,
                slug=slug,
                title=formatted_title,
                description=data.get("description") or data["title"][:200],
                reward_text=data.get("reward_text"),
                reward_value=data.get("reward_value"),
                reward_type=data.get("reward_type"),
                conditions=final_conditions,
                eligible_cards=eligible,
                image_url=data["image_url"],
                start_date=start_date,
                end_date=end_date,
                is_active=True,
                tracking_url=data["source_url"],
                created_at=datetime.utcnow(),
                updated_at=datetime.utcnow(),
            )
            self.session.add(campaign)
            self.session.commit()

            # Brands
            for b_name in data.get("brands", []):
                if len(b_name) < 2:
                    continue
                b_slug = re.sub(r'[^a-z0-9]+', '-', b_name.lower()).strip('-')
                
                try:
                    brand = self.session.query(Brand).filter(
                        (Brand.slug == b_slug) | (Brand.name.ilike(b_name))
                    ).first()
                    if not brand:
                        brand = Brand(name=self._to_title_case(b_name), slug=b_slug)
                        self.session.add(brand)
                        self.session.commit()
                except Exception as e:
                    self.session.rollback()
                    print(f"   ⚠️ Brand save failed for {b_name}: {e}")
                    continue

                try:    
                    link = self.session.query(CampaignBrand).filter(
                        CampaignBrand.campaign_id == campaign.id,
                        CampaignBrand.brand_id == brand.id
                    ).first()
                    if not link:
                        self.session.add(CampaignBrand(campaign_id=campaign.id, brand_id=brand.id))
                        self.session.commit()
                except Exception as e:
                    self.session.rollback()
                    print(f"   ⚠️ CampaignBrand link failed: {e}")
                    continue

            print(f"   ✅ Saved: {campaign.title[:50]}")
            return campaign.id
        except Exception as e:
            self.session.rollback()
            print(f"   ❌ Save failed: {e}")
            traceback.print_exc()
            return None

    def run(self, limit: Optional[int] = None):
        """Main execution flow"""
        global AIParser
        if AIParser is None:
            try:
                from src.services.ai_parser import AIParser
                print("[DEBUG] AIParser lazy-imported via src.services")
            except ImportError:
                print("[DEBUG] Absolute import failed, trying relative")
                from services.ai_parser import AIParser
        
        bank_id = self._get_or_create_bank()
        card_id = self._get_or_create_card(bank_id)

        print(f"✅ Bank: {self.BANK_NAME} (ID: {bank_id}, slug: isbankasi)")
        print(f"✅ Card: Maximum (ID: {card_id}, slug: {self.CARD_SLUG})")
        print("🚀 Starting İşbankası Maximum Scraper (Requests)...")

        try:
            urls, expired_urls = self._fetch_campaign_urls(limit=limit)

            # Evaluate expired campaigns logic
            if expired_urls:
                print(f"🛑 Found {len(expired_urls)} expired campaigns on list page. Checking DB for early end...")
                for e_url in expired_urls:
                    try:
                        existing = self.session.query(Campaign).filter(
                            Campaign.tracking_url == e_url,
                            Campaign.card_id == card_id,
                            Campaign.is_active == True
                        ).first()
                        if existing:
                            print(f"   🛑 Deleting expired campaign from DB: {existing.title}")
                            self.session.delete(existing)
                            self.session.commit()
                    except Exception as e:
                        self.session.rollback()
                        print(f"   ⚠️ Could not update expired campaign {e_url}: {e}")
                        
            results = []
            success, skipped, failed = 0, 0, 0
            error_details = []
            
            for i, url in enumerate(urls, 1):
                if limit and i > limit:
                    break
                    
                print(f"\n[{i}/{len(urls)}]")
                print(f"🔍 Processing: {url}")
                
                # DB Cache query
                existing = self.session.query(Campaign).filter(
                    Campaign.tracking_url == url,
                    Campaign.card_id == card_id
                ).first()
                if existing:
                    print(f"   ℹ️  Already exists in DB: [{existing.id}] {existing.title[:40]}")
                    skipped += 1
                    continue
                    
                try:
                    res_data = self._extract_campaign_data(url)
                    if not res_data:
                        skipped += 1
                        continue
                        
                    try:
                        ai_data = AIParser.parse_campaign_data(
                            raw_text=res_data["raw_text"],
                            title=res_data["title"],
                            bank_name=self.BANK_NAME,
                            card_name="Maximum Card"
                        )
                        if ai_data:
                            print("   ✅ AI parsed successfully")
                            res_data.update(ai_data)
                    except Exception as ai_e:
                        print(f"   ⚠️ AI parse error: {ai_e}")
                        
                    saved_id = self._save_campaign(res_data, bank_id, card_id)
                    if saved_id:
                        success += 1
                        results.append(saved_id)
                    else:
                        failed += 1
                        error_details.append({"url": url, "error": "Save returned None"})
                        
                except Exception as e:
                    print(f"❌ Error during details extraction: {e}")
                    self.session.rollback()
                    failed += 1
                    error_details.append({"url": url, "error": str(e)})
                
                time.sleep(1.5)

            print(f"\n🏁 Finished. {len(urls)} found, {success} saved, {skipped} skipped, {failed} errors")
            
            status = "SUCCESS"
            if failed > 0:
                status = "PARTIAL" if (success > 0 or skipped > 0) else "FAILED"
                
            log_scraper_execution(
                db=self.session,
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
            self.session.close()


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=None, help="Limit the number of campaigns to scrape")
    args = parser.parse_args()
    
    scraper = IsbankMaximumScraper()
    scraper.run(limit=args.limit)
