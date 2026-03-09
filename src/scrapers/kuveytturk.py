import sys
import os
import time
import re
import uuid
import traceback
from datetime import datetime
from typing import Optional, Dict, Any, List, Tuple
import requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin

# Path setup
current_dir = os.path.dirname(os.path.abspath("/Users/hipoglisemi/Desktop/kartavantaj-scraper/src/scrapers/kuveytturk.py"))  # src/scrapers
project_root = os.path.dirname(os.path.dirname(current_dir))  # project root
if project_root not in sys.path:
    sys.path.insert(0, project_root)
src_dir = os.path.dirname(current_dir)
if src_dir not in sys.path:
    sys.path.insert(1, src_dir)

from src.utils.logger_utils import log_scraper_execution

# Load Env
try:
    from dotenv import load_dotenv
    load_dotenv()
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

from sqlalchemy import create_engine, Column, Integer, String, Boolean, DateTime, Date, Numeric, Text, ForeignKey
from sqlalchemy.orm import sessionmaker, relationship, declarative_base
from sqlalchemy.dialects.postgresql import UUID

DATABASE_URL = os.environ.get("DATABASE_URL")
AIParser = None

Base = declarative_base()

class Bank(Base):
    __tablename__ = 'banks'
    id = Column(Integer, primary_key=True)
    name = Column(String)
    slug = Column(String)

class Card(Base):
    __tablename__ = 'cards'
    id = Column(Integer, primary_key=True)
    bank_id = Column(Integer, ForeignKey('banks.id'))
    name = Column(String)
    slug = Column(String)
    is_active = Column(Boolean, default=True)

class Sector(Base):
    __tablename__ = 'sectors'
    id = Column(Integer, primary_key=True)
    name = Column(String)
    slug = Column(String)

class Brand(Base):
    __tablename__ = 'brands'
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name = Column(String)
    slug = Column(String)

class CampaignBrand(Base):
    __tablename__ = 'test_campaign_brands' if os.environ.get('TEST_MODE') == '1' else 'campaign_brands'
    campaign_id = Column(Integer, ForeignKey('test_campaigns.id' if os.environ.get('TEST_MODE') == '1' else 'campaigns.id'), primary_key=True)
    brand_id = Column(UUID(as_uuid=True), ForeignKey('brands.id'), primary_key=True)

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
    clean_text = Column(String)
    ai_marketing_text = Column(String)


class KuveytTurkScraper:
    """Kuveyt Türk Sağlam Kart campaign scraper - Playwright based"""

    BASE_URL = "https://saglamkart.kuveytturk.com.tr"
    CAMPAIGNS_URL = "https://saglamkart.kuveytturk.com.tr/kampanyalar"
    BANK_NAME = "Kuveyt Türk"
    CARD_SLUG = "saglam-kart"

    def __init__(self):
        if not DATABASE_URL:
            raise ValueError("DATABASE_URL is not set")
        self.engine = create_engine(DATABASE_URL, pool_pre_ping=True, pool_recycle=300)
        Session = sessionmaker(bind=self.engine)
        self.session = Session()
        self.headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
            "Accept-Language": "tr-TR,tr;q=0.9,en-US;q=0.8,en;q=0.7",
            "Referer": "https://saglamkart.kuveytturk.com.tr/"
        }
        
        try:
            from src.services.ai_parser import AIParser as _AIParser
        except ImportError:
            from services.ai_parser import AIParser as _AIParser
        self.parser = _AIParser()

    def _get_or_create_bank(self) -> int:
        bank = self.session.query(Bank).filter(
            Bank.slug.in_(['kuveyt-turk', 'kuveytturk'])
        ).first()
        if not bank:
            bank = self.session.query(Bank).filter(
                Bank.name.ilike('%Kuveyt%')
            ).first()
        if not bank:
            print(f"⚠️  {self.BANK_NAME} not found in DB, creating...")
            bank = Bank(name=self.BANK_NAME, slug='kuveyt-turk')
            self.session.add(bank)
            self.session.commit()
        return bank.id

    def _get_or_create_card(self, bank_id: int) -> int:
        card = self.session.query(Card).filter(
            Card.slug.in_(['saglam-kart', 'saglamkart'])
        ).first()
        if not card:
            card = self.session.query(Card).filter(
                Card.name.ilike('%Sağlam%'),
                Card.bank_id == bank_id
            ).first()
        if not card:
            print(f"⚠️  Card 'saglam-kart' not found, creating...")
            card = Card(bank_id=bank_id, name='Sağlam Kart', slug='saglam-kart', is_active=True)
            self.session.add(card)
            self.session.commit()
        return card.id

    def _clean(self, text: str) -> str:
        if not text: return ""
        text = str(text).replace('\xa0', ' ').replace('\r', '')
        text = re.sub(r'\s+', ' ', text)
        return text.strip()

    def _fetch_campaign_urls(self, limit: Optional[int] = None) -> tuple[List[str], List[str]]:
        print(f"📥 Fetching campaign list from {self.CAMPAIGNS_URL}...")
        all_campaign_links = set()
        expired_links = set()

        try:
            # Kuveyt Türk standard SSR response
            response = requests.get(self.CAMPAIGNS_URL, headers=self.headers, verify=False, timeout=20)
            response.raise_for_status()
            
            soup = BeautifulSoup(response.text, "html.parser")
            
            # Find campaign links - typical structure is in .box-content or similar
            campaign_items = soup.select(".box-content a, .campaign-list-item a, a.campaign-item")
            
            if not campaign_items:
                # Fallback: find all links containing /kampanyalar/
                campaign_items = soup.find_all("a", href=re.compile(r'/kampanyalar/'))

            for a in campaign_items:
                href = a.get("href")
                if not href or any(x in href.lower() for x in ["javascript", "#", "/arsiv", "/gecmis"]):
                    continue
                
                full_url = urljoin(self.BASE_URL, href)
                
                # Check for "expired" indicators in parent text
                parent = a.find_parent()
                parent_text = parent.get_text() if parent else ""
                if any(x in parent_text.lower() for x in ["sona erdi", "bitmiştir", "geçmiş"]):
                    expired_links.add(full_url)
                else:
                    all_campaign_links.add(full_url)
                    
            # Handle additional campaigns that might be under "Daha Fazla Göster"
            # The site uses a Load More button that triggers an AJAX request.
            # For now, we fetch the first page, and if we need more, we can implement pagination logic.
            # On Kuveyt Turk, the main listing page usually shows most recent ones.
            
        except Exception as e:
            print(f"   ❌ Fetch failed: {e}")
            return [], []

        unique_urls = list(all_campaign_links)
        unique_expired = list(expired_links)
        
        # If no limit is provided, process ALL found URLs
        if limit and limit > 0:
            unique_urls = unique_urls[:limit]

        print(f"✅ Found {len(unique_urls)} active campaigns, and {len(unique_expired)} expired campaigns")
        return unique_urls, unique_expired

    def _extract_campaign_data(self, url: str) -> Optional[Dict[str, Any]]:
        try:
            response = requests.get(url, headers=self.headers, verify=False, timeout=20)
            response.raise_for_status()
            
            html_content = response.text
            soup = BeautifulSoup(html_content, "html.parser")
            
            # Title extraction
            title_el = soup.select_one("h1, .campaign-title, .title h2, .subpage-header h1")
            title = self._clean(title_el.text) if title_el else "Başlık Yok"
            
            if any(x in url.lower() or x in title.lower() for x in ["gecmis", "geçmiş", "arsiv"]):
                return None

            # Main content area: Kuveyt Turk uses 2 columns inside .search-content
            # We explicitly exclude navigation and repetitive headers to keep AI text clean
            for unwanted in soup.select("header, nav, .nav-wrapper, .breadcrumb, footer, .subpage-header"):
                unwanted.decompose()

            content_row = soup.select_one("div.row.search-content")
            
            # 1. Main Text & Description logic
            main_description = ""
            conditions_text = ""
            if content_row:
                text_col = content_row.select_one("div.col-md-6:nth-child(1)")
                if text_col:
                    list_items = text_col.select("ul.list > li")
                    if list_items:
                        # First bullet is the main description
                        main_description = self._clean(list_items[0].get_text())
                        # Rest are conditions/details
                        conditions_text = "\n".join([f"- {self._clean(li.get_text())}" for li in list_items[1:]])
            
            # Fallback if specific structure fails
            if not main_description:
                content_div = soup.select_one(".search-content, .subpage-wrapper .container, .ck-content")
                if content_div:
                    # Clean the content_div as well
                    main_description = self._clean(content_div.get_text())[:800]
            
            # 2. Image extraction - CRITICAL FIX FOR KITAPYURDU & .vsf
            image_url = None
            # Look for ANY campaign image clues
            img_candidates = soup.select("img")
            for img in img_candidates:
                src = img.get("src") or img.get("data-src") or img.get("data-original")
                if not src or src.startswith("data:"): continue
                
                lower_src = src.lower()
                # Prioritize Campaign images or specific formats
                if any(x in lower_src for x in ["campaign", "kampanya", "detail", ".vsf", "banner"]):
                    image_url = urljoin(self.BASE_URL, src)
                    break
            
            # Absolute fallback: first large-ish image in search-content if none matched clues
            if not image_url and content_row:
                img_el = content_row.select_one("img")
                if img_el:
                    image_url = urljoin(self.BASE_URL, img_el.get("src") or img_el.get("data-src"))

            # 3. Comprehensive text for AI - STRENGTHENED LABELS
            full_raw_text = f"BAŞLIK: {title}\n\n"
            full_raw_text += f"KAMPANYA ANA ÖZETİ (AÇIKLAMA): {main_description}\n\n"
            full_raw_text += f"TÜM KAMPANYA KOŞULLARI VE KATILIM DETAYLARI:\n{conditions_text}\n\n"
            
            # Explicitly call out fields for AI in the raw text to guide parsing
            full_raw_text += "### AI PARSER TALİMATI:\n"
            full_raw_text += "- Yukarıdaki 'KOŞULLAR' listesindeki TÜM maddeleri 'conditions' alanına (liste olarak) dahil et.\n"
            full_raw_text += "- Kart isimlerini (Miles & Smiles, Sağlam Kart vb.) 'cards' alanına (liste olarak) yaz.\n"
            full_raw_text += "- Başlangıç tarihi metinde yoksa bugün ({datetime.now().strftime('%d %B %Y')}) olarak kabul et.\n"

            return {
                "title": title,
                "image_url": image_url,
                "description": main_description,
                "full_text": full_raw_text[:4000],
                "source_url": url,
                "raw_text": full_raw_text
            }
        except Exception as e:
            print(f"   ⚠️ Error extracting {url} via requests: {e}")
            return None

    def _parse_date(self, date_text: str, is_end: bool = False) -> Optional[str]:
        if not date_text:
            return None
        text = str(date_text).lower()
        
        months = {
            'ocak': '01', 'şubat': '02', 'subat': '02', 'mart': '03', 'nisan': '04',
            'mayıs': '05', 'mayis': '05', 'haziran': '06', 'temmuz': '07',
            'ağustos': '08', 'agustos': '08', 'eylül': '09', 'eylul': '09',
            'ekim': '10', 'kasım': '11', 'kasim': '11', 'aralık': '12', 'aralik': '12'
        }
        
        # "12 Mart 2024" or "12.03.2024"
        parts = re.split(r'[-\s/.]+', text)
        if len(parts) >= 3:
            matches = re.findall(r'(\d{1,2})[\s./]+([a-zşçğüöı]+|\d{1,2})[\s./]+(\d{4})', text)
            if matches:
                target = matches[-1] if is_end else matches[0] # Pick last for end date
                day = str(target[0]).zfill(2)
                month_val = target[1]
                year = target[2]
                month = months.get(month_val, str(month_val).zfill(2))
                try:
                    return f"{year}-{month}-{day}"
                except:
                    pass
        return None

    def _clean(self, text: str) -> str:
        if not text: return ""
        text = str(text).replace('\xa0', ' ').replace('\r', '')
        text = re.sub(r'\s+', ' ', text)
        return text.strip()

    def _generate_slug(self, title: str) -> str:
        slug = str(title).lower()
        replacements = {
            'ı': 'i', 'ğ': 'g', 'ü': 'u', 'ş': 's', 'ö': 'o', 'ç': 'c',
            'İ': 'i', 'Ğ': 'g', 'Ü': 'u', 'Ş': 's', 'Ö': 'o', 'Ç': 'c'
        }
        for tr, eng in replacements.items():
            slug = slug.replace(tr, eng)
        slug = re.sub(r'[^a-z0-9\s-]', '', slug)
        slug = re.sub(r'[\s-]+', '-', slug).strip('-')
        return slug

    def _save_campaign(self, bank_id: int, card_id: int, parsed_data: Dict[str, Any], raw_data: Dict[str, Any]):
        title = raw_data["title"]
        slug = self._generate_slug(title)
        source_url = raw_data["source_url"]

        campaign = self.session.query(Campaign).filter_by(tracking_url=source_url).first()
        is_new = not campaign

        if not campaign:
            campaign = Campaign(
                tracking_url=source_url,
                card_id=card_id,
                created_at=datetime.utcnow()
            )
            self.session.add(campaign)

        # 1. Update core fields
        campaign.slug = slug
        campaign.title = title
        campaign.description = parsed_data.get("description") or raw_data.get("description", "")
        campaign.image_url = raw_data.get("image_url") or parsed_data.get("image_url")
        campaign.is_active = True
        campaign.updated_at = datetime.utcnow()
        campaign.clean_text = raw_data.get("raw_text")
        
        # 2. Update dates
        current_date_str = datetime.now().strftime("%Y-%m-%d")
        # Start date: logic in the scraper handles parsing, but AI provides a backup
        start_date_str = self._parse_date(raw_data.get("date_text"), is_end=False)
        if start_date_str:
            campaign.start_date = datetime.strptime(start_date_str, "%Y-%m-%d").date()
        elif parsed_data.get("start_date") and parsed_data["start_date"] != "None":
            try: campaign.start_date = datetime.strptime(parsed_data["start_date"].split('T')[0], "%Y-%m-%d").date()
            except: campaign.start_date = datetime.now().date()
        else:
            campaign.start_date = datetime.now().date() # Fallback to today as per user request

        end_date_str = self._parse_date(raw_data.get("date_text"), is_end=True)
        if end_date_str:
            campaign.end_date = datetime.strptime(end_date_str, "%Y-%m-%d").date()
        elif parsed_data.get("end_date") and parsed_data["end_date"] != "None":
            try: campaign.end_date = datetime.strptime(parsed_data["end_date"].split('T')[0], "%Y-%m-%d").date()
            except: pass

        # 3. Update metadata (Using AI Parser's standard keys)
        campaign.sector = parsed_data.get("sector") or "diger"
        campaign.brands = parsed_data.get("brands") or []
        campaign.eligible_cards = parsed_data.get("cards") or [] # AI prompt uses 'cards'
        campaign.participation_steps = parsed_data.get("participation") or "Kampanya otomatik katılımlıdır."
        campaign.conditions = parsed_data.get("conditions") or [] # AI prompt uses 'conditions'
        campaign.reward_text = parsed_data.get("reward_text")
        campaign.reward_type = parsed_data.get("reward_type")
        campaign.reward_value = parsed_data.get("reward_value")
        campaign.min_spend = parsed_data.get("min_spend")

        # Setup AI Marketing text
        part_text = campaign.participation_steps if isinstance(campaign.participation_steps, str) else str(campaign.participation_steps)
        campaign.ai_marketing_text = f"📱 Katılım: {part_text}"

        # Sector Mapping
        sector_slug = parsed_data.get("sector")
        if sector_slug:
            sector = self.session.query(Sector).filter_by(slug=sector_slug).first()
            if sector:
                 campaign.sector_id = sector.id

        try:
            self.session.flush()
            
            # 4. Handle Brands (Fixed get_or_create to avoid name unique violation)
            brands_list = parsed_data.get("brands", [])
            if isinstance(brands_list, list) and brands_list:
                # Clear old mapping
                self.session.query(CampaignBrand).filter_by(campaign_id=campaign.id).delete()
                
                for brand_name in brands_list:
                    if not brand_name or type(brand_name) is not str: continue
                    brand_name = brand_name.strip()
                    bslug = self._generate_slug(brand_name)
                    if not bslug: continue
                    
                    # Check by name OR slug to be safe
                    brand_obj = self.session.query(Brand).filter((Brand.slug == bslug) | (Brand.name == brand_name)).first()
                    if not brand_obj:
                        brand_obj = Brand(name=brand_name[:255], slug=bslug[:255])
                        self.session.add(brand_obj)
                        self.session.flush()
                        
                    cb = CampaignBrand(campaign_id=campaign.id, brand_id=brand_obj.id)
                    self.session.merge(cb)
                    
            self.session.commit()
            print(f"   ✓ Saved: {title}")
            return is_new, True
        except Exception as e:
            self.session.rollback()
            print(f"   ❌ DB Error saving {title}: {e}")
            return False, False

    def disable_expired_campaigns(self, expired_urls: List[str]):
        if not expired_urls: return
        print(f"\n🔄 Passivating {len(expired_urls)} expired campaigns...")
        count = 0
        for url in expired_urls:
            camp = self.session.query(Campaign).filter_by(tracking_url=url, is_active=True).first()
            if camp:
                camp.is_active = False
                camp.updated_at = datetime.utcnow()
                count += 1
        if count > 0:
            self.session.commit()
            print(f"✅ Passivated {count} campaigns.")

    def run(self, limit: Optional[int] = None):
        print(f"\n🚀 Starting {self.BANK_NAME} scraper...")
        start_time = time.time()
        stats = {'total': 0, 'new': 0, 'updated': 0, 'failed': 0}
        
        try:
            bank_id = self._get_or_create_bank()
            card_id = self._get_or_create_card(bank_id)
            
            urls, expired_urls = self._fetch_campaign_urls(limit)
            self.disable_expired_campaigns(expired_urls)
            
            for index, url in enumerate(urls, 1):
                print(f"\n[{index}/{len(urls)}] Processing: {url}")
                stats['total'] += 1
                
                # Exists check before heavy compute
                existing = self.session.query(Campaign).filter_by(tracking_url=url).first()
                is_test_mode = os.getenv("TEST_MODE") == "1"
                
                if existing and not is_test_mode:
                    if existing.updated_at and (datetime.utcnow() - existing.updated_at).days < 2:
                        print(f"   ⏭️  Skipping recently updated campaign.")
                        continue
                     
                raw_data = self._extract_campaign_data(url)
                if not raw_data:
                    stats['failed'] += 1
                    continue
                
                print("   🤖 Parsing with AI...")
                try:
                    parsed_data = self.parser.parse_campaign_data(
                        raw_text=raw_data["raw_text"], bank_name=self.BANK_NAME
                    ) or {}
                except Exception as e:
                    print(f"   ⚠️ AI parse error: {e}")
                    parsed_data = {}
                    
                if not parsed_data:
                    print("   ❌ AI Parse failed")
                    stats['failed'] += 1
                    continue
                    
                is_new, success = self._save_campaign(bank_id, card_id, parsed_data, raw_data)
                if success:
                    if is_new: stats['new'] += 1
                    else: stats['updated'] += 1
                else:
                    stats['failed'] += 1
                    
                time.sleep(1) # Breath
                
            elapsed = time.time() - start_time
            print(f"\n🎉 {self.BANK_NAME} scraping completed in {elapsed:.1f}s")
            print(f"📊 Stats: {stats['total']} processed | {stats['new']} new | {stats['updated']} updated | {stats['failed']} failed")
            
            log_scraper_execution(
                db=self.session,
                scraper_name=f"{self.BANK_NAME} Scraper",
                status="COMPLETED",
                total_found=stats['total'],
                total_saved=stats['new'] + stats['updated'],
                total_failed=stats['failed']
            )
            
        except Exception as e:
            traceback.print_exc()
            log_scraper_execution(
                db=self.session,
                scraper_name=f"{self.BANK_NAME} Scraper",
                status="FAILED",
                error_details={"error": str(e)}
            )
        finally:
            self.session.close()

if __name__ == "__main__":
    limit_arg = None
    if len(sys.argv) > 1:
        try: limit_arg = int(sys.argv[1])
        except: pass
    scraper = KuveytTurkScraper()
    scraper.run(limit=limit_arg)

