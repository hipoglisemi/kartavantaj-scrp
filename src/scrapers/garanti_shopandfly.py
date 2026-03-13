


import sys
import os

# Dynamic path setup
project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

import requests  # type: ignore # pyre-ignore[21]
import time  # type: ignore # pyre-ignore[21]
from datetime import datetime  # type: ignore # pyre-ignore[21]
from typing import Dict, Any, List, Optional  # type: ignore # pyre-ignore[21]
from bs4 import BeautifulSoup  # type: ignore # pyre-ignore[21]
from urllib.parse import urljoin  # type: ignore # pyre-ignore[21]
from sqlalchemy.orm import Session  # type: ignore # pyre-ignore[21]
from src.database import get_db_session  # type: ignore # pyre-ignore[21]
from src.models import Campaign, Bank, Card, Sector, Brand, CampaignBrand  # type: ignore # pyre-ignore[21]
from src.services.ai_parser import parse_api_campaign  # type: ignore # pyre-ignore[21]
from src.utils.slug_generator import get_unique_slug  # type: ignore # pyre-ignore[21]
from src.utils.cache_manager import clear_cache  # type: ignore # pyre-ignore[21]
from sqlalchemy.exc import IntegrityError  # type: ignore # pyre-ignore[21]
import re  # type: ignore # pyre-ignore[21]

class GarantiShopAndFlyScraper:
    """Scraper for Garanti Shop&Fly campaigns (UIkit based)."""
    
    BASE_URL = 'https://www.shopandfly.com.tr'
    CAMPAIGN_LIST_URL = 'https://www.shopandfly.com.tr/kampanyalar'
    
    HEADERS = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
        'Cache-Control': 'no-cache',
    }
    
    def __init__(self):
        self.session = requests.Session()
        self.bank = None
        self.card = None
        
        # Initialize bank and card from DB
        with get_db_session() as db:
            bank = db.query(Bank).filter(Bank.slug == "garanti-bbva").first()  # type: ignore # pyre-ignore[16]
            if not bank:
                bank = Bank(name="Garanti BBVA", slug="garanti-bbva", is_active=True)
                db.add(bank)  # type: ignore # pyre-ignore[16]
                db.commit()  # type: ignore # pyre-ignore[16]
                db.refresh(bank)
                print(f"✅ Created bank: Garanti BBVA")
            self.bank = bank
            
            card = db.query(Card).filter(  # type: ignore # pyre-ignore[16]
                Card.bank_id == self.bank.id,  # type: ignore # pyre-ignore[16]
                Card.slug == "garanti-shop-fly"
            ).first()
            if not card:
                card = Card(bank_id=self.bank.id, name="Garanti Shop&Fly", slug="garanti-shop-fly", is_active=True)  # type: ignore # pyre-ignore[16]
                db.add(card)  # type: ignore # pyre-ignore[16]
                db.commit()  # type: ignore # pyre-ignore[16]
                db.refresh(card)
                print(f"✅ Created card: Garanti Shop&Fly")
            self.card = card
    
    def _get_or_create_bank(self):
        """Get or create Garanti BBVA bank"""
        with get_db_session() as db:
            bank = db.query(Bank).filter(Bank.slug == "garanti-bbva").first()  # type: ignore # pyre-ignore[16]
            if not bank:
                bank = Bank(
                    name="Garanti BBVA",
                    slug="garanti-bbva",
                    is_active=True
                )
                db.add(bank)  # type: ignore # pyre-ignore[16]
                db.commit()  # type: ignore # pyre-ignore[16]
                db.refresh(bank)
                print(f"✅ Created bank: Garanti BBVA")
            self.bank = bank
            return bank  # type: ignore # pyre-ignore[7]
    
    def _get_or_create_card(self):
        """Get or create Garanti Shop&Fly card"""
        with get_db_session() as db:
            card = None
            if self.bank and hasattr(self.bank, 'id'):
                card = db.query(Card).filter(  # type: ignore # pyre-ignore[16]
                    Card.bank_id == self.bank.id,  # type: ignore # pyre-ignore[16]
                    Card.slug == "garanti-shop-fly"
                ).first()
            
            if not card and self.bank:
                card = Card(
                    bank_id=self.bank.id,  # type: ignore # pyre-ignore[16]
                    name="Garanti Shop&Fly",
                    slug="garanti-shop-fly",
                    is_active=True
                )
                db.add(card)  # type: ignore # pyre-ignore[16]
                db.commit()  # type: ignore # pyre-ignore[16]
                db.refresh(card)
                print(f"✅ Created card: Garanti Shop&Fly")
            
            self.card = card
            return card  # type: ignore # pyre-ignore[7]
    
    def _fetch_campaign_list(self) -> List[str]:  # type: ignore # pyre-ignore[16,6]
        """Fetch all campaign URLs from the main listing page."""
        print(f"📥 Fetching campaign list from {self.CAMPAIGN_LIST_URL}")
        
        try:
            response = self.session.get(
                self.CAMPAIGN_LIST_URL,
                headers=self.HEADERS,
                timeout=20
            )
            response.raise_for_status()
            
            soup = BeautifulSoup(response.content, 'html.parser')
            
            campaign_links = []
            # Find all links starting with /kampanyalar/
            for link in soup.find_all('a', href=True):
                href = link['href']
                if href.startswith('/kampanyalar/') and len(href.split('/')) > 2:
                     # Filter out non-campaign lists if any
                    full_url = urljoin(self.BASE_URL, href)
                    if full_url not in campaign_links:
                        campaign_links.append(full_url)
            
            print(f"✅ Found {len(campaign_links)} campaigns")
            return campaign_links  # type: ignore # pyre-ignore[7]
            
        except Exception as e:
            print(f"❌ Error fetching campaign list: {e}")
            return []  # type: ignore # pyre-ignore[7]
    
    def _process_campaign(self, url: str) -> str:
        """Process a single campaign page."""
        # Database Pre-check (Skip Logic)
        try:
            with get_db_session() as db:
                existing = db.query(Campaign).filter(Campaign.tracking_url == url).first()  # type: ignore # pyre-ignore[16]
                if existing:
                    print(f"   ⏭️ Skipped (Already exists): {url}")
                    return "skipped"  # type: ignore # pyre-ignore[7]
        except Exception as e:
            print(f"   ⚠️ DB Pre-check error: {e}")

        try:
            response = self.session.get(url, headers=self.HEADERS, timeout=15)
            response.raise_for_status()
            
            soup = BeautifulSoup(response.content, 'html.parser')
            
            # Title
            title_elm = soup.find('h1')
            title = title_elm.get_text().strip() if title_elm else "Başlık Bulunamadı"
            
            # Image - Try multiple selectors
            img_container = soup.select_one('.campaignDetail img, .campaign-detail img, .uk-width-expand img')
            image_url = None
            if img_container:
                image_url = img_container.get('src') or img_container.get('data-src')
                if image_url and not image_url.startswith('http'):
                    image_url = urljoin(self.BASE_URL, image_url)
            
            # Dates - Specific HTML Extraction (More reliable than AI for this site)
            start_date = None
            end_date = None
            
            # Find the header "Başlangıç - Bitiş Tarihleri" (h2, h3, p, strong, etc.)
            date_header = soup.find(lambda tag: tag.name in ['h2', 'h3', 'h4', 'h5', 'strong', 'b', 'p'] and  # type: ignore # pyre-ignore[16,6]
                                   'Başlangıç - Bitiş Tarihleri' in tag.get_text())
            
            if date_header:
                # The date usually follows immediately after, either as next sibling or in the next block
                # Try next sibling text first
                date_text = ""
                next_elem = date_header.find_next_sibling()
                if next_elem:
                    date_text = next_elem.get_text().strip()
                
                # If not found, look at the parent's text or next element in hierarchy
                if not date_text:
                    parent = date_header.parent
                    if parent:
                        # Extract text from parent, removing the header text
                        full_text = parent.get_text().strip()
                        header_text = date_header.get_text().strip()
                        date_text = full_text.replace(header_text, '').strip()

                # Parse simple date range: "01.02.2026 - 28.02.2026"
                import re  # type: ignore # pyre-ignore[21]
                date_pattern = r'(\d{1,2}\.\d{1,2}\.\d{4})\s*-\s*(\d{1,2}\.\d{1,2}\.\d{4})'
                match = re.search(date_pattern, date_text)
                if match:
                    try:
                        start_date = datetime.strptime(match.group(1), "%d.%m.%Y")
                        end_date = datetime.strptime(match.group(2), "%d.%m.%Y")
                        print(f"   📅 Found Dates: {start_date.date()} - {end_date.date()}")
                    except:
                        pass

            # Content for AI
            main_content = ""
            # Convert soup to text preserving newlines
            main_content = soup.get_text(separator='\n') 
            
            print(f"   📄 {title[:60]}...")  # type: ignore # pyre-ignore[16,6]
            
            # AI Parsing
            ai_data = parse_api_campaign(
                title=title,
                short_description=title, # Let AI generate better one
                content_html=main_content, # Sending full text works well for Gemini
                bank_name="Garanti BBVA",
                scraper_sector=None
            )
            
            # Dates Fallback (Use AI data only if HTML extraction failed)
            if not start_date and ai_data.get('start_date'):
                try:
                    start_date = datetime.strptime(ai_data['start_date'], "%Y-%m-%d")
                except:
                    pass
            
            if not end_date and ai_data.get('end_date'):
                try:
                    end_date = datetime.strptime(ai_data['end_date'], "%Y-%m-%d")
                except:
                    pass

            # Update start_date logic (user request: default to today if missing)
            if not start_date and end_date:
                try:
                    start_today = datetime.utcnow().date()
                    if start_today <= end_date.date():
                        start_date = datetime(start_today.year, start_today.month, start_today.day)
                    else:
                        start_date = end_date
                except:
                    pass
            
            # Save campaign
            result = self._save_campaign(
                title=title,
                details_text=ai_data.get('short_description'),
                image_url=image_url,
                tracking_url=url,
                start_date=start_date,
                end_date=end_date,
                ai_data=ai_data
            )
            
            return result  # type: ignore # pyre-ignore[7]
            
        except Exception as e:
             print(f"   ❌ Error processing campaign: {e}")
             return "error"  # type: ignore # pyre-ignore[7]

    def _save_campaign(self, title: str, details_text: str, image_url: Optional[str],  # type: ignore # pyre-ignore[16,6]
                       tracking_url: str, start_date, end_date, ai_data: Dict[str, Any]):  # type: ignore # pyre-ignore[16,6]
        """Save campaign to database"""
        with get_db_session() as db:
            # Check if campaign already exists
            existing = db.query(Campaign).filter(Campaign.tracking_url == tracking_url).first()  # type: ignore # pyre-ignore[16]
            if isinstance(title, str):
                print(f"   ⏭️ Skipped (Already exists, preserving manual edits): {title[:50]}...")  # type: ignore # pyre-ignore[16,6]
            else:
                print(f"   ⏭️ Skipped (Already exists): {tracking_url}")

            slug = get_unique_slug(title, db, Campaign)
            
            # Sector
            sector_id = None
            if ai_data.get('sector'):
                sector = db.query(Sector).filter(Sector.slug == ai_data.get('sector', 'diger')).first()  # type: ignore # pyre-ignore[16]
                if sector:
                    sector_id = sector.id  # type: ignore # pyre-ignore[16]
            
            # Conditions
            conditions_list = ai_data.get('conditions', [])
            conditions_text = '\n'.join(conditions_list)
            
            participation = ai_data.get('participation')
            if participation and participation != "Otomatik katılım":
                conditions_text = f"KATILIM: {participation}\n\n" + conditions_text
            
            # Eligible Cards
            eligible_cards_str = None
            cards_list = ai_data.get('cards', [])
            if cards_list:
                eligible_cards_str = ', '.join(cards_list)
                if len(eligible_cards_str) > 255:
                    eligible_cards_str = eligible_cards_str[:255]  # type: ignore # pyre-ignore[16,6]
            
            # Create campaign
            card_id = None
            if self.card and hasattr(self.card, 'id'):
                card_id = self.card.id  # type: ignore # pyre-ignore[16]
                
            campaign = Campaign(
                card_id=card_id,
                sector_id=sector_id,
                slug=slug,
                title=ai_data.get('short_title') or ai_data.get('title') or title,
                reward_text=ai_data.get('reward_text'),
                        clean_text=ai_data.get('_clean_text'),
                reward_value=ai_data.get('reward_value'),
                reward_type=ai_data.get('reward_type'),
                description=details_text,
                conditions=conditions_text,
                eligible_cards=eligible_cards_str,
                image_url=image_url,
                start_date=start_date,
                end_date=end_date,
                tracking_url=tracking_url,
                is_active=True,
                created_at=datetime.utcnow(),
                updated_at=datetime.utcnow()
            )
            
            db.add(campaign)  # type: ignore # pyre-ignore[16]
            db.flush()  # type: ignore # pyre-ignore[16]
            
            # Brands
            brand_names = ai_data.get('brands', [])
            if brand_names:
                for brand_name in brand_names:
                    brand = db.query(Brand).filter(Brand.name == brand_name).first()  # type: ignore # pyre-ignore[16]
                    if not brand:
                        try:
                            with db.begin_nested():
                                brand = Brand(name=brand_name, slug=brand_name.lower().replace(' ', '-'), is_active=True)
                                db.add(brand)  # type: ignore # pyre-ignore[16]
                                db.flush()  # type: ignore # pyre-ignore[16]
                        except IntegrityError:
                            brand = db.query(Brand).filter(Brand.name == brand_name).first()  # type: ignore # pyre-ignore[16]
                    
                    if brand:
                        campaign_brand = CampaignBrand(campaign_id=campaign.id, brand_id=brand.id)  # type: ignore # pyre-ignore[16]
                        db.add(campaign_brand)  # type: ignore # pyre-ignore[16]
            
            db.commit()  # type: ignore # pyre-ignore[16]
            print(f"   ✅ Saved: {campaign.title[:50]}... (Reward: {campaign.reward_text})")  # type: ignore # pyre-ignore[16,6]
            return "saved"  # type: ignore # pyre-ignore[7]

    def run(self):
        """Main execution flow"""
        print("🚀 Garanti Shop&Fly Scraper - UIkit Edition")
        print("=" * 60)
        
        try:
            # Fetch campaign list
            campaign_urls = self._fetch_campaign_list()
            
            if not campaign_urls:
                print("❌ No campaigns found!")
                from src.utils.logger_utils import log_scraper_execution  # type: ignore # pyre-ignore[21]
                with get_db_session() as db:
                     log_scraper_execution(db, "garanti-shop-fly", "SUCCESS", 0, 0, 0, 0)
                return
            
            success_count: int = 0
            skipped_count: int = 0
            failed_count: int = 0
            error_details: List[Dict[str, Any]] = []  # type: ignore # pyre-ignore[16,6]
            for i, url in enumerate(campaign_urls, 1):
                print(f"\n[{i}/{len(campaign_urls)}] Processing: {url}")  # type: ignore # pyre-ignore[16,6]
                
                try:
                    result = self._process_campaign(url)
                    if result == "saved":
                        success_count += 1  # type: ignore # pyre-ignore
                    elif result == "skipped":
                        skipped_count += 1  # type: ignore # pyre-ignore
                    else:
                        failed_count += 1  # type: ignore # pyre-ignore
                        error_details.append({"url": url, "error": "Save failed"})
                except Exception as e:
                    failed_count += 1  # type: ignore # pyre-ignore[58]
                    error_details.append({"url": url, "error": str(e)})
                
                # Rate limiting
                time.sleep(0.8)
            
            print(f"\n{'=' * 60}")
            print(f"✅ Scraping complete!")
            print(f"✅ Özet: {len(campaign_urls)} bulundu, {success_count} eklendi, {int(skipped_count or 0) + int(failed_count or 0)} atlandı/hata aldı.")
            
            status = "SUCCESS"
            if failed_count > 0:  # type: ignore # pyre-ignore[58]
                 status = "PARTIAL" if (success_count > 0 or skipped_count > 0) else "FAILED"  # type: ignore # pyre-ignore[58]
                 
            from src.utils.logger_utils import log_scraper_execution  # type: ignore # pyre-ignore[21]
            with get_db_session() as db:
                 log_scraper_execution(
                      db=db,
                      scraper_name="garanti-shop-fly",
                      status=status,
                      total_found=len(campaign_urls),
                      total_saved=success_count,
                      total_skipped=skipped_count,
                      total_failed=failed_count,
                      error_details={"errors": error_details} if error_details else None
                 )
            
            # Clear cache
            clear_cache()
            
        except Exception as e:
            print(f"❌ Fatal error: {e}")
            from src.utils.logger_utils import log_scraper_execution  # type: ignore # pyre-ignore[21]
            with get_db_session() as db:
                 log_scraper_execution(db, "garanti-shop-fly", "FAILED", 0, 0, 0, 1, {"error": str(e)})
            raise

if __name__ == "__main__":
    scraper = GarantiShopAndFlyScraper()
    scraper.run()
