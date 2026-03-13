import sys
import os
# Path setup
project_root = "/Users/hipoglisemi/Desktop/kartavantaj-scraper"
if project_root not in sys.path:
    sys.path.insert(0, project_root)

import re
import time
import requests
from typing import Optional, List, Dict, Any
from bs4 import BeautifulSoup
from datetime import datetime

from src.database import get_db_session
from src.models import Bank, Card, Sector, Brand, Campaign, CampaignBrand
from src.services.ai_parser import parse_api_campaign
from src.utils.logger_utils import log_scraper_execution
from src.services.brand_normalizer import cleanup_brands
from src.utils.slug_generator import get_unique_slug
from src.utils.cache_manager import clear_cache

class QNBScraper:
    """
    Scraper for QNB Finansbank campaigns using their public API.
    """
    
    BASE_URL = "https://www.qnbcard.com.tr"
    API_URL = "https://www.qnbcard.com.tr/api/Campaigns"
    BANK_NAME = "QNB"
    
    def __init__(self):
        self.bank_id = None
        self.card_id = None
        
        with get_db_session() as db:
            bank = db.query(Bank).filter(Bank.slug == "qnb").first()
            if not bank:
                print(f"   🏦 Creating Bank: {self.BANK_NAME}")
                bank = Bank(name=self.BANK_NAME, slug="qnb", logo_url="https://www.qnbcard.com.tr/Content/images/logo.png", is_active=True)
                db.add(bank)
                db.commit()
                db.refresh(bank)
            self.bank_id = bank.id
            
            card = db.query(Card).filter(Card.bank_id == bank.id, Card.slug == "qnbcard").first()
            if not card:
                print(f"   💳 Creating Card: QNBCard")
                card = Card(bank_id=bank.id, name="QNBCard", slug="qnbcard", card_type="credit", is_active=True)
                db.add(card)
                db.commit()
                db.refresh(card)
            self.card_id = card.id

    def _fetch_campaigns(self, limit=1000) -> List[Dict[str, Any]]:
        print(f"   🌐 Fetching campaigns from QNB API...")
        all_items = []
        page_index = 1
        take = 12
        
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
            "Accept": "application/json, text/plain, */*",
            "x-bone-language": "TR",
            "x-requested-with": "XMLHttpRequest"
        }
        
        try:
            while True:
                headers["page"] = str(page_index)
                params = {"isArchived": "false", "take": str(take)}
                
                response = requests.get(self.API_URL, params=params, headers=headers, timeout=20)
                response.raise_for_status()
                data = response.json()

                items = data.get("Items", [])
                if not items:
                    break
                    
                all_items.extend(items)
                total = data.get("TotalItems", 0)
                
                if len(all_items) >= total or (limit and len(all_items) >= limit):
                    break
                    
                page_index += 1
                time.sleep(0.5)
                
            return all_items[:limit] if limit else all_items
        except Exception as e:
            print(f"   ❌ API fetch failed: {e}")
            return all_items

    def _process_item(self, item: Dict[str, Any]) -> str:
        title = item.get("Title", "").strip()
        if not title:
            return "skipped"

        seo_name = (item.get("SeoProperty") or {}).get("Name")
        campaign_url = f"{self.BASE_URL}/kampanyalar/{seo_name}" if seo_name else f"{self.BASE_URL}/kampanyalar/{item.get('Id')}"

        with get_db_session() as db:
            existing = db.query(Campaign).filter(Campaign.tracking_url == campaign_url).first()
            if existing:
                return "skipped"

        content_html = item.get("Content") or item.get("Description") or ""
        
        ai_data = parse_api_campaign(
            title=title,
            short_description=title,
            content_html=content_html,
            bank_name=self.BANK_NAME
        )
        
        if ai_data.get("_ai_failed"):
            return "error"

        return self._save_campaign(ai_data, campaign_url, item)

    def _save_campaign(self, ai_data: Dict[str, Any], url: str, item: Dict[str, Any]) -> str:
        try:
            with get_db_session() as db:
                sector_name = ai_data.get('sector', 'Diğer')
                sector = db.query(Sector).filter((Sector.slug == sector_name) | (Sector.name.ilike(sector_name))).first()
                if not sector:
                    sector = db.query(Sector).filter(Sector.slug == 'diger').first()
                sector_id = sector.id if sector else None

                image_url = None
                if item.get("Id") and item.get("HasImage"):
                    image_url = f"{self.BASE_URL}/medium/Campaign-DetailImage-{item.get('Id')}.vsf"

                slug = get_unique_slug(ai_data.get('short_title') or ai_data.get('title'), db, Campaign)

                campaign = Campaign(
                    card_id=self.card_id,
                    sector_id=sector_id,
                    title=ai_data.get("short_title") or ai_data.get("title"),
                    slug=slug,
                    description=ai_data.get("description"),
                    conditions="\n".join(ai_data.get("conditions", [])),
                    reward_text=ai_data.get("reward_text", "Fırsatı Kaçırmayın"),
                    reward_value=ai_data.get("reward_value"),
                    reward_type=ai_data.get("reward_type"),
                    start_date=ai_data.get("start_date"),
                    end_date=ai_data.get("end_date"),
                    image_url=image_url or "https://www.qnbcard.com.tr/Content/images/logo.png",
                    tracking_url=url,
                    is_active=True,
                    ai_marketing_text=ai_data.get("marketing_text"),
                    clean_text=ai_data.get("_clean_text")
                )
                
                db.add(campaign)
                db.commit()

                if ai_data.get('brands'):
                    clean_brands = cleanup_brands(ai_data.get('brands'))
                    for b_name in clean_brands:
                        brand = db.query(Brand).filter(Brand.name == b_name).first()
                        if not brand:
                            brand = Brand(name=b_name, slug=get_unique_slug(b_name, db, Brand), is_active=True)
                            db.add(brand)
                            db.commit()
                        
                        link = db.query(CampaignBrand).filter_by(campaign_id=campaign.id, brand_id=brand.id).first()
                        if not link:
                            db.add(CampaignBrand(campaign_id=campaign.id, brand_id=brand.id))
                            db.commit()

            return "saved"
        except Exception as e:
            print(f"      ❌ DB Save Error: {e}")
            return "error"

    def run(self, limit: int = 20):
        print(f"🚀 Starting QNB Scraper...")
        items = self._fetch_campaigns(limit=limit)
        
        success = 0
        skipped = 0
        failed = 0
        error_details = []

        for item in items:
            try:
                res = self._process_item(item)
                if res == "saved":
                    success += 1
                elif res == "skipped":
                    skipped += 1
                else:
                    failed += 1
            except Exception as e:
                failed += 1
                error_details.append({"url": str(item.get("Id")), "error": str(e)})

        status = "SUCCESS" if failed == 0 else ("PARTIAL" if success > 0 else "FAILED")
        with get_db_session() as db:
            log_scraper_execution(
                db=db,
                scraper_name="qnb",
                status=status,
                total_found=len(items),
                total_saved=success,
                total_skipped=skipped,
                total_failed=failed,
                error_details={"errors": error_details} if error_details else None
            )
        
        clear_cache('campaigns:*')

if __name__ == "__main__":
    limit = 5 if os.environ.get('TEST_MODE') == '1' else 20
    scraper = QNBScraper()
    scraper.run(limit=limit)
