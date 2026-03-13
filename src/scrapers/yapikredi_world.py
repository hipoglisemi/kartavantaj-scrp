import sys
import os
# Path setup
project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

import requests
import time
from datetime import datetime
from typing import Dict, Any, List, Optional
from sqlalchemy.orm import Session

from src.database import get_db_session
from src.models import Campaign, Bank, Card, Sector, Brand, CampaignBrand
from src.services.ai_parser import parse_api_campaign
from src.utils.slug_generator import get_unique_slug
from src.utils.cache_manager import clear_cache
from src.services.brand_normalizer import cleanup_brands

class YapikrediWorldScraper:
    """
    Scraper for Yapı Kredi World campaigns using the public API.
    Does not require browser automation (Playwright/Selenium).
    """
    
    BASE_URL = 'https://www.worldcard.com.tr'
    LIST_API_URL = 'https://www.worldcard.com.tr/api/campaigns?campaignSectorId=6d897e71-1849-43a3-a64f-62840e8c0442&campaignSectorKey=tum-kampanyalar'
    BANK_NAME = 'Yapı Kredi'
    CARD_NAME = 'World' # The specific card program
    
    def __init__(self):
        self.bank = None
        self.card = None
        
        # Initialize bank and card from DB
        with get_db_session() as db:
            bank = db.query(Bank).filter(Bank.slug == "yapi-kredi").first()
            if not bank:
                print(f"Creating bank: {self.BANK_NAME}")
                bank = Bank(name=self.BANK_NAME, slug="yapi-kredi", logo_url="/logos/cards/yapikredi.png", is_active=True)
                db.add(bank)
                db.commit()
                db.refresh(bank)
            self.bank = bank
            
            card = db.query(Card).filter(Card.slug == "world", Card.bank_id == self.bank.id).first()
            if not card:
                print(f"Creating card: {self.CARD_NAME}")
                card = Card(name=self.CARD_NAME, bank_id=self.bank.id, slug="world", card_type="credit", logo_url="/logos/cards/yapikrediworld.png", is_active=True)
                db.add(card)
                db.commit()
                db.refresh(card)
            self.card = card

    def _fetch_list(self, page: int) -> List[Dict[str, Any]]:
        headers = {
            'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Referer': f'{self.BASE_URL}/kampanyalar',
            'Accept': 'application/json, text/plain, */*',
            'page': str(page)
        }
        
        try:
            print(f"   Fetching page {page}...")
            response = requests.get(self.LIST_API_URL, headers=headers, timeout=20)
            response.raise_for_status()
            data = response.json()
            return data.get('Items', [])
        except Exception as e:
            print(f"   Error fetching list page {page}: {e}")
            return []

    def _process_item(self, item: Dict[str, Any]):
        title = item.get('Title') or item.get('PageTitle') or "Başlıksız Kampanya"
        url_suffix = item.get('Url')
        if not url_suffix:
            return "skipped"

        if url_suffix.startswith('http'):
            full_url = url_suffix
        else:
            full_url = f"{self.BASE_URL}{url_suffix}"
        
        with get_db_session() as db:
            existing = db.query(Campaign).filter(Campaign.tracking_url == full_url).first()
            if existing:
                print(f"   Skipping existing: {title}")
                return "skipped"

        print(f"   Processing: {title}")
        
        api_image_url = item.get('ImageUrl')
        if api_image_url and not api_image_url.startswith('http'):
            api_image_url = f"{self.BASE_URL}{api_image_url}"
        
        short_description = item.get('ShortDescription') or ''
        content_html = item.get('Content') or ''
        start_date_str = item.get('StartDate')
        end_date_str = item.get('EndDate')
        
        start_date = self._parse_iso_date(start_date_str)
        end_date = self._parse_iso_date(end_date_str)
        
        scraper_sector = item.get('Category') or item.get('Type') or item.get('SectorName') or None
        
        ai_result = parse_api_campaign(
            title=title,
            short_description=short_description,
            content_html=content_html,
            bank_name=self.BANK_NAME,
            scraper_sector=scraper_sector
        )
        
        display_title = ai_result.get('short_title') or title
        
        return self._save_campaign(
            title=display_title,
            details_text=short_description,
            image_url=api_image_url,
            tracking_url=full_url,
            start_date=start_date,
            end_date=end_date,
            ai_data=ai_result,
            seo_slug=item.get('Url', '').strip('/').split('/')[-1] if item.get('Url') else None
        )

    def _save_campaign(self, title: str, details_text: str, image_url: Optional[str],
                       tracking_url: str, start_date, end_date, ai_data: Dict[str, Any], seo_slug: Optional[str] = None):
        try:
            with get_db_session() as db:
                # Map sector
                sector_name = ai_data.get('sector', 'Diğer')
                sector = db.query(Sector).filter((Sector.slug == sector_name) | (Sector.name.ilike(sector_name))).first()
                if not sector:
                    sector = db.query(Sector).filter(Sector.slug == 'diger').first()
                sector_id = sector.id if sector else None

                # Use seo_slug if valid, otherwise fallback to title
                slug_source = seo_slug if seo_slug and len(seo_slug) > 5 else title
                slug = get_unique_slug(slug_source, db, Campaign)

                campaign = Campaign(
                    slug=slug,
                    title=title,
                    card_id=self.card.id if self.card else None,
                    sector_id=sector_id,
                    reward_value=ai_data.get('reward_value'),
                    reward_type=ai_data.get('reward_type'),
                    reward_text=ai_data.get('reward_text', 'Detayları İnceleyin'),
                    clean_text=ai_data.get('_clean_text', ''),
                    description=details_text,
                    conditions="\n".join(ai_data.get('conditions', [])),
                    start_date=start_date,
                    end_date=end_date,
                    image_url=image_url,
                    tracking_url=tracking_url,
                    is_active=True,
                    created_at=datetime.utcnow(),
                    updated_at=datetime.utcnow()
                )
                
                db.add(campaign)
                db.commit()
                print(f"   ✅ Saved: {campaign.title}")

                # Process Brands
                if ai_data.get('brands'):
                    raw_brands = ai_data.get('brands')
                    clean_brand_list = cleanup_brands(raw_brands)
                    
                    for brand_name in clean_brand_list:
                        brand = db.query(Brand).filter(Brand.name == brand_name).first()
                        if not brand:
                            brand = Brand(name=brand_name, slug=get_unique_slug(brand_name, db, Brand), is_active=True)
                            db.add(brand)
                            db.commit()
                            print(f"      ✨ Created Brand: {brand.name}")
                        
                        link = db.query(CampaignBrand).filter(CampaignBrand.campaign_id == campaign.id, CampaignBrand.brand_id == brand.id).first()
                        if not link:
                            link = CampaignBrand(campaign_id=campaign.id, brand_id=brand.id)
                            db.add(link)
                            db.commit()
                            print(f"      🔗 Linked Brand: {brand.name}")
            
            return "saved"
        except Exception as e:
            print(f"   ❌ Error saving: {e}")
            return "error"

    def _parse_iso_date(self, date_str: Optional[str]) -> Optional[datetime]:
        if not date_str:
            return None
        try:
            return datetime.fromisoformat(date_str)
        except:
            return None

    def run(self):
        print(f"🚀 Starting {self.BANK_NAME} {self.CARD_NAME} Scraper...")
        page = 1
        success_count = 0
        skipped_count = 0
        failed_count = 0
        total_found = 0
        error_details = []
        
        while True:
            items = self._fetch_list(page)
            if not items:
                break
                
            print(f"   Found {len(items)} items on page {page}")
            total_found += len(items)
            
            active_count = 0
            for item in items:
                # Filter expired
                end_date_str = item.get('EndDate')
                if end_date_str:
                    try:
                        end_date = datetime.fromisoformat(end_date_str)
                        if end_date < datetime.now():
                            continue
                    except:
                        pass
                
                active_count += 1
                try:
                    res = self._process_item(item)
                    if res == "saved":
                        success_count += 1 # type: ignore
                    elif res == "skipped":
                        skipped_count += 1 # type: ignore
                    else:
                        failed_count += 1 # type: ignore
                except Exception as e:
                    print(f"❌ Error processing item: {e}")
                    failed_count += 1
                    error_details.append({"url": item.get('Url', 'unknown'), "error": str(e)})
            
            if active_count == 0 and len(items) > 0:
                break
                
            page += 1
            time.sleep(1)

        print(f"\n✅ Özet: {total_found} bulundu, {success_count} eklendi, {skipped_count} atlandı, {failed_count} hata aldı.")
        
        status = "SUCCESS"
        if failed_count > 0:
             status = "PARTIAL" if (success_count > 0 or skipped_count > 0) else "FAILED"
             
        try:
            with get_db_session() as db:
                from src.utils.logger_utils import log_scraper_execution
                log_scraper_execution(
                     db=db,
                     scraper_name=f"yapikredi-{self.CARD_NAME.lower()}",
                     status=status,
                     total_found=total_found,
                     total_saved=success_count,
                     total_skipped=skipped_count,
                     total_failed=failed_count,
                     error_details={"errors": error_details} if error_details else None
                )
        except Exception as le:
             print(f"⚠️ Could not save scraper log: {le}")
        
        print("扫 Clearing API cache...")
        clear_cache('campaigns:*')
        clear_cache('cards:*')

if __name__ == "__main__":
    scraper = YapikrediWorldScraper()
    scraper.run()
