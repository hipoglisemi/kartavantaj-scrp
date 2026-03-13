# pyre-ignore-all-errors
# type: ignore


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

class YapikrediPlayScraper:
    """
    Scraper for Yapı Kredi Play campaigns using the public API.
    Does not require browser automation (Playwright/Selenium).
    """
    
    BASE_URL = 'https://www.yapikrediplay.com.tr'
    LIST_API_URL = 'https://www.yapikrediplay.com.tr/api/campaigns?campaignSectorId=dfe87afe-9b57-4dfd-869b-c87dd00b85a1&campaignSectorKey=tum-kampanyalar'
    BANK_NAME = 'Yapı Kredi'
    CARD_NAME = 'Play' # The specific card program
    
    def __init__(self):
        self.db: Session = get_db_session()
        self.bank = self._get_or_create_bank()
        self.card = self._get_or_create_card()
        
    def _get_or_create_bank(self) -> Bank:
        bank = self.db.query(Bank).filter(Bank.slug == "yapi-kredi").first()
        if not bank:
            print(f"Creating bank: {self.BANK_NAME}")
            bank = Bank(
                name=self.BANK_NAME, 
                slug="yapi-kredi", 
                logo_url="/logos/cards/yapikredi.png",
                is_active=True
            )
            self.db.add(bank)
            self.db.commit()
        else:
            if not bank.logo_url:
                bank.logo_url = "/logos/cards/yapikredi.png"
                self.db.commit()
                print(f"Updated bank logo: {self.BANK_NAME}")
        return bank

    def _get_or_create_card(self) -> Card:
        card = self.db.query(Card).filter(Card.slug == "play", Card.bank_id == self.bank.id).first()
        if not card:
            print(f"Creating card: {self.CARD_NAME}")
            card = Card(
                name=self.CARD_NAME,
                bank_id=self.bank.id,
                slug="play",
                card_type="credit",
                logo_url="/logos/cards/yapikrediplay.png",
                is_active=True
            )
            self.db.add(card)
            self.db.commit()
        else:
             if not card.logo_url:
                card.logo_url = "/logos/cards/yapikrediplay.png"
                self.db.commit()
                print(f"Updated card logo: {self.CARD_NAME}")
        return card

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
        
        existing = self.db.query(Campaign).filter(Campaign.tracking_url == full_url).first()
        if existing:
            print(f"   Skipping existing: {title}")
            return "skipped"

        print(f"   Processing: {title}")
        
        # ========== API-FIRST APPROACH ==========
        # 1. Extract fields DIRECTLY from API response (no HTML fetch needed!)
        api_image_url = item.get('ImageUrl')
        if api_image_url and not api_image_url.startswith('http'):
            api_image_url = f"{self.BASE_URL}{api_image_url}"
        
        short_description = item.get('ShortDescription') or ''
        content_html = item.get('Content') or ''
        start_date_str = item.get('StartDate')  # "2026-02-01T00:00:00"
        end_date_str = item.get('EndDate')      # "2026-02-28T00:00:00"
        
        # 2. Parse dates from API (guaranteed accurate — no AI guessing!)
        start_date = self._parse_iso_date(start_date_str)
        end_date = self._parse_iso_date(end_date_str)
        
        # 3. Call Gemini with ONLY title + description + content (lightweight!)
        ai_result = parse_api_campaign(
            title=title,
            short_description=short_description,
            content_html=content_html,
            bank_name=self.BANK_NAME
        )
        
        # 4. Save: API data + AI enrichment
        # Use AI's short_title for display, keep original API title in details_text
        display_title = ai_result.get('short_title') or title
        
        return self._save_campaign(
            title=display_title,
            details_text=short_description,
            image_url=api_image_url,
            tracking_url=full_url,
            start_date=start_date,
            end_date=end_date,
            ai_data=ai_result
        )

    def _save_campaign(self, title: str, details_text: str, image_url: Optional[str],
                       tracking_url: str, start_date, end_date, ai_data: Dict[str, Any]):
        try:
            # Map sector
            sector_name = ai_data.get('sector', 'Diğer')
            sector = self.db.query(Sector).filter((Sector.slug == sector_name) | (Sector.name.ilike(sector_name))).first()
            if not sector:
                sector = self.db.query(Sector).filter(Sector.slug == 'diger').first()

            # Build conditions: prepend metadata lines with markers
            participation = ai_data.get('participation', '')
            cards_list = ai_data.get('cards', [])
            conditions_lines = ai_data.get('conditions', [])
            
            meta_lines = []
            if participation and participation != 'Detayları İnceleyin':
                meta_lines.append(f"KATILIM: {participation}")
            if cards_list:
                meta_lines.append(f"KARTLAR: {', '.join(cards_list)}")
            
            all_lines = meta_lines + conditions_lines

            # Extract SEO-friendly slug from URL if available
            # e.g. /kampanyalar/uzun-seo-basligi -> uzun-seo-basligi
            seo_slug = None
            if tracking_url:
                try:
                    # tracking_url is full url: https://.../kampanyalar/slug
                    # But we passed `full_url` as tracking_url, which was constructed from url_suffix
                    # Let's extract from tracking_url
                    path = tracking_url.split('yapikrediplay.com.tr')[-1] # /kampanyalar/slug
                    parts = path.strip('/').split('/')
                    if parts:
                        seo_slug = parts[-1]
                except:
                    pass

            # Use seo_slug if valid, otherwise fallback to title
            slug_source = seo_slug if seo_slug and len(seo_slug) > 5 else title
            slug = get_unique_slug(slug_source, self.db, Campaign)

            campaign = Campaign(
                slug=slug,                                                # ← SEO slug
                title=title,                                          # ← AI short_title
                card_id=self.card.id,                                 # ← Code
                sector_id=sector.id if sector else None,              # ← AI
                reward_value=ai_data.get('reward_value'),             # ← AI
                reward_type=ai_data.get('reward_type'),               # ← AI
                reward_text=ai_data.get('reward_text', 'Detayları İnceleyin'),  # ← AI
                clean_text=ai_data.get('_clean_text', ''),
                description=details_text,                            # ← API (ShortDescription)
                conditions="\n".join(all_lines),                 # ← AI (participation + conditions)
                start_date=start_date,                                # ← API
                end_date=end_date,                                    # ← API
                image_url=image_url,                                  # ← API
                tracking_url=tracking_url,                            # ← API
                is_active=True,
                created_at=datetime.utcnow(),
                updated_at=datetime.utcnow()
            )
            
            self.db.add(campaign)
            self.db.commit()
            print(f"   ✅ Saved: {campaign.title}")

            # Process Brands
            if ai_data.get('brands'):
                raw_brands = ai_data.get('brands')
                clean_brand_list = cleanup_brands(raw_brands)
                
                for brand_name in clean_brand_list:
                    # Check if brand exists
                    brand = self.db.query(Brand).filter(Brand.name == brand_name).first()
                    if not brand:
                        # Create new brand
                        # Use campaign's sector or default
                        brand = Brand(
                            name=brand_name, 
                            slug=get_unique_slug(brand_name, self.db, Brand),
                            is_active=True
                        )
                        self.db.add(brand)
                        self.db.commit()
                        print(f"      ✨ Created Brand: {brand.name}")
                    
                    # Link to Campaign
                    # Check if link exists (idempotency)
                    link = self.db.query(CampaignBrand).filter(
                        CampaignBrand.campaign_id == campaign.id,
                        CampaignBrand.brand_id == brand.id
                    ).first()
                    
                    if not link:
                        link = CampaignBrand(campaign_id=campaign.id, brand_id=brand.id)
                        self.db.add(link)
                        self.db.commit()
                        print(f"      🔗 Linked Brand: {brand.name}")
            
            return "saved"
        except Exception as e:
            self.db.rollback()
            print(f"   ❌ Error saving: {e}")
            return "error"

    def _generate_slug(self, title: str) -> str:
        # Basic slugify
        import re
        text = title.lower()
        text = text.replace('ı', 'i').replace('ğ', 'g').replace('ü', 'u').replace('ş', 's').replace('ö', 'o').replace('ç', 'c')
        text = re.sub(r'[^a-z0-9\s-]', '', text)
        text = re.sub(r'[\s-]+', '-', text).strip('-')
        return f"{text}-{int(time.time())}"

    def _parse_iso_date(self, date_str: Optional[str]) -> Optional[datetime]:
        """Parse ISO date from API response (e.g., '2026-02-01T00:00:00')"""
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
                print("   No more items or error.")
                break
                
            print(f"   Found {len(items)} items on page {page}")
            total_found += len(items)
            
            active_count = 0
            for item in items:

                # Filter expired
                end_date_str = item.get('EndDate')
                if end_date_str:
                    try:
                        # 2026-02-28T00:00:00
                        end_date = datetime.fromisoformat(end_date_str)
                        if end_date < datetime.now():
                            continue
                    except:
                        pass
                
                active_count += 1
                try:
                    res = self._process_item(item)
                    if res == "saved":
                        success_count += 1
                    elif res == "skipped":
                        skipped_count += 1
                    else:
                        failed_count += 1
                        error_details.append({"url": item.get('Url', 'unknown'), "error": "Unknown DB failure"})
                except Exception as e:
                    print(f"❌ Error processing item: {e}")
                    failed_count += 1
                    error_details.append({"url": item.get('Url', 'unknown'), "error": str(e)})
            
            if active_count == 0 and len(items) > 0:
                print("   All items on this page are expired. Stopping.")
                break
                
            page += 1
            time.sleep(1)

        print(f"\n✅ Özet: {total_found} bulundu, {success_count} eklendi, {skipped_count} atlandı, {failed_count} hata aldı.")
        
        status = "SUCCESS"
        if failed_count > 0:
             status = "PARTIAL" if (success_count > 0 or skipped_count > 0) else "FAILED"
             
        try:
            from src.utils.logger_utils import log_scraper_execution
            log_scraper_execution(
                 db=self.db,
                 scraper_name="yapikredi-play",
                 status=status,
                 total_found=total_found,
                 total_saved=success_count,
                 total_skipped=skipped_count,
                 total_failed=failed_count,
                 error_details={"errors": error_details} if error_details else None
            )
        except Exception as le:
             print(f"⚠️ Could not save scraper log: {le}")
        
        # Clear cache so new campaigns appear immediately
        print("🧹 Clearing API cache...")
        clear_cache('campaigns:*')
        clear_cache('cards:*')

if __name__ == "__main__":
    scraper = YapikrediPlayScraper()
    scraper.run()
