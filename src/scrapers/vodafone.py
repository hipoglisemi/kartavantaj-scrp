# pyre-ignore-all-errors
# type: ignore


import asyncio
import random
import time
import os
import re
import uuid
import sys
import requests
from typing import List, Dict, Any, Optional
from datetime import datetime
from decimal import Decimal
from urllib.parse import urljoin
from bs4 import BeautifulSoup
from sqlalchemy.orm import Session

# Path setup to ensure imports work correctly
project_root = "/Users/hipoglisemi/Desktop/kartavantaj-scraper"
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from src.database import get_db_session
from src.models import Bank, Card, Sector, Brand, Campaign, CampaignBrand
from src.services.ai_parser import AIParser
from src.utils.logger_utils import log_scraper_execution

class VodafoneScraper:
    """
    Vodafone Kampanyaları Scraper
    Uses Requests/BeautifulSoup for high efficiency since content is SSR.
    """
    
    BASE_URL = "https://www.vodafone.com.tr"
    LISTING_URLS = [
        "https://www.vodafone.com.tr/kampanyalar/red-marka-ayricaliklari",
        "https://www.vodafone.com.tr/kampanyalar/freezone-guzellikleri",
        "https://www.vodafone.com.tr/kampanyalar/faturali-kampanyalar",
        "https://www.vodafone.com.tr/kampanyalar/kupon-kodu-kampanyalari",
        "https://www.vodafone.com.tr/kampanyalar/faturasiz-kampanyalar",
        "https://www.vodafone.com.tr/kampanyalar/ev-interneti-kampanyalari"
    ]
    
    def __init__(self, max_campaigns: int = 100, headless: bool = True):
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
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
            "Accept-Language": "tr-TR,tr;q=0.9,en-US;q=0.8,en;q=0.7",
        }

    def run(self):
        """Entry point for execution"""
        print(f"🚀 Starting Vodafone Scraper (Request Mode)...")
        
        try:
            self.db = get_db_session()
            self._load_cache()
            
            # 1. Get List of all campaigns from all categories
            all_links = []
            for url in self.LISTING_URLS:
                print(f"   🌐 Loading listing page: {url}")
                links = self._scrape_list(url)
                print(f"      Found {len(links)} links in category.")
                for link in links:
                    if link not in all_links:
                        all_links.append(link)
            
            print(f"   Total unique campaigns found: {len(all_links)}")
            
            # Limit
            if len(all_links) > self.max_campaigns:
                all_links = all_links[:self.max_campaigns]
            
            # 2. Process Details
            success_count = 0
            for i, url in enumerate(all_links, 1):
                print(f"   [{i}/{len(all_links)}] {url}")
                try:
                    if self._scrape_detail(url):
                        success_count += 1
                    time.sleep(random.uniform(0.5, 1.2))
                except Exception as e:
                    print(f"      ❌ Error processing {url}: {e}")
            
            print(f"\n✅ Scraping complete! Saved {success_count} campaigns.")

        except Exception as e:
            print(f"❌ Fatal error in Vodafone scraper: {e}")
            import traceback
            traceback.print_exc()
        finally:
            if self.db:
                self.db.close()

    def _scrape_list(self, url: str) -> List[str]:
        """Fetch listing page and extract campaign links"""
        try:
            response = requests.get(url, headers=self.headers, timeout=30)
            response.raise_for_status()
            
            soup = BeautifulSoup(response.text, 'html.parser')
            
            links = []
            # Based on research: <a href="/kampanyalar/tchibo-hediye-firsati" class="cmp-card">
            cards = soup.select("a.cmp-card")
            for a in cards:
                href = a.get('href')
                if href and "/kampanyalar/" in href:
                    # Fix: strip whitespace/newlines that might be in the href attribute
                    href = href.strip()
                    full_url = urljoin(self.BASE_URL, href)
                    if full_url not in links:
                        links.append(full_url)
            
            return links
        except Exception as e:
            print(f"      ❌ List extraction failed for {url}: {e}")
            return []

    def _scrape_detail(self, url: str) -> bool:
        """Fetch detail page and extract content from accordions"""
        
        # 1. Duplicate Check
        existing = self.db.query(Campaign).filter(Campaign.tracking_url == url).first()
        if existing:
            print(f"      ⚠️ Skipping (Already exists in DB)")
            return False

        try:
            response = requests.get(url, headers=self.headers, timeout=30)
            response.raise_for_status()
            
            soup = BeautifulSoup(response.text, 'html.parser')
            
            # 2. Extract Basic Info
            h1 = soup.select_one(".gallery--header h1")
            title = h1.get_text(strip=True) if h1 else "Vodafone Kampanyası"
            
            # Enhanced Image extraction
            image_url = None
            banner_img = soup.select_one(".banner__image")
            if banner_img and 'src' in banner_img.attrs:
                image_url = urljoin(self.BASE_URL, banner_img['src'])
            
            if not image_url:
                img_tag = soup.select_one(".img-block img")
                if img_tag and 'src' in img_tag.attrs:
                    image_url = urljoin(self.BASE_URL, img_tag['src'])
            
            # Description (CMS Editor content)
            cms_editor = soup.select_one(".cms-editor")
            description = cms_editor.get_text(separator="\n", strip=True) if cms_editor else ""
            
            # 3. Extract Accordion Content
            # Structure: .Accordion-trigger (button) and .Accordion-panel (content)
            content_parts = []
            participation_text = ""
            
            # Add description as the first part if exists
            if description:
                content_parts.append(f"### Kampanya Hakkında\n{description}")

            accordions = soup.select(".Accordion-trigger")
            for trigger in accordions:
                header_text = trigger.get_text(strip=True)
                panel_id = trigger.get('aria-controls')
                if panel_id:
                    panel = soup.find(id=panel_id)
                    if panel:
                        text = panel.get_text(separator="\n", strip=True)
                        if text:
                            content_parts.append(f"### {header_text}\n{text}")
                            # Participation context
                            lower_header = header_text.lower()
                            if any(x in lower_header for x in ["katılım", "nasıl", "faydalan", "detay"]):
                                participation_text += f"\n[{header_text}]: {text}"

            if not content_parts and not description:
                raw_text = soup.get_text(separator="\n", strip=True)
            else:
                raw_text = "\n\n".join(content_parts)

            # Special Participation metadata
            if participation_text:
                raw_text = f"--- VODAFONE DETAYLAR ---\n{participation_text}\n\n--- TÜM İÇERİK ---\n{raw_text}"

            # AI Parsing
            print(f"      🧠 Sending to AI Parser...")
            ai_data = self.parser.parse_campaign_data(
                raw_text=raw_text,
                title=title,
                bank_name="vodafone",
                card_name="Vodafone"
            )

            if not ai_data:
                print(f"      ❌ AI parsing failed for {url}")
                return False

            # Override/Fixes
            if image_url and (not ai_data.get('image_url') or 'logo' in ai_data.get('image_url', '').lower()):
                ai_data['image_url'] = image_url
            
            # Save to DB
            self._save_campaign(ai_data, url, image_url)
            return True

        except Exception as e:
            print(f"      ❌ Detail error: {e}")
            return False

    def _save_campaign(self, data: Dict[str, Any], url: str, image_url: Optional[str]):
        """Save parsed campaign to DB"""
        
        # Bank & Card
        bank = self.bank_cache
        card = self._get_or_create_card("Vodafone")
        
        # Sector
        sector = self._get_sector(data.get("sector"))
        
        # Brands
        brand_ids = self._get_or_create_brands(data.get("brands", []), sector.id if sector else None)
        
        # Slug
        slug = data.get("slug")
        if not slug:
            clean_title = data.get("title", "").lower()
            tr_map = str.maketrans("ığüşöç", "iguso-")
            clean_title = clean_title.translate(tr_map)
            slug = re.sub(r'[^a-z0-9-]', '-', clean_title)
            slug = re.sub(r'-+', '-', slug).strip('-')
            url_hash = uuid.uuid5(uuid.NAMESPACE_URL, url).hex[:8]
            slug = f"{slug}-{url_hash}"
        
        # Format for CampaignDetailClient.tsx
        participation = data.get("participation", "")
        ai_description = data.get("marketing_text") or data.get("description", "")
        
        if participation.strip():
            clean_part = re.sub(r'\[[^\]]+\]:\s*', '', participation.strip()).strip()
            ai_marketing_text = f"📱 Katılım: {clean_part}\n\n{ai_description}" if ai_description else f"📱 Katılım: {clean_part}"
        else:
            ai_marketing_text = ai_description
            
        campaign = Campaign(
            card_id=card.id,
            sector_id=sector.id if sector else None,
            title=data.get("title"),
            slug=slug,
            description=data.get("description"),
            conditions=data.get("conditions") if not isinstance(data.get("conditions"), list) else "\n".join(data.get("conditions")),
            reward_text=data.get("reward_text"),
            reward_value=data.get("reward_value"),
            reward_type=data.get("reward_type"),
            start_date=data.get("start_date"),
            end_date=data.get("end_date"),
            image_url=image_url or "https://www.vodafone.com.tr/assets/img/vdf-logo.png",
            tracking_url=url,
            is_active=True,
            ai_marketing_text=ai_marketing_text,
            eligible_cards="Vodafone Müşterileri",
            category=data.get("category"),
            badge_color=data.get("badge_color"),
            clean_text=data.get("_clean_text"),
            quality_score=data.get("quality_score", 0)
        )
        
        try:
            self.db.add(campaign)
            self.db.flush()
            
            for bid in brand_ids:
                existing_link = self.db.query(CampaignBrand).filter_by(campaign_id=campaign.id, brand_id=bid).first()
                if not existing_link:
                    cb = CampaignBrand(campaign_id=campaign.id, brand_id=bid)
                    self.db.add(cb)
            
            self.db.commit()
            print(f"      ✅ Saved: {campaign.title}")
        except Exception as e:
            self.db.rollback()
            print(f"      ❌ DB Save Error for {url}: {e}")

    # --- HELPERS ---
    def _load_cache(self):
        bank = self.db.query(Bank).filter(Bank.slug == "vodafone").first()
        if not bank:
            bank = Bank(name="Vodafone", slug="vodafone", is_active=True, logo_url="https://upload.wikimedia.org/wikipedia/commons/thumb/a/af/Vodafone_2017_logo.svg/1200px-Vodafone_2017_logo.svg.png")
            self.db.add(bank)
            self.db.commit()
        self.bank_cache = bank
        for c in self.db.query(Card).filter(Card.bank_id == bank.id).all():
            self.card_cache[c.name.lower()] = c
        for s in self.db.query(Sector).all():
            self.sector_cache[s.name.lower()] = s
        for b in self.db.query(Brand).filter(Brand.is_active == True).limit(500).all():
            self.brand_cache[b.name.lower()] = b

    def _get_or_create_card(self, name: str) -> Card:
        key = name.lower()
        if key in self.card_cache: return self.card_cache[key]
        card = Card(bank_id=self.bank_cache.id, name=name, slug=name.lower().replace(" ", "-"), is_active=True)
        self.db.add(card)
        self.db.flush()
        self.card_cache[key] = card
        return card

    def _get_sector(self, name: str) -> Optional[Sector]:
        if not name: return None
        return self.sector_cache.get(name.lower()) or self.sector_cache.get("diğer")

    def _get_or_create_brands(self, names: List[str], sector_id: Optional[int]) -> List[uuid.UUID]:
        ids = []
        for n in names:
            if not n: continue
            key = n.lower().strip()
            if key in self.brand_cache:
                ids.append(self.brand_cache[key].id)
            else:
                brand = self.db.query(Brand).filter(Brand.name.ilike(n)).first()
                if not brand:
                    brand = Brand(name=n, slug=key.replace(" ", "-")[:50], is_active=True)
                    self.db.add(brand)
                    self.db.flush()
                self.brand_cache[key] = brand
                ids.append(brand.id)
        return list(set(ids))

if __name__ == "__main__":
    max_c = 5 if os.environ.get('TEST_MODE') == '1' else 999
    scraper = VodafoneScraper(max_campaigns=max_c)
    scraper.run()
