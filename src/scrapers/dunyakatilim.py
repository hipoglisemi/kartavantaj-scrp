


import os
import sys

# Dynamic path setup
project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

import requests  # type: ignore # pyre-ignore[21]
import time  # type: ignore # pyre-ignore[21]
from typing import List, Dict, Any, Optional  # type: ignore # pyre-ignore[21]
from datetime import datetime  # type: ignore # pyre-ignore[21]
import hashlib  # type: ignore # pyre-ignore[21]
import re  # type: ignore # pyre-ignore[21]

from bs4 import BeautifulSoup  # type: ignore # pyre-ignore[21]
from sqlalchemy.orm import Session  # type: ignore # pyre-ignore[21]

from src.database import get_db_session  # type: ignore # pyre-ignore[21]
from src.models import Bank, Card, Sector, Brand, Campaign, CampaignBrand  # type: ignore # pyre-ignore[21]
from src.services.ai_parser import AIParser  # type: ignore # pyre-ignore[21]
from src.utils.logger_utils import log_scraper_execution  # type: ignore # pyre-ignore[21]

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
        self.db: Any = None
        self.parser = AIParser()
        
        # Cache
        self.bank_cache: Optional[Bank] = None  # type: ignore # pyre-ignore[16,6]
        self.card_cache: Dict[str, Card] = {}  # type: ignore # pyre-ignore[16,6]
        self.sector_cache: Dict[str, Sector] = {}  # type: ignore # pyre-ignore[16,6]
        self.brand_cache: Dict[str, Brand] = {}  # type: ignore # pyre-ignore[16,6]
        
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
                print(f"\n🌍 Processing Source: {source['name']}")  # type: ignore # pyre-ignore[16,6]
                self._process_source(source)
                
            print(f"\n✅ Scraping complete!")
            
        except Exception as e:
            print(f"❌ Fatal error: {e}")
            import traceback  # type: ignore # pyre-ignore[21]
            traceback.print_exc()
        finally:
            if self.db:
                self.db.close()  # type: ignore # pyre-ignore[16]

    def _process_source(self, source: Dict):
        """Process a single HTML Chunk source"""
        try:
            campaigns = self._fetch_campaign_nodes(source)
            print(f"   Found {len(campaigns)} campaigns for {source['name']}")
            
            if len(campaigns) > self.max_campaigns:
                campaigns = campaigns[:self.max_campaigns]  # type: ignore # pyre-ignore[16,6]
            
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
                    failed_count += 1  # type: ignore # pyre-ignore[58]
                    error_details.append({"error": "Missing URL"})
                    continue
                
                try:
                    res = self._scrape_detail(url, title, base_image, source)
                    if res == "saved":
                        success_count += 1  # type: ignore # pyre-ignore[58]
                    elif res == "skipped":
                        skipped_count += 1  # type: ignore # pyre-ignore[58]
                    else:
                        failed_count += 1  # type: ignore # pyre-ignore[58]
                        error_details.append({"url": url, "error": "Unknown DB failure"})
                        
                    time.sleep(1)  # Rate limiting
                except Exception as e:
                    print(f"      ❌ Error: {e}")
                    failed_count += 1  # type: ignore # pyre-ignore[58]
                    error_details.append({"url": url, "error": str(e)})
                    
            print(f"   ✅ Özet: {len(campaigns)} bulundu, {success_count} eklendi, {skipped_count + failed_count} atlandı/hata aldı.")
            
            status = "SUCCESS"
            if failed_count > 0:  # type: ignore # pyre-ignore[58]
                 status = "PARTIAL" if (success_count > 0 or skipped_count > 0) else "FAILED"  # type: ignore # pyre-ignore[58]
                 
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
            import traceback  # type: ignore # pyre-ignore[21]
            traceback.print_exc()

    def _fetch_campaign_nodes(self, source: Dict) -> List[Any]:  # type: ignore # pyre-ignore[16,6]
        """Fetch campaigns from XHR endpoint returning HTML"""
        try:
            response = requests.get(source['api'], headers=self.headers, timeout=30)
            response.raise_for_status()
            soup = BeautifulSoup(response.text, 'html.parser')
            nodes = soup.select('.item.blog-item')
            return nodes  # type: ignore # pyre-ignore[7]
        except Exception as e:
            print(f"      ❌ API Fetch Error: {e}")
            return []  # type: ignore # pyre-ignore[7]

    def _extract_url(self, node: Any, base_url: str) -> Optional[str]:  # type: ignore # pyre-ignore[16,6]
        a_tag = node.select_one('a')
        if a_tag and a_tag.get('href'):
            url = a_tag['href']
            if url.startswith('/'):
                url = base_url + url
            elif not url.startswith('http'):
                url = base_url + '/' + url
            return url  # type: ignore # pyre-ignore[7]
        return None  # type: ignore # pyre-ignore[7]

    def _extract_title(self, node: Any) -> str:
        h3 = node.select_one('h3')
        return h3.get_text(strip=True) if h3 else "Kampanya"  # type: ignore # pyre-ignore[7]
        
    def _extract_image(self, node: Any, base_url: str) -> Optional[str]:  # type: ignore # pyre-ignore[16,6]
        img = node.select_one('img')
        if img:
            src = img.get('src', '')
            if src.startswith('/'):
                return base_url + src  # type: ignore # pyre-ignore[7]
            if not src.startswith('http'):
                return base_url + '/' + src  # type: ignore # pyre-ignore[7]
            return src  # type: ignore # pyre-ignore[7]
        return None  # type: ignore # pyre-ignore[7]

    def _scrape_detail(self, url: str, title: str, base_image: Optional[str], source: Dict) -> str:  # type: ignore # pyre-ignore[16,6]
        """Scrape single campaign detail page"""
        
        # Check if exists
        existing = self.db.query(Campaign).filter(Campaign.tracking_url == url).first()  # type: ignore # pyre-ignore[16]
        if existing:
            print(f"      ⏭️ Skipped (Already exists)")
            return "skipped"  # type: ignore # pyre-ignore[7]

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
                return "skipped"  # type: ignore # pyre-ignore[7]

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
                return "error"  # type: ignore # pyre-ignore[7]

            # Save
            return self._save_campaign(ai_data, url, image_url, source['default_card'])  # type: ignore # pyre-ignore[7]
            
        except Exception as e:
            print(f"      ❌ Page Error: {e}")
            return "error"  # type: ignore # pyre-ignore[7]

    def _save_campaign(self, data: Dict[str, Any], url: str, image_url: str, card_name: str) -> str:  # type: ignore # pyre-ignore[16,6]
        """Save to DB"""
        try:
            primary_card = self._get_or_create_card(card_name)
            sector = self._get_sector(str(data.get("sector") or ""))
            brand_ids = self._get_or_create_brands(data.get("brands", []), sector.id if sector else None)  # type: ignore # pyre-ignore[16]
            
            text = data.get("title", "").lower()
            text = text.replace("ı", "i").replace("ğ", "g").replace("ü", "u").replace("ş", "s").replace("ö", "o").replace("ç", "c")
            slug = re.sub(r'[^a-z0-9-]', '-', text)
            slug = re.sub(r'-+', '-', slug).strip('-')
            url_hash = hashlib.md5(url.encode()).hexdigest()[:8]  # type: ignore # pyre-ignore[16,6]
            slug = f"{slug}-{url_hash}"
            
            # AIParser returns conditions as a list, ensure it's mapped properly.
            conditions_data = data.get("conditions", [])
            conditions_text = "\n".join(f"• {c}" for c in conditions_data) if isinstance(conditions_data, list) else str(conditions_data)
            
            # Map participation (AI) to category (DB)
            participation_text = data.get("participation")
            
            # Use AI parsed cards if available, otherwise fallback to the default card_name
            cards_list = data.get("cards", [])
            eligible_cards_text = ", ".join(cards_list) if isinstance(cards_list, list) and len(cards_list) > 0 else card_name  # type: ignore # pyre-ignore[58]
            
            campaign = Campaign(
                card_id=primary_card.id,  # type: ignore # pyre-ignore[16]
                sector_id=sector.id if sector else None,  # type: ignore # pyre-ignore[16]
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
                clean_text=data.get('_clean_text'),
                tracking_url=url,
                image_url=image_url,
                is_active=True
            )
            
            self.db.add(campaign)  # type: ignore # pyre-ignore[16]
            self.db.commit()  # type: ignore # pyre-ignore[16]
            
            for bid in brand_ids:
                try:
                    cb = CampaignBrand(campaign_id=campaign.id, brand_id=bid)  # type: ignore # pyre-ignore[16]
                    self.db.add(cb)  # type: ignore # pyre-ignore[16]
                except: pass
            self.db.commit()  # type: ignore # pyre-ignore[16]
            return "saved"  # type: ignore # pyre-ignore[7]
            
        except Exception as e:
            print(f"      ❌ Save error: {e}")
            self.db.rollback()  # type: ignore # pyre-ignore[16]
            return "error"  # type: ignore # pyre-ignore[7]

    def _load_cache(self):
        bank = self.db.query(Bank).filter(Bank.slug == "dunya-katilim").first()  # type: ignore # pyre-ignore[16]
        if not bank:
            bank = Bank(name="Dünya Katılım", slug="dunya-katilim", is_active=True)
            self.db.add(bank)  # type: ignore # pyre-ignore[16]
            self.db.commit()  # type: ignore # pyre-ignore[16]
        self.bank_cache = bank
        
        for c in self.db.query(Card).filter(Card.bank_id == bank.id).all():  # type: ignore # pyre-ignore[16]
            self.card_cache[c.name.lower()] = c
        for s in self.db.query(Sector).all():  # type: ignore # pyre-ignore[16]
            self.sector_cache[s.slug] = s
            self.sector_cache[s.name.lower()] = s
        for b in self.db.query(Brand).all():  # type: ignore # pyre-ignore[16]
            self.brand_cache[b.name.lower()] = b

    def _get_or_create_card(self, name: str) -> Card:
        key = name.lower()
        if key in self.card_cache:
            return self.card_cache[key]  # type: ignore # pyre-ignore[7]
        
        text_for_slug = name.lower()
        text_for_slug = text_for_slug.replace("ı", "i").replace("ğ", "g").replace("ü", "u").replace("ş", "s").replace("ö", "o").replace("ç", "c")
        slug_val = re.sub(r'[^a-z0-9-]', '-', text_for_slug)
        slug_val = re.sub(r'-+', '-', slug_val).strip('-')
        
        assert self.bank_cache is not None
        card = self.db.query(Card).filter(Card.bank_id == self.bank_cache.id, Card.slug == slug_val).first()  # type: ignore # pyre-ignore[16]
        if not card:
            card = Card(bank_id=self.bank_cache.id, name=name, slug=slug_val, is_active=True)  # type: ignore # pyre-ignore[16]
            self.db.add(card)  # type: ignore # pyre-ignore[16]
            self.db.flush()  # type: ignore # pyre-ignore[16]
        self.card_cache[key] = card
        return card  # type: ignore # pyre-ignore[7]

    def _get_sector(self, slug: str) -> Optional[Sector]:  # type: ignore # pyre-ignore[16,6]
        if not slug: return None
        return self.sector_cache.get(slug.lower()) or self.sector_cache.get("diğer")  # type: ignore # pyre-ignore[7]

    def _get_or_create_brands(self, names: List[str], sector_id: int) -> List[int]:  # type: ignore # pyre-ignore[16,6]
        ids = []
        for n in names:
            key = n.lower()
            if key in self.brand_cache:
                ids.append(self.brand_cache[key].id)
            else:
                b = Brand(name=n, slug=key.replace(" ", "-"), is_active=True)
                self.db.add(b)  # type: ignore # pyre-ignore[16]
                self.db.commit()  # type: ignore # pyre-ignore[16]
                self.brand_cache[key] = b
                ids.append(b.id)
        return ids  # type: ignore # pyre-ignore[7]

if __name__ == "__main__":
    is_test = os.environ.get("TEST_MODE") == "1"
    scraper = DunyaKatilimScraper(max_campaigns=999)
    scraper.run()
