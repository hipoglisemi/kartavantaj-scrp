# pyre-ignore-all-errors
# type: ignore


import requests # type: ignore
from bs4 import BeautifulSoup # type: ignore
from urllib.parse import urljoin
import time
import random
from typing import List, Dict, Optional, Any
from datetime import datetime

from src.models import Campaign, CampaignBrand # type: ignore
from src.database import get_db_session # type: ignore
from src.utils.slug_generator import generate_slug # type: ignore
from src.services.ai_parser import parse_api_campaign # type: ignore
from src.services.brand_normalizer import normalize_brand_name # type: ignore
from sqlalchemy.exc import IntegrityError # type: ignore

class AkbankBaseScraper:
    """
    Base scraper for Akbank brands (Axess, Free, Wings, Ticari).
    Handles:
    - AJAX list fetching
    - HTML detail parsing
    - AI content extraction
    - Database saving
    """
    
    def __init__(self, 
                 card_name: str, 
                 base_url: str, 
                 list_url: str, 
                 referer_url: str,
                 list_params: Optional[Dict[str, Any]] = None):
        self.card_name = card_name
        self.base_url = base_url
        self.list_url = list_url
        self.referer_url = referer_url
        self.list_params = list_params or {'checkBox': '[0]', 'searchWord': '""'}
        
        self.session = requests.Session()
        self.session.headers.update({
             'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
             'Accept': 'application/json, text/plain, */*',
             'Referer': self.referer_url
        })
        
        # Helper to find card_id
        with get_db_session() as db: # type: ignore
            from src.models import Card # type: ignore
            card = db.query(Card).filter(Card.name == self.card_name).first()
            if not card:
                raise ValueError(f"Card '{self.card_name}' not found in DB. Please run seed_sectors.py first.")
            self.card = card
            self.card_id = card.id

    def _fetch_campaign_list(self) -> List[str]:
        """Iterate through AJAX pages to get all campaign URLs"""
        print(f"📥 Fetching campaign list for {self.card_name}...")
        campaign_urls = []
        page = 1
        
        while True:
            params = dict(self.list_params)
            params['page'] = str(page)
            
            try:
                print(f"   Scanning page {page}...")
                response = self.session.get(self.list_url, params=params, timeout=20)
                response.raise_for_status()
                
                if 'kampanyadetay' not in response.text:
                    print(f"   Page {page} empty. Stopping.")
                    break
                
                soup = BeautifulSoup(response.text, 'html.parser')
                links = soup.select('.campaingBox a.dLink')
                
                if not links:
                    print(f"   No links found on page {page}. Stopping.")
                    break
                    
                new_found = False
                for link in links:
                    href = link.get('href')
                    if href:
                        full_url = urljoin(self.base_url, href)
                        if full_url not in campaign_urls:
                            campaign_urls.append(full_url)
                            new_found = True
                            
                if not new_found:
                    print("   No new campaigns found. Stopping.")
                    break
                    
                page = page + 1 # type: ignore
                time.sleep(random.uniform(0.5, 1.0))
                
            except Exception as e:
                print(f"❌ Error fetching page {page}: {e}")
                break
                
        print(f"✅ Found {len(campaign_urls)} campaigns for {self.card_name}")
        return campaign_urls

    def _process_campaign(self, url: str, force: bool = False) -> str:
        """Process a single campaign URL"""
        print(f"🔍 Processing: {url}")
        try:
            # Note: Early DB check moved to run() method to handle sub-class overrides automatically.
            
            response = self.session.get(url, timeout=20)
            response.raise_for_status()
            soup = BeautifulSoup(response.content, 'html.parser')
            
            # --- 1. Raw HTML Extraction ---
            title_elm = soup.select_one('h2.pageTitle')
            title = title_elm.get_text(strip=True) if title_elm else "Kampanya"
            
            img_elm = soup.select_one('.campaingDetailImage img')
            image_url = None
            if img_elm:
                src = img_elm.get('src')
                if src:
                    image_url = urljoin(self.base_url, src)
            
            detail_container = soup.select_one('.cmsContent.clearfix')
            details_text = ""
            if detail_container:
                # Remove scripts and styles
                for script in detail_container(["script", "style"]):
                    script.decompose()
                details_text = detail_container.get_text(separator="\n", strip=True)
            else:
                details_text = title
                
            # --- 2. AI Parsing (Using Global Cache) ---
            ai_data = parse_api_campaign(
                title=title,
                short_description=title, 
                content_html=details_text,
                bank_name="Akbank",
                scraper_sector=None,
                tracking_url=url,
                force=force
            )
            
            # --- 3. Save to DB ---
            return self._save_campaign(title, details_text, image_url, ai_data, url)
            
        except Exception as e:
            print(f"❌ Failed to process {url}: {e}")
            return "error"

    def _save_campaign(self, title, details_text, image_url, ai_data, source_url) -> str:
        with get_db_session() as db: # type: ignore
            from src.models import Sector # type: ignore
            from src.utils.slug_generator import get_unique_slug # type: ignore
            
            # Use specific title from AI if available, otherwise fallback
            final_title = ai_data.get('short_title') or ai_data.get('title') or title
            
            # Check for existing campaign by source_url + card_id first
            existing_url = db.query(Campaign).filter(
                Campaign.tracking_url == source_url,
                Campaign.card_id == self.card_id
            ).first()
            
            if existing_url:
                # This should usually be handled by _process_campaign's early check, 
                # but we keep it here as a safety measure.
                print(f"   ⏭️  Skipped (Safety Check: URL exists): {source_url}")
                return "skipped"

            # Ensure slug is unique using the utility
            slug = get_unique_slug(final_title, db, Campaign)
            
            if not slug or slug == "kampanya":
                # Ultimate fallback if title is too generic
                import uuid
                u_str = str(uuid.uuid4())
                slug = f"kampanya-{u_str[:8]}" # type: ignore

            # Map sector from AI data
            sector_name = ai_data.get('sector', 'Diğer')
            sector = db.query(Sector).filter((Sector.slug == sector_name) | (Sector.name.ilike(sector_name))).first()
            if not sector:
                sector = db.query(Sector).filter(Sector.slug == 'diger').first()

            # Dates
            start_date = None
            if ai_data.get('start_date'):
               try:
                   start_date = datetime.strptime(ai_data['start_date'], '%Y-%m-%d')
               except: pass
               
            if not start_date:
                start_date = datetime.now() # Fallback for active campaigns

            end_date = None
            if ai_data.get('end_date'):
                try:
                    end_date = datetime.strptime(ai_data['end_date'], '%Y-%m-%d')
                except: pass

            # Build conditions text with participation and eligible cards
            conditions_lines = []
            
            # Add participation info
            participation = ai_data.get('participation')
            if participation and participation != "Detayları İnceleyin":
                conditions_lines.append(f"KATILIM: {participation}")
            
            # --- USER REQUEST: DO NOT REPEAT ELIGIBLE CARDS IN CONDITIONS ---
            eligible_cards_list = ai_data.get('cards', [])
            # (Previously added GEÇERLİ KARTLAR here, now removed)
            
            # Add AI conditions
            if ai_data.get('conditions'):
                conditions_lines.extend(ai_data.get('conditions'))
            
            conditions_text = "\n".join(conditions_lines)
            eligible_cards_str = ", ".join(eligible_cards_list) if eligible_cards_list else None

            campaign = Campaign(
                card_id=self.card_id,
                sector_id=sector.id if sector else None,
                slug=slug,
                title=ai_data.get('short_title') or ai_data.get('title') or title,
                description=ai_data.get('description') or title,
                reward_text=ai_data.get('reward_text'),
                reward_value=ai_data.get('reward_value'),
                reward_type=ai_data.get('reward_type'),
                conditions=conditions_text,
                eligible_cards=eligible_cards_str,
                image_url=image_url,
                start_date=start_date,
                end_date=end_date,
                clean_text=ai_data.get('_clean_text'),
                is_active=True,
                created_at=datetime.utcnow(),
                updated_at=datetime.utcnow(),
                tracking_url=source_url
            )
            
            db.add(campaign)
            db.commit()
            
            # --- Brands ---
            # Using normalize_brand_name utility
            if ai_data.get('brands'):
                from src.models import Brand # type: ignore
                for brand_name in ai_data['brands']:
                    b_slug = generate_slug(brand_name)
                    try:
                        brand = db.query(Brand).filter(
                            (Brand.slug == b_slug) | (Brand.name.ilike(brand_name))
                        ).first()
                        
                        if not brand:
                            brand = Brand(name=brand_name, slug=b_slug, is_active=True)
                            db.add(brand)
                            db.commit()
                            
                        # Link brand to campaign
                        cb = db.query(CampaignBrand).filter(
                             CampaignBrand.campaign_id == campaign.id,
                             CampaignBrand.brand_id == brand.id
                        ).first()
                        
                        if not cb:
                            cb = CampaignBrand(campaign_id=campaign.id, brand_id=brand.id)
                            db.add(cb)
                            db.commit()
                    except Exception as e:
                        db.rollback()
                        print(f"   ⚠️ Could not link brand {brand_name}: {e}")

            print(f"   ✅ Saved: {campaign.title}")
            return "saved"

    def run(self, limit: Optional[int] = None, urls: Optional[List[str]] = None, force: bool = False):
        print(f"🚀 Starting {self.card_name} Scraper...")
        from src.utils.logger_utils import log_scraper_execution # type: ignore
        
        process_urls: List[str] = []
        if urls:
            process_urls = urls
        else:
            process_urls = self._fetch_campaign_list()
            if limit and isinstance(process_urls, list):
                process_urls = process_urls[:limit] # type: ignore
        
        total_found = len(process_urls)
        total_saved = 0
        total_skipped = 0
        total_failed = 0
        error_details = []

        for i, url in enumerate(process_urls):
            print(f"[{i+1}/{len(process_urls)}]", end=" ")
            try:
                # --- Early DB Check (Moved here to handle sub-class overrides) ---
                if not force:
                    from src.models import Campaign # type: ignore
                    with get_db_session() as db: # type: ignore
                        existing = db.query(Campaign).filter(
                            Campaign.tracking_url == url,
                            Campaign.card_id == self.card_id
                        ).first()
                        if existing:
                            print(f"⏭️  Skipped (Already exists): {url}")
                            total_skipped += 1
                            continue

                # Process (Sub-classes may override this)
                res = self._process_campaign(url, force=force)
                
                # Sub-classes might return None but be successful if they didn't throw
                if res == "saved" or res is None:
                    total_saved += 1
                elif res == "skipped":
                    total_skipped += 1
                else:
                    total_failed += 1
            except Exception as e:
                print(f"❌ Error in loop: {e}")
                total_failed += 1
                error_details.append({"url": url, "error": str(e)})
            time.sleep(1) # Polite delay
            
        print(f"🏁 Scraping finished. Found: {total_found}, Saved: {total_saved}, Skipped: {total_skipped}, Failed: {total_failed}")
        
        # Determine status
        status = "SUCCESS"
        if total_failed > 0:
            status = "PARTIAL" if (total_saved > 0 or total_skipped > 0) else "FAILED"
            
        # Log to database
        with get_db_session() as db:
            scraper_id = f"akbank_{self.card_name.lower()}"
            log_scraper_execution(
                db=db,
                scraper_name=scraper_id,
                status=status,
                total_found=total_found,
                total_saved=total_saved,
                total_skipped=total_skipped,
                total_failed=total_failed,
                error_details={"errors": error_details} if error_details else None
            )
