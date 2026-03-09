
import asyncio
import random
import time
import os
import re
import uuid
import sys
from typing import List, Dict, Any, Optional
from datetime import datetime
from decimal import Decimal
from urllib.parse import urljoin

from playwright.async_api import async_playwright, Page
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

class TurkcellScraper:
    """
    Turkcell Marka Kampanyaları Scraper
    Uses Playwright for lazy loading and accordion expansion.
    """
    
    BASE_URL = "https://www.turkcell.com.tr"
    LISTING_URL = "https://www.turkcell.com.tr/kampanyalar/marka-kampanyalari/marka-kampanyalari"
    
    def __init__(self, max_campaigns: int = 20, headless: bool = True):
        self.max_campaigns = max_campaigns
        self.headless = headless
        self.db: Optional[Session] = None
        self.parser = AIParser()
        
        # Cache
        self.bank_cache: Optional[Bank] = None
        self.card_cache: Dict[str, Card] = {}
        self.sector_cache: Dict[str, Sector] = {}
        self.brand_cache: Dict[str, Brand] = {}

    def run(self):
        """Entry point for synchronous execution"""
        asyncio.run(self._run_async())

    async def _run_async(self):
        """Main async execution flow"""
        print(f"🚀 Starting Turkcell Scraper...")
        
        try:
            self.db = get_db_session()
            self._load_cache()
            
            async with async_playwright() as p:
                browser = await p.chromium.launch(headless=self.headless)
                context = await browser.new_context(
                    user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                    viewport={'width': 1280, 'height': 800}
                )
                
                # 1. Get List
                page = await context.new_page()
                links = await self._scrape_list(page)
                await page.close()
                print(f"   Found {len(links)} campaigns in total.")
                
                # Limit
                if len(links) > self.max_campaigns:
                    links = links[:self.max_campaigns]
                
                # 2. Process Details
                success_count = 0
                for i, url in enumerate(links, 1):
                    print(f"   [{i}/{len(links)}] {url}")
                    try:
                        if await self._scrape_detail(context, url):
                            success_count += 1
                        await asyncio.sleep(random.uniform(1, 2))
                    except Exception as e:
                        print(f"      ❌ Error processing {url}: {e}")
                
                await browser.close()
                
            print(f"\n✅ Scraping complete! Saved {success_count} campaigns.")

        except Exception as e:
            print(f"❌ Fatal error in Turkcell scraper: {e}")
            import traceback
            traceback.print_exc()
        finally:
            if self.db:
                self.db.close()

    async def _scrape_list(self, page: Page) -> List[str]:
        """Scroll to the bottom to handle lazy loading and extract campaign links"""
        print(f"   🌐 Loading listing page: {self.LISTING_URL}")
        try:
            await page.goto(self.LISTING_URL, wait_until="networkidle", timeout=60000)
            
            # Dismiss initial popups if any
            try:
                # Potential selectors for "Daha Sonra" or cookie banners
                banners = await page.query_selector_all("button:has-text('Kabul Et'), button:has-text('Daha Sonra'), .close-icon")
                for btn in banners:
                    if await btn.is_visible():
                        await btn.click()
            except:
                pass

            # Lazy loading scroll loop
            last_height = await page.evaluate("document.body.scrollHeight")
            for i in range(10): # Limit scrolls to prevent infinite loops
                await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                await asyncio.sleep(2) # Wait for new content to load
                new_height = await page.evaluate("document.body.scrollHeight")
                if new_height == last_height:
                    break
                last_height = new_height
                print(f"      🔃 Scrolled ({i+1})...")

            # Extract Links
            # Card selectors identified during research
            # Titles: h4.atom-card-v2_a-cardV2__title__nS3m9
            elements = await page.query_selector_all('a:has(h4[class*="title"])')
            links = []
            for el in elements:
                href = await el.get_attribute('href')
                if href:
                    full_url = urljoin(self.BASE_URL, href)
                    if full_url not in links:
                        links.append(full_url)
            
            return links
        except Exception as e:
            print(f"   ❌ List extraction failed: {e}")
            return []

    async def _scrape_detail(self, context, url: str) -> bool:
        """Scrape single campaign page by expanding accordions"""
        
        # 1. Duplicate Check
        existing = self.db.query(Campaign).filter(Campaign.tracking_url == url).first()
        if existing:
            print(f"      ⚠️ Skipping (Already exists in DB)")
            return False

        page = await context.new_page()
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=45000)
            
            # Wait a bit for dynamic elements
            await asyncio.sleep(2)
            
            # 2. Extract Basic Info
            title = await page.inner_text("h1") if await page.query_selector("h1") else "Turkcell Kampanyası"
            
            # 3. Expand Accordions
            print(f"      📂 Expanding detail accordions...")
            # Accordion headers identified: div.ant-collapse-header
            headers = await page.query_selector_all('div.ant-collapse-header')
            content_parts = []
            
            for header in headers:
                try:
                    # Check if already expanded (aria-expanded or checking visibility of content box)
                    span = await header.query_selector("span[aria-expanded]")
                    expanded = await span.get_attribute("aria-expanded") == "true" if span else False
                    
                    if not expanded:
                        await header.click()
                        await asyncio.sleep(0.5) # Fast expansion
                    
                    # Find the corresponding content box
                    # Usually next sibling or child depending on Ant Design version
                    # Let's just get the text after click
                    text = await page.evaluate('''(header) => {
                        const panel = header.closest('.ant-collapse-item');
                        if (panel) {
                            const body = panel.querySelector('.ant-collapse-content-box');
                            return body ? body.innerText : "";
                        }
                        return "";
                    }''', header)
                    
                    if text.strip():
                        header_text = await header.inner_text()
                        content_parts.append(f"### {header_text}\n{text}")
                except Exception as ex:
                    print(f"         ⚠️ Error expanding section: {ex}")

            # 4. Fallback search for text if no accordions found
            if not content_parts:
                raw_text = await page.evaluate("document.body.innerText")
            else:
                raw_text = "\n\n".join(content_parts)

            # 5. Image Extraction
            image_url = await page.evaluate('''() => {
                const img = document.querySelector('img[class*="campaign-hero"], .campaign-detail img, main img');
                return img ? img.src : null;
            }''')

            # 6. AI Parsing
            print(f"      🧠 Sending to AI Parser...")
            ai_data = self.parser.parse_campaign_data(
                raw_text=raw_text,
                title=title,
                bank_name="turkcell",
                card_name="Turkcell"
            )
            
            if not ai_data:
                print(f"      ❌ AI parsing failed for {url}")
                return False
                
            # 7. Save to DB
            self._save_campaign(ai_data, url, image_url)
            return True
            
        except Exception as e:
            print(f"      ❌ Detail error: {e}")
            return False
        finally:
            await page.close()

    def _save_campaign(self, data: Dict[str, Any], url: str, image_url: Optional[str]):
        """Save parsed campaign to DB"""
        
        # Bank & Card
        bank = self.bank_cache
        card = self._get_or_create_card("Turkcell")
        
        # Sector
        sector = self._get_sector(data.get("sector"))
        
        # Brands
        brand_ids = self._get_or_create_brands(data.get("brands", []), sector.id if sector else None)
        
        # Slug
        slug = data.get("slug")
        if not slug:
            clean_title = data.get("title", "").lower()
            clean_title = clean_title.replace("ı", "i").replace("ğ", "g").replace("ü", "u").replace("ş", "s").replace("ö", "o").replace("ç", "c")
            slug = re.sub(r'[^a-z0-9-]', '-', clean_title)
            slug = re.sub(r'-+', '-', slug).strip('-')
            url_hash = uuid.uuid5(uuid.NAMESPACE_URL, url).hex[:8]
            slug = f"{slug}-{url_hash}"
        
        campaign = Campaign(
            card_id=card.id,
            sector_id=sector.id if sector else None,
            title=data.get("title"),
            slug=slug,
            description=data.get("description"),
            conditions="\n".join(data.get("conditions", [])) if isinstance(data.get("conditions"), list) else data.get("conditions"),
            reward_text=data.get("reward_text"),
            reward_value=data.get("reward_value"),
            reward_type=data.get("reward_type"),
            start_date=data.get("start_date"),
            end_date=data.get("end_date"),
            image_url=image_url or "https://www.turkcell.com.tr/assets/img/turkcell-logo.png",
            tracking_url=url,
            is_active=True,
            ai_marketing_text=data.get("marketing_text") or data.get("description"),
            eligible_cards=data.get("eligible_cards") or "Turkcell Müşterileri",
            category=data.get("category"),
            badge_color=data.get("badge_color"),
            card_logo_url=data.get("card_logo_url") or "https://upload.wikimedia.org/wikipedia/en/thumb/5/53/Turkcell_logo.svg/1200px-Turkcell_logo.svg.png",
            clean_text=data.get("_clean_text"),
            quality_score=data.get("quality_score", 0)
        )
        
        try:
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
            print(f"      ✅ Saved: {campaign.title}")
        except Exception as e:
            self.db.rollback()
            print(f"      ❌ DB Save Error: {e}")

    # --- CACHE & DB HELPERS ---
    def _load_cache(self):
        # Bank
        bank = self.db.query(Bank).filter(Bank.slug == "turkcell").first()
        if not bank:
            bank = Bank(name="Turkcell", slug="turkcell", is_active=True, logo_url="https://upload.wikimedia.org/wikipedia/en/thumb/5/53/Turkcell_logo.svg/1200px-Turkcell_logo.svg.png")
            self.db.add(bank)
            self.db.commit()
        self.bank_cache = bank
        
        # Cards
        for c in self.db.query(Card).filter(Card.bank_id == bank.id).all():
            self.card_cache[c.name.lower()] = c
            
        # Sectors
        for s in self.db.query(Sector).all():
            self.sector_cache[s.name.lower()] = s
            
        # Brands
        # Only cache active brands to avoid memory issues
        # Use UUID string for dict key if needed, or just standard name
        for b in self.db.query(Brand).filter(Brand.is_active == True).limit(500).all():
            self.brand_cache[b.name.lower()] = b

    def _get_or_create_card(self, name: str) -> Card:
        key = name.lower()
        if key in self.card_cache:
            return self.card_cache[key]
        
        card = self.db.query(Card).filter(Card.bank_id == self.bank_cache.id, Card.name == name).first()
        if not card:
            card = Card(
                bank_id=self.bank_cache.id,
                name=name,
                slug=name.lower().replace(" ", "-"),
                is_active=True,
                image_url="https://upload.wikimedia.org/wikipedia/en/thumb/5/53/Turkcell_logo.svg/1200px-Turkcell_logo.svg.png"
            )
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
            key = n.lower()
            if key in self.brand_cache:
                ids.append(self.brand_cache[key].id)
            else:
                brand = self.db.query(Brand).filter(Brand.name == n).first()
                if not brand:
                    brand = Brand(name=n, slug=key.replace(" ", "-"), is_active=True)
                    self.db.add(brand)
                    self.db.flush()
                self.brand_cache[key] = brand
                ids.append(brand.id)
        return ids

if __name__ == "__main__":
    # Standard TEST_MODE handling
    # If TEST_MODE=1, SQLAlchemy models will automatically use test_campaigns table
    max_c = 10 if os.environ.get('TEST_MODE') == '1' else 999
    scraper = TurkcellScraper(max_campaigns=max_c, headless=True)
    scraper.run()
