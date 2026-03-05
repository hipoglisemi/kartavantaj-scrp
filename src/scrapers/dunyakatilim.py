import os
import requests
import time
from typing import List, Dict, Any, Optional
from datetime import datetime
import hashlib
import re

from bs4 import BeautifulSoup
from sqlalchemy.orm import Session

from src.database import get_db_session
from src.models import Bank, Card, Sector, Brand, Campaign, CampaignBrand
from src.services.ai_parser import AIParser
from src.utils.logger_utils import log_scraper_execution

class DunyaKatilimScraper:
    """
    Dünya Katılım Bankası Scraper
    Fetches campaign list via HTML XHR endpoint and details via SSR pages.
    """
    
    SOURCES = [
         {
             "name": "Bireysel Kampanyalar",
             "api": "https://dunyakatilim.com.tr/GetCampaigns?campaignType=1&siteID=1&query=&categoryId=0",
             "base": "https://dunyakatilim.com.tr",
             "default_card": "Dünya Katılım Kartı"
         }
    ]
    
    def __init__(self, max_campaigns: int = 999):
        self.max_campaigns = max_campaigns
        self.db: Optional[Session] = None
        self.parser = AIParser()
        
        # Cache
        self.bank_cache: Optional[Bank] = None
        self.card_cache: Dict[str, Card] = {}
        self.sector_cache: Dict[str, Sector] = {}
        self.brand_cache: Dict[str, Brand] = {}
        
        self.headers = {
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "X-Requested-With": "XMLHttpRequest",
            "Accept": "text/html, */*; q=0.01",
            "Referer": "https://dunyakatilim.com.tr/kampanyalar"
        }

    def run(self):
        """Entry point for synchronous execution"""
        print(f"🚀 Starting Dünya Katılım Scraper... (TEST_MODE: {os.environ.get('TEST_MODE', '0')})")
        
        try:
            self.db = get_db_session()
            self._load_cache()
            
            for source in self.SOURCES:
                print(f"\n🌍 Processing Source: {source['name']}")
                self._process_source(source)
                
            print(f"\n✅ Scraping complete!")
            
        except Exception as e:
            print(f"❌ Fatal error: {e}")
            import traceback
            traceback.print_exc()
        finally:
            if self.db:
                self.db.close()

    def _process_source(self, source: Dict):
        """Process a single HTML Chunk source"""
        try:
            campaigns = self._fetch_campaign_nodes(source)
            print(f"   Found {len(campaigns)} campaigns for {source['name']}")
            
            if len(campaigns) > self.max_campaigns:
                campaigns = campaigns[:self.max_campaigns]
            
            success_count = 0
            skipped_count = 0
            failed_count = 0
            error_details = []
            
            for i, node in enumerate(campaigns, 1):
                url = self._extract_url(node, source['base'])
                title = self._extract_title(node)
                base_image = self._extract_image(node, source['base'])
                
                print(f"   [{i}/{len(campaigns)}] {title} - {url}")
                if not url:
                    failed_count += 1
                    error_details.append({"error": "Missing URL"})
                    continue
                
                try:
                    res = self._scrape_detail(url, title, base_image, source)
                    if res == "saved":
                        success_count += 1
                    elif res == "skipped":
                        skipped_count += 1
                    else:
                        failed_count += 1
                        error_details.append({"url": url, "error": "Unknown DB failure"})
                        
                    time.sleep(1)  # Rate limiting
                except Exception as e:
                    print(f"      ❌ Error: {e}")
                    failed_count += 1
                    error_details.append({"url": url, "error": str(e)})
                    
            print(f"   ✅ Özet: {len(campaigns)} bulundu, {success_count} eklendi, {skipped_count + failed_count} atlandı/hata aldı.")
            
            status = "SUCCESS"
            if failed_count > 0:
                 status = "PARTIAL" if (success_count > 0 or skipped_count > 0) else "FAILED"
                 
            try:
                log_scraper_execution(
                     db=self.db,
                     scraper_name="dunyakatilim",
                     status=status,
                     total_found=len(campaigns),
                     total_saved=success_count,
                     total_skipped=skipped_count,
                     total_failed=failed_count,
                     error_details={"errors": error_details} if error_details else None
                )
            except Exception as le:
                 print(f"⚠️ Could not save scraper log: {le}")
            
        except Exception as e:
            print(f"   ❌ Source Error: {e}")
            try:
                log_scraper_execution(self.db, "dunyakatilim", "FAILED", 0, 0, 0, 1, {"error": str(e)})
            except:
                pass
            import traceback
            traceback.print_exc()

    def _fetch_campaign_nodes(self, source: Dict) -> List[Any]:
        """Fetch campaigns from XHR endpoint returning HTML"""
        try:
            response = requests.get(source['api'], headers=self.headers, timeout=30)
            response.raise_for_status()
            soup = BeautifulSoup(response.text, 'html.parser')
            nodes = soup.select('.item.blog-item')
            return nodes
        except Exception as e:
            print(f"      ❌ API Fetch Error: {e}")
            return []

    def _extract_url(self, node: Any, base_url: str) -> Optional[str]:
        a_tag = node.select_one('a')
        if a_tag and a_tag.get('href'):
            url = a_tag['href']
            if url.startswith('/'):
                url = base_url + url
            elif not url.startswith('http'):
                url = base_url + '/' + url
            return url
        return None

    def _extract_title(self, node: Any) -> str:
        h3 = node.select_one('h3')
        return h3.get_text(strip=True) if h3 else "Kampanya"
        
    def _extract_image(self, node: Any, base_url: str) -> Optional[str]:
        img = node.select_one('img')
        if img:
            src = img.get('src', '')
            if src.startswith('/'):
                return base_url + src
            if not src.startswith('http'):
                return base_url + '/' + src
            return src
        return None

    def _scrape_detail(self, url: str, title: str, base_image: Optional[str], source: Dict) -> str:
        """Scrape single campaign detail page"""
        
        # Check if exists
        existing = self.db.query(Campaign).filter(Campaign.tracking_url == url).first()
        if existing:
            print(f"      ⏭️ Skipped (Already exists)")
            return "skipped"

        try:
            detail_headers = self.headers.copy()
            detail_headers.pop("X-Requested-With", None)
            
            response = requests.get(url, headers=detail_headers, timeout=30)
            response.raise_for_status()
            soup = BeautifulSoup(response.text, 'html.parser')
            
            content_div = (
                soup.select_one('.news-campaign-content') or 
                soup.select_one('.bt') or 
                soup.select_one('.richtext') or 
                soup.find('h2', string=lambda text: text and 'Kampanya Koşulları' in text)
            )
            
            if content_div:
                if content_div.name == 'h2' and content_div.parent:
                    raw_text = content_div.parent.get_text(separator='\n', strip=True) 
                else:
                    raw_text = content_div.get_text(separator='\n', strip=True)
            else:
                raw_text = soup.body.get_text(separator='\n', strip=True) if soup.body else ""
            
            if len(raw_text) < 30:
                print("      ❌ Content too short or cannot find content block.")
                return "skipped"

            # Check for high-res hero image in detail
            hero_img = soup.select_one('.blog-detail-img img')
            image_url = base_image
            if hero_img and hero_img.get('src'):
                src = hero_img['src']
                image_url = source['base'] + src if src.startswith('/') else (source['base'] + '/' + src if not src.startswith('http') else src)
            
            if not image_url:
                image_url = "https://dunyakatilim.com.tr/Assets/images/logo.svg"
            
            # AI Parse
            ai_data = self.parser.parse_campaign_data(
                raw_text=raw_text,
                title=title,
                bank_name="dunyakatilim",
                card_name=source['default_card']
            )
            
            if not ai_data or ai_data.get("_ai_failed"):
                print("      ❌ AI parsing failed (rate limit veya timeout) — kayıt atlandı")
                return "error"

            # Save
            return self._save_campaign(ai_data, url, image_url, source['default_card'])
            
        except Exception as e:
            print(f"      ❌ Page Error: {e}")
            return "error"

    def _save_campaign(self, data: Dict[str, Any], url: str, image_url: str, card_name: str) -> str:
        """Save to DB"""
        try:
            primary_card = self._get_or_create_card(card_name)
            sector = self._get_sector(data.get("sector"))
            brand_ids = self._get_or_create_brands(data.get("brands", []), sector.id if sector else None)
            
            text = data.get("title", "").lower()
            text = text.replace("ı", "i").replace("ğ", "g").replace("ü", "u").replace("ş", "s").replace("ö", "o").replace("ç", "c")
            slug = re.sub(r'[^a-z0-9-]', '-', text)
            slug = re.sub(r'-+', '-', slug).strip('-')
            url_hash = hashlib.md5(url.encode()).hexdigest()[:8]
            slug = f"{slug}-{url_hash}"
            
            # AIParser returns conditions as a list, ensure it's mapped properly.
            conditions_data = data.get("conditions", [])
            conditions_text = "\n".join(f"• {c}" for c in conditions_data) if isinstance(conditions_data, list) else str(conditions_data)
            
            # Map participation (AI) to category (DB)
            participation_text = data.get("participation")
            
            # Use AI parsed cards if available, otherwise fallback to the default card_name
            cards_list = data.get("cards", [])
            eligible_cards_text = ", ".join(cards_list) if isinstance(cards_list, list) and len(cards_list) > 0 else card_name
            
            campaign = Campaign(
                card_id=primary_card.id,
                sector_id=sector.id if sector else None,
                title=data.get("title"),
                slug=slug,
                description=data.get("description"),
                conditions=conditions_text,
                start_date=data.get("start_date"),
                end_date=data.get("end_date"),
                reward_type=data.get("reward_type"),
                reward_value=data.get("reward_value"),
                reward_text=data.get("reward_text"),
                ai_marketing_text=data.get("description"),
                eligible_cards=eligible_cards_text,
                category=participation_text,
                badge_color=data.get("badge_color"),
                card_logo_url="https://dunyakatilim.com.tr/Assets/images/logo.svg",
                tracking_url=url,
                image_url=image_url,
                is_active=True
            )
            
            self.db.add(campaign)
            self.db.commit()
            
            for bid in brand_ids:
                try:
                    cb = CampaignBrand(campaign_id=campaign.id, brand_id=bid)
                    self.db.add(cb)
                except: pass
            self.db.commit()
            return "saved"
            
        except Exception as e:
            print(f"      ❌ Save error: {e}")
            self.db.rollback()
            return "error"

    def _load_cache(self):
        bank = self.db.query(Bank).filter(Bank.slug == "dunyakatilim").first()
        if not bank:
            bank = Bank(name="Dünya Katılım", slug="dunyakatilim", is_active=True)
            self.db.add(bank)
            self.db.commit()
        self.bank_cache = bank
        
        for c in self.db.query(Card).filter(Card.bank_id == bank.id).all():
            self.card_cache[c.name.lower()] = c
        for s in self.db.query(Sector).all():
            self.sector_cache[s.name.lower()] = s
        for b in self.db.query(Brand).all():
            self.brand_cache[b.name.lower()] = b

    def _get_or_create_card(self, name: str) -> Card:
        key = name.lower()
        if key in self.card_cache:
            return self.card_cache[key]
        
        slug_val = name.lower().replace(" ", "-")
        card = self.db.query(Card).filter(Card.bank_id == self.bank_cache.id, Card.slug == slug_val).first()
        if not card:
            card = Card(bank_id=self.bank_cache.id, name=name, slug=slug_val, is_active=True)
            self.db.add(card)
            self.db.flush()
        self.card_cache[key] = card
        return card

    def _get_sector(self, name: str) -> Optional[Sector]:
        if not name: return None
        return self.sector_cache.get(name.lower(), self.sector_cache.get("diğer"))

    def _get_or_create_brands(self, names: List[str], sector_id: int) -> List[int]:
        ids = []
        for n in names:
            key = n.lower()
            if key in self.brand_cache:
                ids.append(self.brand_cache[key].id)
            else:
                b = Brand(name=n, slug=key.replace(" ", "-"), is_active=True)
                self.db.add(b)
                self.db.commit()
                self.brand_cache[key] = b
                ids.append(b.id)
        return ids

if __name__ == "__main__":
    is_test = os.environ.get("TEST_MODE") == "1"
    scraper = DunyaKatilimScraper(max_campaigns=999)
    scraper.run()
