import sys
import os
import time
import re
import uuid
import traceback
from datetime import datetime
from typing import Optional, Dict, Any, List, Tuple
from bs4 import BeautifulSoup
from urllib.parse import urljoin
import requests
import json

# Path setup
current_dir = os.path.dirname(os.path.abspath(__file__))  # src/scrapers
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
    campaign_id = Column(Integer, ForeignKey('campaigns.id'), primary_key=True)
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

class AlbarakaScraper:
    """Albaraka Türk bank campaign scraper"""

    BASE_URL = "https://www.albaraka.com.tr"
    API_URL = "https://www.albaraka.com.tr/plugins/GetCampaigns"
    BANK_NAME = "Albaraka Türk"
    CARD_SLUG = "albaraka-kredi-karti"

    def __init__(self):
        if not DATABASE_URL:
            raise ValueError("DATABASE_URL is not set")
        self.engine = create_engine(DATABASE_URL, pool_pre_ping=True, pool_recycle=300)
        Session = sessionmaker(bind=self.engine)
        self.session = Session()
        self.headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept": "application/json, text/javascript, */*; q=0.01",
            "X-Requested-With": "XMLHttpRequest",
            "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
            "Origin": "https://www.albaraka.com.tr",
            "Referer": "https://www.albaraka.com.tr/tr/kampanyalar"
        }
        
        try:
            from src.services.ai_parser import AIParser as _AIParser
            self.parser = _AIParser()
        except ImportError:
            from services.ai_parser import AIParser as _AIParser
            self.parser = _AIParser()

    def _get_or_create_bank(self) -> int:
        bank = self.session.query(Bank).filter(
            Bank.slug.in_(['albaraka', 'albaraka-turk'])
        ).first()
        if not bank:
            bank = self.session.query(Bank).filter(
                Bank.name.ilike('%Albaraka%')
            ).first()
        if not bank:
            print(f"⚠️  {self.BANK_NAME} not found in DB, creating...")
            bank = Bank(name=self.BANK_NAME, slug='albaraka')
            self.session.add(bank)
            self.session.commit()
        return bank.id

    def _get_or_create_card(self, bank_id: int) -> int:
        card = self.session.query(Card).filter(
            Card.slug.in_([self.CARD_SLUG, 'albaraka'])
        ).first()
        if not card:
            card = self.session.query(Card).filter(
                Card.name.ilike('%Albaraka%'),
                Card.bank_id == bank_id
            ).first()
        if not card:
            print(f"⚠️  Card '{self.CARD_SLUG}' not found, creating...")
            card = Card(bank_id=bank_id, name='Albaraka Kredi Kartı', slug=self.CARD_SLUG, is_active=True)
            self.session.add(card)
            self.session.commit()
        return card.id

    def _fetch_campaign_list(self) -> List[Dict[str, Any]]:
        print(f"📥 Fetching campaign list from Albaraka API...")
        import urllib3
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
        
        all_campaigns = []
        page_index = 1
        page_size = 9
        total_count = None
        
        while True:
            data = {
                "langId": "bf2689d9-071e-4a20-9450-b1dbdd39778f",
                "language": "tr",
                "Slug": "kampanyalar",
                "PageIndex": page_index,
                "PageSize": page_size,
                "searchUrl": "/tr/arama"
            }
            try:
                # Add retry logic for API stability
                max_retries = 3
                for attempt in range(max_retries):
                    try:
                        response = requests.post(self.API_URL, data=data, headers=self.headers, verify=False, timeout=20)
                        response.raise_for_status()
                        res_json = response.json()
                        break
                    except Exception as api_err:
                        if attempt == max_retries - 1:
                            raise api_err
                        print(f"   ⚠️ API attempt {attempt+1} failed, retrying in 3s... ({api_err})")
                        time.sleep(3)

                campaigns_list = res_json.get("Data", {}).get("Campaigns", [])
                if total_count is None:
                    total_count = res_json.get("Data", {}).get("TotalCount", 0)
                    
                if not campaigns_list:
                    break
                    
                all_campaigns.extend(campaigns_list)
                print(f"   ✓ Fetched page {page_index} ({len(all_campaigns)}/{total_count})")
                
                if len(all_campaigns) >= total_count:
                    break
                    
                page_index += 1
                time.sleep(1.5) # Consistent delay
                
            except Exception as e:
                print(f"   ❌ Failed to fetch campaign list on page {page_index} after retries: {e}")
                break
                
        # Filter out expired campaigns based on title or content if obvious, but usually Albaraka removes them
        unique_urls = set()
        active_campaigns = []
        
        for camp in all_campaigns:
            link = camp.get("Link")
            if not link:
                continue
            
            full_url = urljoin(self.BASE_URL, link)
            if full_url in unique_urls:
                continue
            unique_urls.add(full_url)
            
            title = camp.get("Title", "").lower()
            if "sona eren" in title or "süresi dolan" in title:
                continue
                
            active_campaigns.append({
                "url": full_url,
                "title": camp.get("Title", ""),
                "summary": camp.get("Content", ""),
                "image_url": urljoin(self.BASE_URL, camp.get("CampaignImage", "")) if camp.get("CampaignImage") else None,
                "end_date_str": camp.get("EndDate", "")
            })

        print(f"✅ Found {len(active_campaigns)} active campaigns")
        return active_campaigns

    def _extract_campaign_details(self, url: str) -> Optional[str]:
        """Extract campaign details using requests (SSR content found in raw HTML)"""
        try:
            import urllib3
            urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
            
            # Increase timeout and stay with requests for stability
            response = requests.get(url, headers=self.headers, verify=False, timeout=20)
            response.raise_for_status()
            
            html_content = response.text
            soup = BeautifulSoup(html_content, "html.parser")
            
            # Remove scripts, styles, and navigational elements
            for s in soup(["script", "style", "nav", "footer", "header", "noscript"]):
                s.extract()
            
            # Extract text from the main container or the whole body
            # We found that Albaraka content is in the SSR even if JS moves it later
            main_text = soup.get_text(separator="\n", strip=True)
            
            # AI Helper: If we see "Katılım Adımları", make it more obvious
            if "Katılım Adımları" in main_text:
                main_text = main_text.replace("Katılım Adımları", "\n\n### KAMPANYAYA KATILIM ADIMLARI:\n")
            
            # Helper to find date in raw HTML via regex to aid AI
            # Looking for DD.MM.YYYY - DD.MM.YYYY
            date_match = re.search(r'(\d{2}\.\d{2}\.\d{4}\s*[-–]\s*\d{2}\.\d{2}\.\d{4})', html_content)
            date_info = ""
            if date_match:
                date_info = f"\n\nKAMPANYA TARIHI (KESIN): {date_match.group(1)}\n"
            
            return self._clean(main_text) + date_info

        except Exception as e:
            print(f"   ⚠️ Error extracting details from {url} via requests: {e}")
            return None

    def _parse_date(self, date_text: str, is_end: bool = False) -> Optional[str]:
        if not date_text:
            return None
        text = date_text.replace("İ", "i").lower()
        
        # Format: "Son gün 31 Mart" or "31 Mart'a kadar"
        months = {
            "ocak": "01", "şubat": "02", "mart": "03", "nisan": "04",
            "mayıs": "05", "haziran": "06", "temmuz": "07", "ağustos": "08",
            "eylül": "09", "ekim": "10", "kasım": "11", "aralık": "12",
        }
        
        # Check dd.mm.yyyy format first: 1.1.2026 - 31.12.2026
        pattern_dot = r"(\d{1,2})\.(\d{1,2})\.(\d{4})"
        match_dots = re.findall(pattern_dot, text)
        if match_dots:
            if is_end or len(match_dots) == 1:
                target = match_dots[-1]
            else:
                target = match_dots[0]
            d, m, y = target
            return f"{y}-{m.zfill(2)}-{d.zfill(2)}"

        try:
            pattern = r"(\d{1,2})\s*([a-zğüşıöç]+)\s*(\d{4})?"
            matches = list(re.finditer(pattern, text))
            
            if matches:
                if is_end or len(matches) == 1:
                    match = matches[-1]
                else:
                    match = matches[0]
                    
                day, month_str, year = match.groups()
                month = months.get(month_str, "12")
                
                # If no year is specified, assume current year, or next year if month already passed
                if not year:
                    current_year = datetime.now().year
                    current_month = datetime.now().month
                    if int(month) < current_month - 1: # generous padding
                        year = str(current_year + 1)
                    else:
                        year = str(current_year)
                        
                return f"{year}-{month}-{day.zfill(2)}"
        except Exception:
            pass
        return None

    def _clean(self, text: str) -> str:
        if not text:
            return ""
        # Collapse multiple newlines and spaces
        text = re.sub(r'\n+', '\n', text)
        return re.sub(r'[ \t]+', ' ', text).strip()

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

            db_sector_name = data.get("sector", "diger")
            if isinstance(db_sector_name, list):
                db_sector_name = db_sector_name[0] if db_sector_name else "diger"
                
            sector = self.session.query(Sector).filter(Sector.slug == db_sector_name).first()
            if not sector:
                sector = self.session.query(Sector).filter(Sector.slug == 'diger').first()

            start_date = None
            end_date = None
            if data.get("start_date"):
                try: start_date = datetime.strptime(data["start_date"], "%Y-%m-%d").date()
                except Exception: pass
            if data.get("end_date"):
                try: end_date = datetime.strptime(data["end_date"], "%Y-%m-%d").date()
                except Exception: pass
                
            if not end_date and data.get("end_date_str"):
                ed = self._parse_date(data["end_date_str"], is_end=True)
                if ed:
                    try: end_date = datetime.strptime(ed, "%Y-%m-%d").date()
                    except Exception: pass

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
                image_url=data.get("image_url"),
                start_date=start_date,
                end_date=end_date,
                clean_text=data.get('raw_text'),
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
        bank_id = self._get_or_create_bank()
        card_id = self._get_or_create_card(bank_id)

        print(f"✅ Bank: {self.BANK_NAME} (ID: {bank_id})")
        print(f"✅ Card: {self.CARD_SLUG} (ID: {card_id})")
        print("🚀 Starting Albaraka Scraper (API)...")

        try:
            campaigns_list = self._fetch_campaign_list()
            
            results = []
            success, skipped, failed = 0, 0, 0
            error_details = []
            
            for i, camp in enumerate(campaigns_list, 1):
                if limit and i > limit:
                    break
                    
                url = camp["url"]
                print(f"\n[{i}/{len(campaigns_list)}]")
                print(f"🔍 Processing: {url}")
                
                existing = self.session.query(Campaign).filter(
                    Campaign.tracking_url == url,
                    Campaign.card_id == card_id
                ).first()
                if existing:
                    print(f"   ℹ️  Already exists in DB: [{existing.id}] {existing.title[:40]}")
                    skipped += 1
                    continue
                    
                try:
                    full_text = self._extract_campaign_details(url)
                    if not full_text:
                        full_text = camp["summary"] # Fallback to summary
                    
                    combined_text = f"{camp['title']}\n{camp['summary']}\n{full_text}"
                    
                    ai_data = self.parser.parse_campaign_data(
                        raw_text=combined_text[:6000], # Limit to 6k chars
                        title=camp["title"],
                        bank_name=self.BANK_NAME,
                        card_name="Albaraka Kredi Kartı"
                    )
                    
                    res_data = {
                        "title": camp["title"],
                        "image_url": camp["image_url"],
                        "source_url": url,
                        "raw_text": combined_text[:6000],
                        "end_date_str": camp["end_date_str"]
                    }
                    
                    if ai_data:
                        print("   ✅ AI parsed successfully")
                        res_data.update(ai_data)
                    else:
                        print("   ⚠️ AI parse returned None, attempting to save with basic data")
                        
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

            print(f"\n🏁 Finished. {len(campaigns_list)} found, {success} saved, {skipped} skipped, {failed} errors")
            
            status = "SUCCESS"
            if failed > 0:
                status = "PARTIAL" if (success > 0 or skipped > 0) else "FAILED"
                
            log_scraper_execution(
                db=self.session,
                scraper_name="albaraka",
                status=status,
                total_found=len(campaigns_list),
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
                    scraper_name="albaraka",
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
    
    scraper = AlbarakaScraper()
    scraper.run(limit=args.limit)
