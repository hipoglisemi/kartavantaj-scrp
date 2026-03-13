# pyre-ignore-all-errors
# type: ignore


import requests
import time
from datetime import datetime
from typing import Dict, Any, List, Optional
from bs4 import BeautifulSoup
from urllib.parse import urljoin
from sqlalchemy.orm import Session

from src.database import get_db_session
from src.models import Campaign, Bank, Card, Sector, Brand, CampaignBrand
from src.services.ai_parser import parse_api_campaign
from src.utils.slug_generator import get_unique_slug
from src.utils.cache_manager import clear_cache
from src.services.brand_normalizer import cleanup_brands


class GarantiBonusScraper:
    """Scraper for Garanti Bonus campaigns using HTML parsing + Gemini AI.
    
    Unlike Yapı Kredi which has APIs, Garanti uses Server-Side Rendering.
    All 200+ campaigns are delivered in the initial HTML response.
    """
    
    BASE_URL = 'https://www.bonus.com.tr'
    CAMPAIGN_LIST_URL = 'https://www.bonus.com.tr/kampanyalar'
    
    HEADERS = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
        'Accept-Language': 'tr-TR,tr;q=0.9,en-US;q=0.8,en;q=0.7',
        'Cache-Control': 'no-cache',
    }
    
    def __init__(self):
        self.session = requests.Session()
        self.bank = None
        self.card = None
    
    def _get_or_create_bank(self):
        """Get or create Garanti BBVA bank"""
        with get_db_session() as db:
            bank = db.query(Bank).filter(Bank.slug == "garanti-bbva").first()
            if not bank:
                bank = Bank(
                    name="Garanti BBVA",
                    slug="garanti-bbva",
                    is_active=True
                )
                db.add(bank)
                db.commit()
                db.refresh(bank)
                print(f"✅ Created bank: Garanti BBVA")
            self.bank = bank
            return bank
    
    def _get_or_create_card(self):
        """Get or create Garanti Bonus card"""
        with get_db_session() as db:
            card = db.query(Card).filter(
                Card.bank_id == self.bank.id,
                Card.slug == "garanti-bonus"
            ).first()
            
            if not card:
                card = Card(
                    bank_id=self.bank.id,
                    name="Garanti Bonus",
                    slug="garanti-bonus",
                    is_active=True
                )
                db.add(card)
                db.commit()
                db.refresh(card)
                print(f"✅ Created card: Garanti Bonus")
            
            self.card = card
            return card
    
    def _fetch_campaign_list(self) -> List[str]:
        """Fetch all campaign URLs from the main listing page.
        
        Returns:
            List of campaign URLs
        """
        print(f"📥 Fetching campaign list from {self.CAMPAIGN_LIST_URL}")
        
        try:
            response = self.session.get(
                self.CAMPAIGN_LIST_URL,
                headers=self.HEADERS,
                timeout=20
            )
            response.raise_for_status()
            
            soup = BeautifulSoup(response.content, 'html.parser')
            
            # Find all campaign links (a.direct elements)
            campaign_links = []
            for link in soup.find_all('a', class_='direct', href=True):
                href = link['href']
                # Filter out non-campaign pages
                if '/kampanyalar/' in href and len(href.split('/')) > 2:
                    if not any(x in href for x in ['sektor', 'kategori', 'marka', '#', 'javascript']):
                        full_url = urljoin(self.BASE_URL, href)
                        if full_url not in campaign_links:
                            campaign_links.append(full_url)
            
            print(f"✅ Found {len(campaign_links)} campaigns")
            return campaign_links
            
        except Exception as e:
            print(f"❌ Error fetching campaign list: {e}")
            return []
    
    def _process_campaign(self, url: str) -> str:
        """Process a single campaign page.
        
        Args:
            url: Campaign detail page URL
            
        Returns:
            True if successful, False otherwise
        """
        # Database Pre-check (Skip Logic)
        try:
            with get_db_session() as db:
                existing = db.query(Campaign).filter(Campaign.tracking_url == url).first()
                if existing:
                    print(f"   ⏭️ Skipped (Already exists): {url}")
                    return True  # Treat as success to avoid counting as failed
        except Exception as e:
            print(f"   ⚠️ DB Pre-check error: {e}")

        try:
            # Fetch campaign detail page
            response = self.session.get(url, headers=self.HEADERS, timeout=15)
            response.raise_for_status()
            
            soup = BeautifulSoup(response.content, 'html.parser')
            
            # ✅ DIRECT HTML EXTRACTION (No AI needed)
            # Title
            title_elm = soup.select_one('.campaign-detail-title h1')
            title = title_elm.get_text().strip() if title_elm else "Başlık Bulunamadı"
            
            # Image
            img_elm = soup.select_one('.campaign-detail__image img')
            image_url = None
            if img_elm:
                image_url = img_elm.get('data-src') or img_elm.get('src')
                if image_url:
                    image_url = urljoin(self.BASE_URL, image_url)
            
            # Dates
            date_elm = soup.select_one('.campaign-date')
            start_date = None
            end_date = None
            
            if date_elm:
                date_text = date_elm.get_text().strip()
                if '-' in date_text:
                    parts = date_text.split('-')
                    if len(parts) >= 2:
                        # Try to parse end date first to get month/year context
                        end_part = parts[1].strip()
                        end_date = self._parse_turkish_date(end_part)
                        
                        start_part = parts[0].strip()
                        # specific handling for "1 - 28 Şubat 2026" where start is just a day
                        # Check if start part is just digits
                        if start_part.isdigit() and end_date:
                            try:
                                day = int(start_part)
                                start_date = datetime(end_date.year, end_date.month, day)
                            except:
                                start_date = self._parse_turkish_date(start_part)
                        else:
                            # Full date parse attempt
                           start_date = self._parse_turkish_date(start_part)
                else:
                    # Single date? unlikely but possible
                    pass
            
            # Description
            description = title
            how_win_header = soup.find('h2', string=lambda x: x and 'NASIL KAZANIRIM' in x.upper())
            if how_win_header:
                desc_p = how_win_header.find_next_sibling('p')
                if desc_p:
                    description = desc_p.get_text().strip()
            
            print(f"   📄 {title[:60]}...")
            
            # 🧠 GEMINI AI - Only for complex parsing
            # Extract ALL campaign content for AI
            content_parts = []
            
            # 1. Info Boxes (Left & Right) - CRITICAL: Contains participation, dates, validity
            # We put this FIRST to ensure it's not truncated if content exceeds limit
            # Some campaigns have info on left, some on right. Using common class.
            info_boxes = soup.select('.campaign-detail__info')
            if info_boxes:
                for box in info_boxes:
                    content_parts.append(box.get_text(separator='\n'))
            else:
                # Fallback to specific sidebars if common class not found
                sidebar = soup.select_one('.campaign-detail__info--right')
                if sidebar:
                    content_parts.append(sidebar.get_text(separator='\n'))
                
                sidebar_left = soup.select_one('.campaign-detail__info--left')
                if sidebar_left:
                    content_parts.append(sidebar_left.get_text(separator='\n'))
            
            # 2. Main content area (left side) - "Nasıl Kazanırım?" and "Diğer Bilgiler"
            main_content = soup.select_one('.how-to-win')
            if main_content:
                content_parts.append(main_content.get_text(separator='\n'))
            
            # 3. Alternative: campaign detail content
            detail_content = soup.select_one('.campaign-detail__content')
            if detail_content and not main_content:
                content_parts.append(detail_content.get_text(separator='\n'))
            
            # Combine all content
            content_text = '\n\n'.join(content_parts)
            
            # Provide sector hint from category if available
            scraper_sector = None
            category_elm = soup.select_one('.campaign-category, .category-tag')
            if category_elm:
                scraper_sector = category_elm.get_text().strip()
            
            # AI parses only: reward_text, reward_value, reward_type, brands, sector, conditions, dates
            ai_data = parse_api_campaign(
                title=title,
                short_description=description,
                content_html=content_text,
                bank_name="Garanti BBVA",
                scraper_sector=scraper_sector
            )
            
            # Fallback for dates if HTML parsing failed
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
            
            # Save campaign
            result = self._save_campaign(
                title=title,
                details_text=description,
                image_url=image_url,
                tracking_url=url,
                start_date=start_date,
                end_date=end_date,
                ai_data=ai_data
            )
            
            return result
            
        except Exception as e:
            print(f"   ❌ Error processing campaign: {e}")
            return "error"
    
    def _save_campaign(self, title: str, details_text: str, image_url: Optional[str],
                       tracking_url: str, start_date, end_date, ai_data: Dict[str, Any]):
        """Save campaign to database"""
        with get_db_session() as db:
            # Check if campaign already exists
            existing = db.query(Campaign).filter(Campaign.tracking_url == tracking_url).first()
            if existing:
                print(f"   ⏭️ Skipped (Already exists, preserving manual edits): {title[:50]}...")
                return "skipped"

            # Generate unique slug
            slug = get_unique_slug(title, db, Campaign)
            
            # Get sector
            sector_id = None
            if ai_data.get('sector'):
                sector = db.query(Sector).filter(Sector.slug == ai_data.get('sector', 'diger')).first()
                if sector:
                    sector_id = sector.id
            
            # Prepare conditions text
            conditions_list = ai_data.get('conditions', [])
            conditions_text = '\n'.join(conditions_list)
            
            # Add participation info to conditions if available
            participation = ai_data.get('participation')
            if participation and participation != "Otomatik katılım":
                conditions_text = f"KATILIM: {participation}\n\n" + conditions_text
            
            # Prepare eligible cards
            eligible_cards_str = None
            cards_list = ai_data.get('cards', [])
            if cards_list:
                eligible_cards_str = ', '.join(cards_list)
                # Ensure it fits in DB column if limited (String usually 255 but let's be safe)
            # Ensure eligible_cards fits in DB column if limited (String usually 255 but let's be safe)
            if eligible_cards_str and len(eligible_cards_str) > 255:
                eligible_cards_str = eligible_cards_str[:255]

            # Fallback for start_date if missing but end_date exists
            # Fallback for start_date if missing but end_date exists
            if not start_date and end_date:
                # Set start date to today (scrape date) as requested
                # This handles long-running campaigns correctly (start date = scrape date)
                try:
                    start_today = datetime.utcnow().date()
                    # Only set if today is before or equal to end_date
                    if start_today <= end_date:
                        start_date = start_today
                    else:
                        # If today is after end_date (shouldn't happen for active campaigns), use end_date
                        start_date = end_date
                except Exception:
                    pass

            # Create campaign
            campaign = Campaign(
                card_id=self.card.id,
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
            
            db.add(campaign)
            db.flush()  # Get campaign ID
            
            # Link brands
            brand_names = ai_data.get('brands', [])
            if brand_names:
                for brand_name in brand_names:
                    # Generic safe slug generating for brand
                    import re
                    # Replace Turkish characters
                    replacements = {'ı': 'i', 'ğ': 'g', 'ü': 'u', 'ş': 's', 'ö': 'o', 'ç': 'c', 'İ': 'i', 'Ğ': 'g', 'Ü': 'u', 'Ş': 's', 'Ö': 'o', 'Ç': 'c'}
                    safe_slug = brand_name.lower().strip()
                    for tr_char, en_char in replacements.items():
                        safe_slug = safe_slug.replace(tr_char, en_char)
                    # Remove non-alphanumeric and replace spaces
                    safe_slug = re.sub(r'[^a-z0-9]+', '-', safe_slug).strip('-')

                    try:
                        # Get or create brand
                        brand = db.query(Brand).filter(
                            (Brand.slug == safe_slug) | (Brand.name.ilike(brand_name))
                        ).first()
                        
                        if not brand:
                            brand = Brand(
                                name=brand_name,
                                slug=safe_slug,
                                is_active=True
                            )
                            db.add(brand)
                            db.commit() # Commit to get ID and catch unique constraints early
                        
                        # Link brand to campaign
                        campaign_brand = db.query(CampaignBrand).filter(
                            CampaignBrand.campaign_id == campaign.id,
                            CampaignBrand.brand_id == brand.id
                        ).first()
                        
                        if not campaign_brand:
                            campaign_brand = CampaignBrand(
                                campaign_id=campaign.id,
                                brand_id=brand.id
                            )
                            db.add(campaign_brand)
                            db.commit()
                    except Exception as e:
                        db.rollback()
                        print(f"   ⚠️ Could not link brand {brand_name}: {e}")
            
            db.commit()
            print(f"   ✅ Saved: {campaign.title[:50]}... (Reward: {campaign.reward_text})")
            return "saved"
    
    def _generate_slug(self, title: str) -> str:
        """Generate URL-friendly slug from title"""
        import re
        import unicodedata
        
        # Normalize unicode characters
        title = unicodedata.normalize('NFKD', title)
        # Convert to lowercase
        title = title.lower()
        # Replace Turkish characters
        replacements = {
            'ı': 'i', 'ğ': 'g', 'ü': 'u', 'ş': 's', 'ö': 'o', 'ç': 'c'
        }
        for tr_char, en_char in replacements.items():
            title = title.replace(tr_char, en_char)
        # Remove non-alphanumeric characters
        title = re.sub(r'[^a-z0-9\s-]', '', title)
        # Replace spaces with hyphens
        title = re.sub(r'[\s]+', '-', title)
        # Remove consecutive hyphens
        title = re.sub(r'-+', '-', title)
        # Trim hyphens from ends
        return title.strip('-')[:100]
    
    def _parse_turkish_date(self, date_str: str) -> Optional[datetime]:
        """Parse Turkish date string (e.g., '1 Ocak 2026')"""
        if not date_str:
            return None
        
        months = {
            'ocak': 1, 'şubat': 2, 'mart': 3, 'nisan': 4,
            'mayıs': 5, 'haziran': 6, 'temmuz': 7, 'ağustos': 8,
            'eylül': 9, 'ekim': 10, 'kasım': 11, 'aralık': 12
        }
        
        try:
            import re
            # Extract day, month, year
            parts = date_str.lower().strip().split()
            day = int(re.sub(r'\D', '', parts[0]))
            month_name = next((m for m in months if m in date_str.lower()), None)
            if not month_name:
                return None
            month = months[month_name]
            
            # Find year (4-digit number)
            year = datetime.now().year
            for part in parts:
                if part.isdigit() and len(part) == 4:
                    year = int(part)
                    break
            
            return datetime(year, month, day)
        except:
            return None
    
    def run(self):
        """Main execution flow"""
        print("🚀 Garanti Bonus Scraper - HTML + Gemini AI Edition")
        print("=" * 60)
        
        try:
            from src.utils.logger_utils import log_scraper_execution
            
            # Setup
            self._get_or_create_bank()
            self._get_or_create_card()
            
            # Fetch campaign list
            campaign_urls = self._fetch_campaign_list()
            
            total_found = len(campaign_urls)
            success_count = 0
            skipped_count = 0
            failed_count = 0
            error_details = []
            
            if not campaign_urls:
                print("❌ No campaigns found!")
                
                with get_db_session() as db:
                    log_scraper_execution(
                        db=db,
                        scraper_name="garanti_bonus",
                        status="FAILED",
                        total_found=0,
                        total_saved=0,
                        total_skipped=0,
                        total_failed=0,
                        error_details={"error": "No campaigns found"}
                    )
                return
            
            # Process campaigns
            for i, url in enumerate(campaign_urls, 1):
                print(f"\n[{i}/{len(campaign_urls)}] Processing: {url}")
                
                try:
                    result = self._process_campaign(url)
                    if result == "saved":
                        success_count += 1
                    elif result == "skipped":
                        skipped_count += 1
                    else:
                        failed_count += 1
                        error_details.append({"url": url, "error": "Process campaign returned error"})
                except Exception as e:
                    failed_count += 1
                    error_details.append({"url": url, "error": str(e)})
                    print(f"❌ Failed processing {url}: {e}")
                
                # Rate limiting
                time.sleep(0.8)
            
            print(f"\n{'=' * 60}")
            print(f"✅ Scraping complete!")
            print(f"✅ Özet: {total_found} bulundu, {success_count} eklendi, {skipped_count} atlandı, {failed_count} hata aldı.")
            
            # Determine status
            status = "SUCCESS"
            if failed_count > 0:
                status = "PARTIAL" if (success_count > 0 or skipped_count > 0) else "FAILED"
                
            # Log to DB
            with get_db_session() as db:
                log_scraper_execution(
                    db=db,
                    scraper_name="garanti_bonus",
                    status=status,
                    total_found=total_found,
                    total_saved=success_count,
                    total_skipped=skipped_count,
                    total_failed=failed_count,
                    error_details={"errors": error_details} if error_details else None
                )
            
            # Clear cache
            clear_cache()
            
        except Exception as e:
            print(f"❌ Fatal error: {e}")
            
            with get_db_session() as db:
                from src.utils.logger_utils import log_scraper_execution
                log_scraper_execution(
                    db=db,
                    scraper_name="garanti_bonus",
                    status="FAILED",
                    total_found=0,
                    total_saved=0,
                    total_skipped=0,
                    total_failed=1,
                    error_details={"error": str(e)}
                )
                
            raise


if __name__ == "__main__":
    scraper = GarantiBonusScraper()
    scraper.run()
