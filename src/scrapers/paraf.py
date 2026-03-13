# pyre-ignore-all-errors
# type: ignore


import requests
import time
from typing import List, Dict, Any, Optional
from datetime import datetime
from decimal import Decimal
from urllib.parse import urljoin
import hashlib
import re

from bs4 import BeautifulSoup
from sqlalchemy.orm import Session

from src.database import get_db_session
from src.models import Bank, Card, Sector, Brand, Campaign, CampaignBrand
from src.services.ai_parser import AIParser
from src.utils.logger_utils import log_scraper_execution

class ParafScraper:
    """
    Halkbank (Paraf & Parafly) Scraper
    Uses JSON API endpoints for fast, reliable data extraction.
    """
    
    SOURCES = [
        {
            "name": "Paraf",
            "api": "https://www.paraf.com.tr/content/parafcard/tr/kampanyalar/_jcr_content/root/responsivegrid/filter.filtercampaigns.all.json",
            "base": "https://www.paraf.com.tr",
            "default_card": "Paraf"
        },
        {
            "name": "Parafly",
            "api": "https://www.parafly.com.tr/content/parafly/tr/kampanyalar/_jcr_content/root/responsivegrid/filter.filtercampaigns.all.json",
            "base": "https://www.parafly.com.tr",
            "default_card": "Parafly"
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

    def run(self, limit: Optional[int] = None, urls: Optional[List[str]] = None, force: bool = False):
        """Entry point for synchronous execution"""
        print(f"🚀 Starting Halkbank (Paraf/Parafly) API Scraper...")
        
        try:
            self.db = get_db_session()
            self._load_cache()
            
            for source in self.SOURCES:
                print(f"\n🌍 Processing Source: {source['name']}")
                self._process_source(source, limit=limit, urls=urls, force=force)
                
            print(f"\n✅ Scraping complete!")
            
        except Exception as e:
            print(f"❌ Fatal error: {e}")
            import traceback
            traceback.print_exc()
        finally:
            if self.db:
                self.db.close()

    def _process_source(self, source: Dict, limit: Optional[int] = None, urls: Optional[List[str]] = None, force: bool = False):
        """Process a single API source (Paraf or Parafly)"""
        try:
            # 1. Fetch campaigns from API
            campaigns = self._fetch_campaigns(source)
            print(f"   Found {len(campaigns)} campaigns for {source['name']}")
            
            # Filter if specific URLs provided
            if urls:
                filtered = []
                for c in campaigns:
                    c_url = urljoin(source['base'], c.get('url', ''))
                    if c_url in urls:
                        filtered.append(c)
                campaigns = filtered
            elif limit:
                campaigns = campaigns[:limit]
            
            # 2. Process each campaign
            success_count = 0
            skipped_count = 0
            failed_count = 0
            for i, campaign_data in enumerate(campaigns, 1):
                url = urljoin(source['base'], campaign_data.get('url', ''))
                print(f"   [{i}/{len(campaigns)}] {url}")
                
                try:
                    res = self._scrape_detail(campaign_data, url, source, force=force)
                    if res == "saved":
                        success_count += 1
                    elif res == "skipped":
                        skipped_count += 1
                    else:
                        failed_count += 1
                    time.sleep(1)  # Rate limiting
                except Exception as e:
                    print(f"      ❌ Error: {e}")
                    failed_count += 1
                    
            print(f"   ✅ Özet: {len(campaigns)} bulundu, {success_count} eklendi, {skipped_count} atlandı, {failed_count} hata aldı.")
            
            # Log execution to Database
            log_scraper_execution(
                db=self.db,
                scraper_name=source['name'].lower(),
                status="SUCCESS" if failed_count == 0 else ("PARTIAL" if success_count > 0 else "FAILED"),
                total_found=len(campaigns),
                total_saved=success_count,
                total_skipped=skipped_count,
                total_failed=failed_count
            )
            
        except Exception as e:
            print(f"   ❌ Source Error: {e}")
            import traceback
            error_details = {"traceback": traceback.format_exc(), "error": str(e)}
            log_scraper_execution(
                db=self.db,
                scraper_name=source['name'].lower(),
                status="FAILED",
                error_details=error_details
            )
            traceback.print_exc()

    def _fetch_campaigns(self, source: Dict) -> List[Dict]:
        """Fetch campaigns from JSON API"""
        try:
            response = requests.get(source['api'], timeout=30)
            response.raise_for_status()
            data = response.json()
            
            # API returns array of campaign objects
            if isinstance(data, list):
                return data
            elif isinstance(data, dict) and 'campaigns' in data:
                return data['campaigns']
            else:
                print(f"      ⚠️ Unexpected API response format")
                return []
                
        except Exception as e:
            print(f"      ❌ API Fetch Error: {e}")
            return []

    def _scrape_detail(self, campaign_data: Dict, url: str, source: Dict, force: bool = False) -> str:
        """Scrape single campaign detail page"""
        
        # Check if exists
        if not force:
            existing = self.db.query(Campaign).filter(Campaign.tracking_url == url).first()
            if existing:
                print(f"      ⏭️ Skipped (Already exists)")
                return "skipped"

        try:
            # Fetch detail page for full conditions text
            response = requests.get(url, timeout=30)
            response.raise_for_status()
            soup = BeautifulSoup(response.text, 'html.parser')
            
            # Extract title
            title_el = soup.select_one('h1')
            title = title_el.get_text(strip=True) if title_el else campaign_data.get('title', 'Kampanya')
            
            # Extract full conditions text
            content_div = soup.select_one('.text--use-ulol .cmp-text')
            if not content_div:
                content_div = soup.select_one('.text-area')
            
            raw_text = content_div.get_text(separator='\n', strip=True) if content_div else ""
            
            # Validation
            if len(raw_text) < 50:
                print("      ❌ Content too short")
                return "skipped"

            # Fix image URL
            image_url = self._fix_image_url(
                campaign_data.get('teaserImage') or campaign_data.get('logoImage'),
                source['base']
            )
            
            # AI Parse with enhanced extraction
            ai_data = self.parser.parse_campaign_data(
                raw_text=raw_text,
                title=title,
                bank_name="halkbank",
                card_name=source['default_card'],
                tracking_url=url,
                force=force
            )
            
            if not ai_data:
                print("      ❌ AI parsing failed")
                return "error"
                
            # Save
            self._save_campaign(ai_data, url, image_url, source['default_card'])
            print(f"      ✅ Saved: {ai_data['title']}")
            return "saved"
            
        except Exception as e:
            print(f"      ❌ Page Error: {e}")
            return "error"

    def _fix_image_url(self, image_path: str, base_url: str) -> str:
        """Convert relative image paths to absolute URLs"""
        if not image_path:
            return "https://www.paraf.com.tr/content/dam/parafcard/paraf-logos/paraf-logo-yeni.png"
        
        # Already absolute
        if image_path.startswith('http'):
            return image_path
        
        # Relative path
        if image_path.startswith('/'):
            return f"{base_url}{image_path}"
        
        return image_path

    def _save_campaign(self, data: Dict[str, Any], url: str, image_url: str, card_name: str):
        """Save to DB"""
        
        # Card
        target_cards = data.get("cards", [])
        if not target_cards:
            target_cards = [card_name]
        
        # Bütün Parafly kampanyalarının Paraf'a kaymasını engellemek için AI'ın bulduğu ilk kart yerine, 
        # scraper'ın çalıştığı asıl kaynağın adını (Paraf veya Parafly) baz alıyoruz.
        primary_card = self._get_or_create_card(card_name)
        
        # Sector
        sector = self._get_sector(data.get("sector"))
        
        # Brands
        brand_ids = self._get_or_create_brands(data.get("brands", []), sector.id if sector else None)
        
        # Slug Generation
        slug = data.get("slug")
        if not slug:
            # Turkish char replacement
            text = data.get("title", "").lower()
            text = text.replace("ı", "i").replace("ğ", "g").replace("ü", "u").replace("ş", "s").replace("ö", "o").replace("ç", "c")
            slug = re.sub(r'[^a-z0-9-]', '-', text)
            slug = re.sub(r'-+', '-', slug).strip('-')
            # Add URL hash for uniqueness
            url_hash = hashlib.md5(url.encode()).hexdigest()[:8]
            slug = f"{slug}-{url_hash}"
        
        # Prepare marketing text with participation method
        participation = data.get("participation", "")
        marketing_text = data.get("description", "")
        if participation:
            marketing_text = f"{marketing_text}\n\n📱 Katılım: {participation}"
        
        # Map card logo URL based on eligible cards
        card_logo_url = None
        if target_cards:
            first_card = target_cards[0].lower()
            if "paraf troy" in first_card:
                card_logo_url = "https://www.paraf.com.tr/content/dam/parafcard/paraf-logos/paraf-troy-logo.png"
            elif "parafly" in first_card:
                card_logo_url = "https://www.parafly.com.tr/content/dam/parafly/parafly-logos/parafly-logo.png"
            elif "paraf" in first_card:
                card_logo_url = "https://www.paraf.com.tr/content/dam/parafcard/paraf-logos/paraf-logo-yeni.png"
        
        campaign = Campaign(
            card_id=primary_card.id,
            sector_id=sector.id if sector else None,
            title=data.get("title"),
            slug=slug,
            description=data.get("description"),
            conditions="\n".join(data.get("conditions", [])),
            start_date=data.get("start_date"),
            end_date=data.get("end_date"),
            reward_type=data.get("reward_type"),
            reward_value=data.get("reward_value"),
            reward_text=data.get("reward_text"),
            
            # AI Fields
            ai_marketing_text=marketing_text,
            eligible_cards=", ".join(target_cards),
            category=data.get("category"),
            badge_color=data.get("badge_color"),
            card_logo_url=card_logo_url,  # Use mapped logo URL
            
            clean_text=ai_data.get('_clean_text') if 'ai_data' in locals() else None,
            tracking_url=url,
            image_url=image_url,
            is_active=True
        )
        
        self.db.add(campaign)
        self.db.commit()
        
        # Link Brands
        for bid in brand_ids:
            try:
                cb = CampaignBrand(campaign_id=campaign.id, brand_id=bid)
                self.db.add(cb)
            except:
                pass
        self.db.commit()

    # --- HELPERS ---
    def _load_cache(self):
        bank = self.db.query(Bank).filter(Bank.slug == "halkbank").first()
        if not bank:
            bank = Bank(name="Halkbank", slug="halkbank", is_active=True)
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
        """Get or create card, but only for known Paraf variants."""
        name_lower = name.lower()
        
        # Prevent AI from creating variants like "Paraf Gold", "Paraf Platinum" etc.
        if "parafly" in name_lower or "fly" in name_lower:
            name = "Parafly"
        elif "troy" in name_lower:
            name = "Paraf TROY"
        else:
            name = "Paraf" # Default all others to main "Paraf"
            
        key = name.lower()
        if key in self.card_cache:
            return self.card_cache[key]
        
        # If not in cache, check DB
        slug_val = name.lower().replace(" ", "-")
        card = self.db.query(Card).filter(
            Card.bank_id == self.bank_cache.id,
            Card.slug == slug_val
        ).first()
        
        if not card:
            print(f"   ➕ Creating standard card: {name}")
            card = Card(
                bank_id=self.bank_cache.id,
                name=name,
                slug=name.lower().replace(" ", "-"),
                is_active=True
            )
            self.db.add(card)
            self.db.flush()
            
        self.card_cache[key] = card
        return card

    def _get_sector(self, name: str) -> Optional[Sector]:
        if not name:
            return None
        if name.lower() in self.sector_cache:
            return self.sector_cache[name.lower()]
        return self.sector_cache.get("diğer")

    def _get_or_create_brands(self, names: List[str], sector_id: int) -> List[int]:
        from sqlalchemy.exc import IntegrityError
        ids = []
        for n in names:
            key = n.lower()
            slug_val = key.replace(" ", "-")
            if key in self.brand_cache:
                ids.append(self.brand_cache[key].id)
            else:
                existing = self.db.query(Brand).filter(Brand.slug == slug_val).first()
                if existing:
                    self.brand_cache[key] = existing
                    ids.append(existing.id)
                    continue
                    
                b = Brand(name=n, slug=slug_val, is_active=True)
                self.db.add(b)
                try:
                    self.db.commit()
                    self.brand_cache[key] = b
                    ids.append(b.id)
                except IntegrityError:
                    self.db.rollback()
                    existing = self.db.query(Brand).filter(Brand.slug == slug_val).first()
                    if existing:
                        self.brand_cache[key] = existing
                        ids.append(existing.id)
        return ids
if __name__ == "__main__":
    try:
        import argparse
        parser = argparse.ArgumentParser()
        parser.add_argument("--limit", type=int, help="Limit campaigns")
        parser.add_argument("--force", action="store_true", help="Force refresh")
        args = parser.parse_args()
        
        scraper = ParafScraper()
        scraper.run(limit=args.limit, force=args.force)
    except Exception as e:
        import traceback
        traceback.print_exc()
        print(f"CRITICAL ERROR: {e}")
