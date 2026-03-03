
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

# Import AI Parser from sibling directory
# We need to add the project root to sys.path
current_dir = os.path.dirname(os.path.abspath(__file__)) # src/scrapers
project_root = os.path.dirname(os.path.dirname(current_dir)) # /Users/.../kartavantaj-scraper
if project_root not in sys.path:
    sys.path.append(project_root)

try:
    from src.services.ai_parser import AIParser
except ImportError:
    # If running from different context, try adding parent of src
    sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from src.services.ai_parser import AIParser

# Load Env (for DB and API Key)
try:
    # Try loading from local kartavantaj .env first (if running from there)
    with open('.env', 'r') as f:
        for line in f:
            if line.strip() and not line.startswith('#'):
                k, v = line.strip().split('=', 1)
                os.environ[k] = v.strip('"\'')
except: pass

# Also try loading from scraper project .env
try:
    with open(os.path.join(project_root, '.env'), 'r') as f:
        for line in f:
             if line.strip() and not line.startswith('#'):
                k, v = line.strip().split('=', 1)
                # Don't overwrite if already set
                if k not in os.environ:
                     os.environ[k] = v.strip('"\'')
except: pass

# --- CONFIGURATION ---
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


# --- SCRAPER ---
class VakifbankScraper:
    BASE_URL = "https://www.vakifkart.com.tr"
    LIST_URL_TEMPLATE = "https://www.vakifkart.com.tr/kampanyalar/sayfa/{}"

    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        })
        self.engine = create_engine(DATABASE_URL)
        Session = sessionmaker(bind=self.engine)
        self.db = Session()
        
        # Initialize AI Parser
        self.parser = AIParser() # Ensure GEMINI_API_KEY is in env
        
        # Ensure Bank
        bank_slug = 'vakifbank'
        self.bank = self.db.query(Bank).filter(Bank.slug == bank_slug).first()
        if not self.bank:
            self.bank = Bank(name='VakıfBank', slug=bank_slug)
            self.db.add(self.bank)
            self.db.commit()
            
        # Ensure Card
        card_slug = 'vakifworld'
        # Fallback Name: Since user renamed it VakıfWorld, default to VakıfWorld for new inserts, but search by slug always.
        self.card = self.db.query(Card).filter(Card.slug == card_slug).first()
        if not self.card:
             self.card = Card(bank_id=self.bank.id, name='VakıfWorld', slug=card_slug, is_active=True)
             self.db.add(self.card)
             self.db.commit()
        
        self.card_id = self.card.id

    def _fetch_campaign_list(self, limit_pages=None):
        campaign_urls = []
        page = 1
        while True:
            if limit_pages and page > limit_pages: break
            print(f"📄 Fetching page {page}...")
            url = self.LIST_URL_TEMPLATE.format(page)
            try:
                response = self.session.get(url, timeout=30)
                if response.status_code == 404: break
                soup = BeautifulSoup(response.text, 'html.parser')
                items = soup.select("div.mainKampanyalarDesktop:not(.eczk) .list a.item")
                if not items: break
                
                new_found = False
                for item in items:
                    href = item.get('href')
                    if href:
                        full_url = urljoin(self.BASE_URL, href)
                        if full_url not in campaign_urls:
                            campaign_urls.append(full_url)
                            new_found = True
                print(f"   -> Found {len(items)} items.")
                if not new_found: break
                page += 1
                time.sleep(1)
            except Exception as e:
                print(f"   ❌ Error fetching page {page}: {e}")
                break
        return campaign_urls

    def _process_campaign(self, url):
        print(f"🔍 Processing (Via AI Parser): {url}")
        try:
            response = self.session.get(url, timeout=30)
            html = response.text
            
            # --- USE CENTRALIZED AI PARSER ---
            # It handles JSON extraction, normalization, and safety checks internally
            ai_data = self.parser.parse_campaign_data(
                raw_text=html,
                bank_name="VakıfBank" # Trigger specific rules
            )
            
            if not ai_data:
                print("   ❌ AI Parsing failed (Returned None). Skipping.")
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
                "Diğer": "Diğer"
            }
            ai_cat = ai_data.get("sector", "Diğer")
            db_sector_name = cat_map.get(ai_cat, "Diğer")
            
            sector = self.db.query(Sector).filter(Sector.name == db_sector_name).first()
            if not sector: sector = self.db.query(Sector).filter(Sector.slug == 'diger').first()
            
            # Generate Unique Slug
            base_slug = re.sub(r'[^a-z0-9]+', '-', title.lower()).strip('-')
            slug = base_slug
            counter = 1
            while self.db.query(Campaign).filter(Campaign.slug == slug).first():
                slug = f"{base_slug}-{counter}"
                counter += 1

            # Prepare Conditions
            conds = ai_data.get("conditions", [])
            part_method = ai_data.get("participation")
            
            # Prepend participation if it exists and isn't generic
            if part_method and "Detayları İnceleyin" not in part_method:
                conds.insert(0, f"KATILIM: {part_method}")
            
            final_conditions = "\n".join(conds)
            
            # Image URL extraction (Still manual as AI Parser doesn't do image extraction yet)
            soup = BeautifulSoup(html, 'html.parser')
            img_el = soup.select_one('.kampanyaDetay .coverSide img')
            image_url = urljoin(self.BASE_URL, img_el['src']) if img_el else None
            
            # Dates
            vf = None
            vu = None
            if ai_data.get("start_date"):
                try: vf = datetime.strptime(ai_data.get("start_date"), "%Y-%m-%d")
                except: pass
            if ai_data.get("end_date"):
                try: vu = datetime.strptime(ai_data.get("end_date"), "%Y-%m-%d")
                except: pass

            # DB Operation
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
                reward_value=ai_data.get("reward_value"),
                conditions=final_conditions,
                eligible_cards=", ".join(ai_data.get("cards", [])),
                image_url=image_url,
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
            # Central parser returns list of strings
            if brands:
                for b_name in brands:
                    if b_name == "Genel": continue
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

            print(f"   ✅ Saved: {title} | Sector: {db_sector_name} | Brands: {brands}")
            return "saved"
            
        except Exception as e:
            print(f"   ❌ Error: {e}")
            self.db.rollback()
            traceback.print_exc()
            return "error"

    def run(self):
        print("🚀 Starting VakıfBank Scraper (Powered by Kartavantaj AI Parser)...")
        urls = self._fetch_campaign_list()
        
        success_count = 0
        skipped_count = 0
        failed_count = 0
        error_details = []
        
        for i, url in enumerate(urls):
            try:
                res = self._process_campaign(url)
                if res == "saved":
                    success_count += 1
                elif res == "skipped":
                    skipped_count += 1
                else:
                    failed_count += 1
                    error_details.append({"url": url, "error": "Save failed"})
            except Exception as e:
                failed_count += 1
                error_details.append({"url": url, "error": str(e)})
                
            time.sleep(2) # Rate limiting
            
        print(f"\n✅ Özet: {len(urls)} bulundu, {success_count} eklendi, {skipped_count} atlandı, {failed_count} hata aldı.")
        
        status = "SUCCESS"
        if failed_count > 0:
             status = "PARTIAL" if (success_count > 0 or skipped_count > 0) else "FAILED"
             
        try:
            from src.utils.logger_utils import log_scraper_execution
            log_scraper_execution(
                 db=self.db,
                 scraper_name="vakifbank",
                 status=status,
                 total_found=len(urls),
                 total_saved=success_count,
                 total_skipped=skipped_count,
                 total_failed=failed_count,
                 error_details={"errors": error_details} if error_details else None
            )
        except Exception as le:
             print(f"⚠️ Could not save scraper log: {le}")
        
        print("🏁 Finished.")

if __name__ == "__main__":
    scraper = VakifbankScraper()
    scraper.run()
