
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

class EnparaScraper:
    """
    Scraper for Enpara campaigns.
    Uses BeautifulSoup for SSR HTML parsing and Gemini AI for data structuring.
    """
    
    BASE_URL = 'https://www.enpara.com'
    LIST_URL = 'https://www.enpara.com/kampanyalar'
    BANK_NAME = 'Enpara'
    CARD_NAME = 'Enpara' # Updated per user request
    
    HEADERS = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8',
        'Accept-Language': 'tr-TR,tr;q=0.9,en-US;q=0.8,en;q=0.7',
    }
    
    def __init__(self):
        self.session = requests.Session()
        self.db: Session = get_db_session()
        self.bank = self._get_or_create_bank()
        self.card = self._get_or_create_card()
        
    def _get_or_create_bank(self) -> Bank:
        # Search by slug to be immune to name changes from admin panel
        bank_slug = "enpara"
        bank = self.db.query(Bank).filter(Bank.slug == bank_slug).first()
        if not bank:
            print(f"✨ Creating bank: {self.BANK_NAME}")
            bank = Bank(
                name=self.BANK_NAME, 
                slug=bank_slug, 
                is_active=True,
                logo_url="/logos/cards/enpara.png" # Standard path
            )
            self.db.add(bank)
            self.db.commit()
            self.db.refresh(bank)
        return bank

    def _get_or_create_card(self) -> Card:
        # User may change the name to 'Enpara' instead of 'Enpara Kredi Kartı'
        card_slug = "enpara-kredi-karti"
        card = self.db.query(Card).filter(Card.slug == card_slug).first()
        if not card:
            # Fallback to older slug just in case
            card = self.db.query(Card).filter(Card.slug == "enpara").first()

        if not card:
            print(f"💳 Creating card: {self.CARD_NAME}")
            card = Card(
                name=self.CARD_NAME,
                bank_id=self.bank.id,
                slug=card_slug,
                card_type="credit",
                is_active=True
            )
            self.db.add(card)
            self.db.commit()
            self.db.refresh(card)
        return card

    def _fetch_campaign_links(self) -> List[str]:
        """Fetch all campaign URLs from the listing page."""
        print(f"📥 Fetching campaign list from {self.LIST_URL}")
        try:
            response = self.session.get(self.LIST_URL, headers=self.HEADERS, timeout=20)
            response.raise_for_status()
            soup = BeautifulSoup(response.content, 'html.parser')
            
            links = []
            # Based on investigation, cards use class 'enpara-campaigns__campaign-item'
            items = soup.select('.enpara-campaigns__campaign-item')
            
            # Also check for highlight campaign which has a different class sometimes
            highlight = soup.select_one('.enpara-campaigns__highlight-campaign-photo')
            if highlight and highlight.parent and highlight.parent.name == 'a':
                items.append(highlight.parent)
            
            for item in items:
                href = item.get('href')
                if href:
                    full_url = urljoin(self.BASE_URL, href)
                    if full_url not in links:
                        links.append(full_url)
            
            print(f"✅ Found {len(links)} campaigns")
            return links
        except Exception as e:
            print(f"❌ Error fetching listing: {e}")
            return []

    def _parse_tr_date_range(self, text: str) -> (Optional[str], Optional[str]):
        """Parse Turkish date range like '01 Şubat 2026 - 31 Temmuz 2026'"""
        if not text: return None, None
        import re as _re
        months = {
            'Ocak': '01', 'Şubat': '02', 'Mart': '03', 'Nisan': '04',
            'Mayıs': '05', 'Haziran': '06', 'Temmuz': '07', 'Ağustos': '08',
            'Eylül': '09', 'Ekim': '10', 'Kasım': '11', 'Aralık': '12'
        }
        
        # Regex for "Day Month Year"
        date_pattern = r'(\d{1,2})\s+([a-zA-ZğüşıöçĞÜŞİÖÇ]+)\s+(\d{4})'
        matches = _re.findall(date_pattern, text)
        
        if len(matches) >= 2:
            results = []
            for m in matches[:2]:
                day = m[0].zfill(2)
                month_name = m[1].capitalize()
                month = months.get(month_name, '01')
                year = m[2]
                results.append(f"{year}-{month}-{day}")
            return results[0], results[1]
        
        # Fallback for "31 Aralık 2026'ya kadar"
        single_match = _re.search(date_pattern, text)
        if single_match:
            day = single_match.group(1).zfill(2)
            month_name = single_match.group(2).capitalize()
            month = months.get(month_name, '01')
            year = single_match.group(3)
            # Use current date as start if only end date found
            return datetime.now().strftime("%Y-%m-%d"), f"{year}-{month}-{day}"
            
        return None, None

    def _process_campaign(self, url: str):
        """Process a single campaign detail page."""
        try:
            print(f"   Processing: {url}")
            response = self.session.get(url, headers=self.HEADERS, timeout=15)
            response.raise_for_status()
            soup = BeautifulSoup(response.content, 'html.parser')
            
            # Extract basic info
            title_elm = soup.select_one('h1') or soup.select_one('h2')
            title = title_elm.get_text().strip() if title_elm else "Enpara Kampanyası"
            
            # 🖼️ Improved Image Selector
            img_elm = soup.select_one('.enpara-campaign-detail__photo img') or \
                      soup.select_one('.enpara-campaign-detail__image img') or \
                      soup.select_one('img[src*="kampanyalar"]')
            image_url = None
            if img_elm:
                src = img_elm.get('src')
                if src:
                    image_url = urljoin(self.BASE_URL, src)
            
            # 📅 Improved Date Extraction
            date_elm = soup.select_one('.enpara-campaign-detail__box-date-text') or \
                       soup.select_one('.enpara-campaign-detail__box') or \
                       soup.select_one('.enpara-campaign-detail__info-box span')
            date_text = date_elm.get_text().strip() if date_elm else ""
            
            # 📝 Content for AI
            content_parts = []
            # Clean date text for AI (remove "Geçerlilik tarihi" if present)
            clean_date_text = date_text.replace("Geçerlilik tarihi", "").strip()
            if clean_date_text: 
                content_parts.append(f"Geçerlilik Tarihi: {clean_date_text}")
            
            intro = soup.select_one('#content p')
            if intro: content_parts.append(f"Özet: {intro.get_text().strip()}")
            
            # The main details are in .enpara-campaign-detail__text
            detail_text_elm = soup.select_one('.enpara-campaign-detail__text')
            if detail_text_elm:
                # Find "Nelere dikkat etmelisiniz?" specifically
                all_text = detail_text_elm.get_text(separator='\n').strip()
                if "Nelere dikkat etmelisiniz?" in all_text:
                    # Try to separate it for higher visibility to AI
                    parts = all_text.split("Nelere dikkat etmelisiniz?")
                    content_parts.append(f"Genel Detaylar: {parts[0].strip()}")
                    content_parts.append(f"NELERE DİKKAT ETMELİSİNİZ: {parts[1].strip()}")
                else:
                    content_parts.append(f"Kampanya Detayları: {all_text}")
            
            raw_content = "\n\n".join(content_parts)
            
            # AI enrichment
            ai_data = parse_api_campaign(
                title=title,
                short_description=title,
                content_html=raw_content,
                bank_name=self.BANK_NAME
            )
            
            # 🛠️ MANUAL DATE PARSING (Always provide a fallback)
            if not ai_data.get('start_date') or not ai_data.get('end_date'):
                s_date, e_date = self._parse_tr_date_range(date_text or raw_content)
                if s_date and not ai_data.get('start_date'): ai_data['start_date'] = s_date
                if e_date and not ai_data.get('end_date'): ai_data['end_date'] = e_date

            # Save to DB
            return self._save_campaign(
                title=ai_data.get('short_title') or title,
                details_text=title,
                image_url=image_url,
                tracking_url=url,
                ai_data=ai_data
            )
            
        except Exception as e:
            print(f"   ❌ Error processing campaign {url}: {e}")
            return "error"

    def _save_campaign(self, title: str, details_text: str, image_url: Optional[str],
                       tracking_url: str, ai_data: Dict[str, Any]):
        try:
            # Check if exists
            existing = self.db.query(Campaign).filter(Campaign.tracking_url == tracking_url).first()
            if existing:
                print(f"   ⏩ Skipping existing: {title}")
                return "skipped"

            # Map sector
            sector_name = ai_data.get('sector', 'Diğer')
            sector = self.db.query(Sector).filter(Sector.name == sector_name).first()
            if not sector:
                sector = self.db.query(Sector).filter(Sector.slug == 'diger').first()

            # Slug
            slug = get_unique_slug(title, self.db, Campaign)

            # Dates
            start_date = None
            end_date = None
            if ai_data.get('start_date'):
                try: start_date = datetime.strptime(ai_data['start_date'], "%Y-%m-%d")
                except: pass
            if ai_data.get('end_date'):
                try: end_date = datetime.strptime(ai_data['end_date'], "%Y-%m-%d")
                except: pass

            # Prepare conditions text
            conditions_list = ai_data.get('conditions', [])
            conditions_text = '\n'.join(conditions_list)
            
            # Add participation info to conditions if available
            participation = ai_data.get('participation')
            if participation and participation != "Otomatik katılım":
                conditions_text = f"KATILIM: {participation}\n\n" + conditions_text

            campaign = Campaign(
                slug=slug,
                title=title,
                card_id=self.card.id,
                sector_id=sector.id if sector else None,
                reward_value=ai_data.get('reward_value'),
                reward_type=ai_data.get('reward_type'),
                reward_text=ai_data.get('reward_text', 'Detayları İnceleyin'),
                description=ai_data.get('description') or details_text,
                conditions=conditions_text, # Updated
                start_date=start_date,
                end_date=end_date,
                image_url=image_url,
                tracking_url=tracking_url,
                is_active=True,
                created_at=datetime.utcnow(),
                updated_at=datetime.utcnow()
            )
            
            self.db.add(campaign)
            self.db.commit()
            print(f"   ✅ Saved: {campaign.title}")

            # Brands
            brand_names = ai_data.get('brands', [])
            for b_name in brand_names:
                brand = self.db.query(Brand).filter(Brand.name == b_name).first()
                if not brand:
                    brand = Brand(
                        name=b_name,
                        slug=get_unique_slug(b_name, self.db, Brand),
                        is_active=True
                    )
                    self.db.add(brand)
                    self.db.flush()
                
                # Link
                cb = CampaignBrand(campaign_id=campaign.id, brand_id=brand.id)
                self.db.add(cb)
            
            self.db.commit()
            return "saved"

        except Exception as e:
            self.db.rollback()
            print(f"   ❌ Error saving: {e}")
            return "error"

    def run(self, limit: Optional[int] = None):
        print(f"🚀 Starting {self.BANK_NAME} Scraper...")
        links = self._fetch_campaign_links()
        
        if limit:
            links = links[:limit]
            print(f"   Using limit: {limit}")
            
        success_count = 0
        skipped_count = 0
        failed_count = 0
        error_details = []

        for link in links:
            try:
                result = self._process_campaign(link)
                if result == "saved":
                    success_count += 1
                elif result == "skipped" or result is None:
                    skipped_count += 1
                else:
                    failed_count += 1
            except Exception as e:
                failed_count += 1
                error_details.append({"url": link, "error": str(e)})
            time.sleep(1) # Soft rate limiting
            
        print(f"\n✅ Özet: {len(links)} bulundu, {success_count} eklendi, {skipped_count} atlandı, {failed_count} hata aldı.")
        
        status = "SUCCESS"
        if failed_count > 0:
             status = "PARTIAL" if (success_count > 0 or skipped_count > 0) else "FAILED"
             
        from src.utils.logger_utils import log_scraper_execution
        log_scraper_execution(
             db=self.db,
             scraper_name="enpara",
             status=status,
             total_found=len(links),
             total_saved=success_count,
             total_skipped=skipped_count,
             total_failed=failed_count,
             error_details={"errors": error_details} if error_details else None
        )
        
        if success_count > 0:
            print("🧹 Clearing cache...")
            clear_cache('campaigns:*')
            clear_cache('cards:*')

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--limit', type=int, help='Limit number of campaigns')
    args = parser.parse_args()
    
    scraper = EnparaScraper()
    scraper.run(limit=args.limit)
