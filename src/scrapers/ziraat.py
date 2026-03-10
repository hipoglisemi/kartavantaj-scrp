
import sys
import time
import re
import uuid
import requests
import json
import os
import traceback
from bs4 import BeautifulSoup
from datetime import datetime
from urllib.parse import urljoin
from sqlalchemy import create_engine, Column, Integer, String, Boolean, DateTime, Date, Numeric, Text, ForeignKey
from sqlalchemy.orm import sessionmaker, relationship, declarative_base
from sqlalchemy.dialects.postgresql import UUID

# --- CONFIGURATION ---
# (Will load AFTER environment variables)

# Import AI Parser from sibling directory
current_dir = os.path.dirname(os.path.abspath(__file__)) # src/scrapers
project_root = os.path.dirname(os.path.dirname(current_dir)) # /Users/.../kartavantaj-scraper
if project_root not in sys.path:
    sys.path.append(project_root)

try:
    from src.services.ai_parser import AIParser
except ImportError:
    sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from src.services.ai_parser import AIParser

# Load Env
try:
    from dotenv import load_dotenv
    load_dotenv()
    with open('.env', 'r') as f:
        for line in f:
            if line.strip() and not line.startswith('#'):
                k, v = line.strip().split('=', 1)
except: pass

DATABASE_URL = os.environ.get("DATABASE_URL")

# --- MODELS ---
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
    is_active = Column(Boolean, name="is_active", default=True)
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
    reward_text = Column(String, name="reward_text")
    reward_value = Column(Numeric, name="reward_value")
    reward_type = Column(String, name="reward_type")
    conditions = Column(String)
    eligible_cards = Column(String, name="eligible_cards")
    image_url = Column(String, name="image_url")
    start_date = Column(Date, name="start_date")
    end_date = Column(Date, name="end_date")
    is_active = Column(Boolean, name="is_active", default=True)
    tracking_url = Column(String, name="tracking_url")
    created_at = Column(DateTime, name="created_at", default=datetime.utcnow)
    updated_at = Column(DateTime, name="updated_at", default=datetime.utcnow)
    ai_marketing_text = Column(String, name="ai_marketing_text")
    
    card = relationship("Card", back_populates="campaigns")
    sector = relationship("Sector", back_populates="campaigns")
    brands = relationship("CampaignBrand", back_populates="campaign")


class ZiraatScraper:
    BASE_URL = "https://www.bankkart.com.tr"
    LIST_URL = "https://www.bankkart.com.tr/kampanyalar"

    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        })
        self.engine = create_engine(DATABASE_URL)
        Session = sessionmaker(bind=self.engine)
        self.db = Session()
        self.parser = AIParser()
        
        # Ensure Bank & Card
        self.bank = self.db.query(Bank).filter(
            (Bank.slug == 'ziraat-bankasi') | 
            (Bank.slug == 'ziraat') | 
            (Bank.name.ilike('%Ziraat%'))
        ).first()

        if not self.bank:
            self.bank = Bank(name='Ziraat Bankası', slug='ziraat-bankasi')
            self.db.add(self.bank)
            self.db.commit()
            
        self.card = self.db.query(Card).filter(Card.slug == 'bankkart').first()
        if not self.card:
             self.card = Card(bank_id=self.bank.id, name='Bankkart', slug='bankkart', is_active=True)
             self.db.add(self.card)
             self.db.commit()
        
        self.card_id = self.card.id

    def _fetch_campaign_list(self):
        print(f"📄 Fetching all campaigns via API...")
        campaigns = []
        seen_urls = set()

        # Ziraat API: indexNo=1..N, each page returns 8 items as {"Items": [...]}
        # When exhausted, API returns [] (empty JSON array, NOT a dict)
        page = 1
        consecutive_empty = 0

        while True:
            ajax_url = f"https://www.bankkart.com.tr/api/Campaigns/GetMoreShow?indexNo={page}&type=Bireysel"
            print(f"   -> Fetching API page {page}: {ajax_url}")

            try:
                resp = self.session.get(ajax_url, timeout=30)
                if resp.status_code != 200:
                    print(f"   ⚠️ API returned status {resp.status_code}, stopping.")
                    break

                try:
                    data = resp.json()
                except Exception:
                    print(f"   ⚠️ Failed to parse JSON at page {page}, stopping.")
                    break

                # API returns [] when exhausted (not a dict!)
                if isinstance(data, list):
                    print(f"   ℹ️ Empty list response at page {page} — all campaigns fetched.")
                    break

                if not isinstance(data, dict):
                    print(f"   ⚠️ Unexpected API response type: {type(data)}, stopping.")
                    break

                new_items = data.get('Items', [])

                if not new_items:
                    consecutive_empty += 1
                    print(f"   ℹ️ No items at page {page} (empty #{consecutive_empty}).")
                    if consecutive_empty >= 2:
                        break
                    page += 1
                    time.sleep(0.5)
                    continue

                consecutive_empty = 0

                for item in new_items:
                    seo_name = item.get('SeoName')
                    cat = item.get('Category', {})
                    cat_seo = cat.get('SeoName', 'diger-kampanyalar') if isinstance(cat, dict) else 'diger-kampanyalar'

                    if not seo_name:
                        continue

                    full_url = f"https://www.bankkart.com.tr/kampanyalar/{cat_seo}/{seo_name}"

                    if full_url in seen_urls:
                        continue
                    seen_urls.add(full_url)

                    img_url = urljoin(self.BASE_URL, item.get('ImageUrl')) if item.get('ImageUrl') else None

                    end_date_iso = item.get('EndDate')
                    end_date_str = None
                    if end_date_iso:
                        try:
                            dt = datetime.fromisoformat(end_date_iso)
                            end_date_str = dt.strftime("%d.%m.%Y")
                        except Exception:
                            pass

                    campaigns.append({
                        "url": full_url,
                        "image_url": img_url,
                        "list_end_date": end_date_str
                    })

                print(f"   -> Found {len(new_items)} items on page {page} (total so far: {len(campaigns)}).")
                page += 1
                time.sleep(0.8)

                # Safety limit: 200 pages × 8 items = 1600 campaigns max
                if page > 200:
                    print("   ⚠️ Safety limit (200 pages) reached.")
                    break

            except Exception as e:
                print(f"   ⚠️ Error on page {page}: {e}")
                break

        print(f"   ✅ Total found: {len(campaigns)} items.")
        return campaigns


    def _process_campaign(self, campaign_data):
        url = campaign_data['url']
        print(f"🔍 Processing (AI Enabled): {url}")
        
        try:
            response = self.session.get(url, timeout=30)
            html = response.text
            soup = BeautifulSoup(html, 'html.parser')

            # --- ENHANCED CONTENT EXTRACTION ---
            # Ziraat puts conditions in Tabs (#tab-1, #tab-2 etc)
            # We need to explicitly fetch them
            
            main_content = soup.select_one('.subpage-detail')
            content_text = main_content.get_text(separator=' ', strip=True) if main_content else ""

            # Append Tab Contents (Conditions, Cards)
            tabs = soup.select('.tabs-content .tab-content')
            for tab in tabs:
                content_text += "\n" + tab.get_text(separator='\n', strip=True)

            # Fallback: specific IDs used by Ziraat
            if "Katılım Koşulları" not in content_text:
                 specific_tabs = soup.select('#tab-1, #tab-2, #tab-3, #tab-4')
                 for st in specific_tabs:
                     content_text += "\n" + st.get_text(separator='\n', strip=True)
            
            # -----------------------------------

            # 1. Try to get High-Res Image from Detail Page
            detail_img = None
            # Try #firstImg (Legacy scraper used this)
            img_el = soup.select_one('#firstImg')
            if not img_el:
                img_el = soup.select_one('.subpage-detail figure img')
            
            if img_el and img_el.get('src'):
                detail_img = urljoin(self.BASE_URL, img_el['src'])
            
            final_image = detail_img if detail_img else campaign_data.get('image_url')

            # 2. Inject Date Hint to AI
            date_hint = ""
            if campaign_data.get('list_end_date'):
                date_hint = f"\nİPUCU: Kampanya Bitiş Tarihi: {campaign_data['list_end_date']} (Bunu referans al, yılı buradan doğrulayabilirsin)"

            # 3. Inject Sector Hint from URL
            # URL: .../kampanyalar/market-ve-gida/...
            sector_hint = ""
            try:
                parts = url.split('/kampanyalar/')
                if len(parts) > 1:
                    category_slug = parts[1].split('/')[0]
                    sector_hint = f"\nİPUCU: Kampanya Kategorisi Linkte '{category_slug}' olarak geçiyor. Buna uygun Sektör seç."
            except: pass

            # AI PARSING
            ai_data = self.parser.parse_campaign_data(
                raw_text=content_text + date_hint + sector_hint, # Use ENHANCED content + HINTS
                bank_name="ziraat"
            )
            
            if not ai_data:
                print("   ❌ AI Parsing failed.")
                return "error"

            title = ai_data.get("title", "Kampanya")
            desc = ai_data.get("description", "")
            
            # Map Sector
            cat_map = {
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
                "Kültür & Sanat": "Eğlence",
                "Diğer": "Diğer"
            }
            ai_cat = ai_data.get("sector", "Diğer")
            db_sector_name = cat_map.get(ai_cat, "Diğer")
            
            sector = self.db.query(Sector).filter(Sector.slug == db_sector_name).first()
            if not sector: sector = self.db.query(Sector).filter(Sector.slug == 'diger').first()
            
            # Slug
            base_slug = re.sub(r'[^a-z0-9]+', '-', title.lower()).strip('-')
            slug = base_slug
            counter = 1
            while self.db.query(Campaign).filter(Campaign.slug == slug).first():
                slug = f"{base_slug}-{counter}"
                counter += 1

            # Conditions & Participation
            conds = ai_data.get("conditions", [])
            if isinstance(conds, str):
                conds = [c.strip() for c in conds.split("\n") if c.strip()]
            part_method = ai_data.get("participation")
            if part_method and "Detayları İnceleyin" not in part_method:
                conds.insert(0, f"KATILIM: {part_method}")
            final_conditions = "\n".join(conds)

            cards_raw = ai_data.get("cards", [])
            if isinstance(cards_raw, str):
                cards_raw = [c.strip() for c in cards_raw.split(",") if c.strip()]

            # Dates
            vf = None
            vu = None
            # Tey safe parsing from AI
            if ai_data.get("start_date"):
                try: vf = datetime.strptime(ai_data.get("start_date"), "%Y-%m-%d")
                except: pass
            if ai_data.get("end_date"):
                try: vu = datetime.strptime(ai_data.get("end_date"), "%Y-%m-%d")
                except: pass
            
            # Fallback for End Date from list page if AI missed it
            if not vu and campaign_data['list_end_date']:
                try:
                    # "Son Gün 28.2.2026"
                    clean_date = campaign_data['list_end_date'].replace("Son Gün", "").strip()
                    vu = datetime.strptime(clean_date, "%d.%m.%Y")
                except: pass

            # DB Upsert
            existing = self.db.query(Campaign).filter(Campaign.tracking_url == url).first()
            if existing:
                print(f"   ⏭️ Skipped (Already exists, preserving manual edits): {title[:50]}...")
                return "skipped"

            campaign = Campaign(
                card_id=self.card_id,
                sector_id=sector.id if sector else None,
                slug=slug,
                title=title,
                description=desc,
                reward_text=ai_data.get("reward_text"),
                        clean_text=ai_data.get('_clean_text'),
                reward_value=ai_data.get("reward_value"),
                conditions=final_conditions,
                eligible_cards=", ".join(cards_raw),
                image_url=final_image,
                start_date=vf,
                end_date=vu,
                is_active=True,
                tracking_url=url,
                created_at=datetime.utcnow(),
                updated_at=datetime.utcnow()
            )
            self.db.add(campaign)
            
            self.db.commit()

            # BRANDS
            brands = ai_data.get("brands", [])
            if brands:
                for b_name in brands:
                    if len(b_name) < 2: continue
                    b_slug = re.sub(r'[^a-z0-9]+', '-', b_name.lower()).strip('-')
                    brand = self.db.query(Brand).filter(Brand.slug == b_slug).first()
                    if not brand: brand = self.db.query(Brand).filter(Brand.name.ilike(b_name)).first()
                    if not brand: 
                        brand = Brand(name=b_name, slug=b_slug)
                        self.db.add(brand)
                        self.db.commit()
                    
                    link = self.db.query(CampaignBrand).filter(
                        CampaignBrand.campaign_id == campaign.id,
                        CampaignBrand.brand_id == brand.id
                    ).first()
                    if not link:
                        link = CampaignBrand(campaign_id=campaign.id, brand_id=brand.id)
                        self.db.add(link)
                        self.db.commit()

            print(f"   ✅ Saved: {title} | End: {vu}")
            return "saved"
            
        except Exception as e:
            print(f"   ❌ Error processing {url}: {e}")
            self.db.rollback()
            traceback.print_exc()
            return "error"

    def run(self):
        print("🚀 Starting Ziraat Bank Scraper...")
        campaigns = self._fetch_campaign_list()
        
        # Check environment limit
        max_campaigns = os.environ.get("MAX_CAMPAIGNS_PER_RUN")
        limit = int(max_campaigns) if max_campaigns else 999
        
        count = 0
        success_count = 0
        skipped_count = 0
        failed_count = 0
        error_details = []

        for camp in campaigns:
            if count >= limit:
                print(f"🛑 Reached MAX_CAMPAIGNS_PER_RUN limit ({limit})")
                break
            
            try:
                res = self._process_campaign(camp)
                if res == "saved": success_count += 1
                elif res == "skipped": skipped_count += 1
                else: 
                    failed_count += 1
                    error_details.append({"url": camp.get('url', 'unknown'), "error": "Unknown DB failure"})
            except Exception as e:
                failed_count += 1
                error_details.append({"url": camp.get('url', 'unknown'), "error": str(e)})
            
            count += 1
            time.sleep(2)
        print(f"✅ Özet: {len(campaigns)} bulundu, {success_count} eklendi, {skipped_count} atlandı, {failed_count} hata aldı.")
        
        status = "SUCCESS"
        if failed_count > 0:
             status = "PARTIAL" if (success_count > 0 or skipped_count > 0) else "FAILED"
             
        try:
            from src.utils.logger_utils import log_scraper_execution
            log_scraper_execution(
                 db=self.db,
                 scraper_name="ziraat",
                 status=status,
                 total_found=len(campaigns),
                 total_saved=success_count,
                 total_skipped=skipped_count,
                 total_failed=failed_count,
                 error_details={"errors": error_details} if error_details else None
            )
        except Exception as le:
             print(f"⚠️ Could not save scraper log: {le}")
             
        print("🏁 Finished.")

if __name__ == "__main__":
    scraper = ZiraatScraper()
    scraper.run()
