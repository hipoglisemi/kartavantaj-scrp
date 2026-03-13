# pyre-ignore-all-errors
# type: ignore


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
from src.utils.logger_utils import log_scraper_execution  # type: ignore

class TurkcellScraper:
    """
    Turkcell Marka Kampanyaları Scraper
    Uses Playwright for lazy loading and accordion expansion.
    """
    
    BASE_URL = "https://www.turkcell.com.tr"
    LISTING_URL = "https://www.turkcell.com.tr/kampanyalar/marka-kampanyalari/marka-kampanyalari"
    
    def __init__(self, max_campaigns: int = 20, headless: bool = False):
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
                browser = await p.chromium.launch(
                    headless=self.headless,
                    args=["--no-sandbox", "--disable-dev-shm-usage"]
                )
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
                if links and self.max_campaigns:
                    links = list(links)[:int(self.max_campaigns)]  # type: ignore
                
                # 2. Process Details
                success_count = 0
                for i, url in enumerate(links, 1):
                    print(f"   [{i}/{len(links)}] {url}")
                    try:
                        if await self._scrape_detail(context, url):
                            success_count = int(success_count or 0) + 1  # type: ignore
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
            if hasattr(self, 'db') and self.db:
                self.db.close()

    async def _scrape_list(self, page: Page) -> List[str]:
        """Scroll to the bottom to handle lazy loading and extract campaign links"""
        print(f"   🌐 Loading listing page: {self.LISTING_URL}")
        try:
            await page.goto(self.LISTING_URL, wait_until="networkidle", timeout=90000)
            
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
        if self.db is None:
            print(f"      ❌ DB session not initialized")
            return False
            
        if self.db is not None:
            existing = self.db.query(Campaign).filter(Campaign.tracking_url == url).first()
            if existing:
                print(f"      ⚠️ Skipping (Already exists in DB)")
                return False
        else:
            print(f"      ⚠️ DB session is None, cannot check for duplicates")

        page = await context.new_page()
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=45000)
            
            # Wait a bit for dynamic elements
            await asyncio.sleep(2)
            
            # 2. Extract Basic Info
            title = await page.inner_text("h1") if await page.query_selector("h1") else "Turkcell Kampanyası"
            
            # --- IMAGE FIX ---
            # Smart Image Selection: Find the largest non-logo image on the page
            image_url = await page.evaluate('''() => {
                const imgs = Array.from(document.querySelectorAll('img'));
                const candidates = imgs
                    .map(img => ({
                        src: img.src,
                        area: img.naturalWidth * img.naturalHeight,
                        width: img.naturalWidth,
                        isLogo: img.src.toLowerCase().includes('logo') || img.className.toLowerCase().includes('logo') || img.src.includes('nav')
                    }))
                    .filter(c => c.area > 15000 && !c.isLogo) // Minimum size approx 120x120
                    .sort((a, b) => b.area - a.area);
                
                return candidates.length > 0 ? candidates[0].src : null;
            }''')
            
            if not image_url:
                # Fallback: specific container check
                image_url = await page.evaluate('''() => {
                    const banner = document.querySelector('.Detail_detail__image__omC5p img, [class*="Detail_detail__image"] img');
                    if (banner && !banner.src.includes('logo')) return banner.src;
                    return null;
                }''')
            
            # 3. Expand Accordions & Extract Participation
            print(f"      📂 Expanding detail accordions...")
            headers = await page.query_selector_all('div.ant-collapse-header')
            content_parts = []
            participation_text = ""
            
            for header in headers:
                try:
                    header_text = (await header.inner_text()).strip()
                    if not header_text: continue
                    
                    # 1. Scroll and Click
                    await header.scroll_into_view_if_needed()
                    # Use evaluate to click direct DOM to avoid overlays
                    await page.evaluate('(h) => h.click()', header)
                    await asyncio.sleep(1.2) # Increased wait for expansion animation
                    
                    # 2. Extract content from the item
                    text = await page.evaluate('''(header) => {
                        const item = header.closest('.ant-collapse-item');
                        if (!item) return "";
                        const contentBox = item.querySelector('.ant-collapse-content');
                        return contentBox ? contentBox.innerText : "";
                    }''', header)
                    
                    if text.strip():
                        print(f"         ✅ Extracted: {header_text[:30]}...")
                        content_parts.append(f"### {header_text}\n{text}")
                        # --- PARTICIPATION FIX ---
                        if any(x in header_text.lower() for x in ["katılım", "nasil faydalanirim", "satın alma", "kampanya detayları"]):
                            participation_text = str(participation_text) + f"\n[{header_text}]: {text}"

                except Exception as ex:
                    print(f"         ⚠️ Error expanding section {header_text if 'header_text' in locals() else ''}: {ex}")

            if content_parts:
                raw_text = "\n\n".join(content_parts)
            else:
                # Fallback to body text if no accordions found or extracted
                raw_text = await page.evaluate("document.body.innerText")
            
            # Append explicit participation info for AI - make it very loud
            if participation_text:
                raw_text = f"--- TURKCELL KATILIM DETAYLARI ---\n{participation_text}\n\n--- TÜM İÇERİK ---\n{raw_text}"

            # AI Parsing
            print(f"      🧠 Sending to AI Parser...")
            ai_data = self.parser.parse_campaign_data(
                raw_text=raw_text,
                title=title,
                bank_name="turkcell",
                card_name="Turkcell"
            )
            
            # CRITICAL: Override participation with directly extracted accordion text
            # The AI's _clean_text often strips short lines, making participation generic
            if participation_text.strip():
                # Clean up the accordion text into a readable format
                clean_participation = participation_text.strip()
                # Remove JSON-unsafe chars but keep Turkish characters
                clean_participation = clean_participation.replace("[Katılım Kriterleri]: ", "**Katılım Kriterleri:**\n")
                clean_participation = clean_participation.replace("[Diğer Satın Alma Seçenekleri]: ", "**Diğer Satın Alma Seçenekleri:**\n")
                clean_participation = clean_participation.replace("[Kampanya Detayları]: ", "**Kampanya Detayları:**\n")
                # Only override if the AI gave generic text
                current_participation = ai_data.get('participation', '')
                if not current_participation or 'talimatları izleyerek' in current_participation or 'Otomatik' in current_participation:
                    ai_data['participation'] = clean_participation[:800]
                    print(f"         ✅ Participation override applied (accordion text)")
            
            # Explicitly set image if metadata extraction missed it or picked logo
            if image_url and (not ai_data.get('image_url') or 'logo' in ai_data.get('image_url', '').lower()):
                ai_data['image_url'] = image_url
            
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
        
        # Build ai_marketing_text with participation in the format frontend expects
        # CampaignDetailClient.tsx uses regex: /📱 Katılım:\s*([\s\S]+?)(?:\n\n|$)/
        participation = data.get("participation", "")
        ai_description = data.get("marketing_text") or data.get("description", "")
        
        # Try the override from accordion (stored in data by the scraper override above)
        # If participation is still generic, use accordion text directly
        if not participation or 'talimatları izleyerek' in participation:
            participation = ""  # Will be empty → ai_marketing_text won't have participation section
        
        if participation.strip():
            # Clean up the participation text
            clean_part = participation.strip()
            # Remove the bracket-style labels we added
            import re as _re
            clean_part = _re.sub(r'\[[^\]]+\]:\s*', '', clean_part).strip()
            ai_marketing_text = f"📱 Katılım: {clean_part}\n\n{ai_description}" if ai_description else f"📱 Katılım: {clean_part}"
        else:
            ai_marketing_text = ai_description
        
        # Build conditions (without participation prepended anymore — it's in ai_marketing_text)
        ai_conditions = data.get("conditions", "")
        if isinstance(ai_conditions, list):
            ai_conditions = "\n".join(ai_conditions)
        
        campaign = Campaign(
            card_id=card.id,
            sector_id=sector.id if sector else None,
            title=data.get("title"),
            slug=slug,
            description=data.get("description"),
            conditions=ai_conditions,
            reward_text=data.get("reward_text"),
            reward_value=data.get("reward_value"),
            reward_type=data.get("reward_type"),
            start_date=data.get("start_date"),
            end_date=data.get("end_date"),
            image_url=image_url or "https://www.turkcell.com.tr/assets/img/turkcell-logo.png",
            tracking_url=url,
            is_active=True,
            ai_marketing_text=ai_marketing_text,
            eligible_cards=data.get("eligible_cards") or data.get("cards") or "Turkcell Müşterileri",
            category=data.get("category"),
            badge_color=data.get("badge_color"),
            card_logo_url=None,  # Let card.logo_url from cards table take priority
            clean_text=data.get("_clean_text"),
            quality_score=data.get("quality_score", 0)
        )
        
        try:
            if self.db is not None:
                self.db.add(campaign)
                self.db.flush()
            else:
                print(f"      ❌ DB session is None, cannot save campaign")
                return
            
            # Link Brands
            for bid in brand_ids:
                try:
                    # Check if already linked to avoid duplicate CampaignBrand entries
                    existing_link = self.db.query(CampaignBrand).filter_by(campaign_id=campaign.id, brand_id=bid).first()
                    if not existing_link and self.db is not None:
                        cb = CampaignBrand(campaign_id=campaign.id, brand_id=bid)
                        self.db.add(cb)
                        self.db.flush()
                except Exception as cb_e:
                    self.db.rollback()
                    print(f"         ⚠️ Error linking brand {bid}: {cb_e}")
                    # Re-acquire session or continue? For now, we just skip the broken link
            
            self.db.commit()
            print(f"      ✅ Saved: {campaign.title}")
        except Exception as e:
            self.db.rollback()
            print(f"      ❌ DB Save Error for {url}: {e}")

    # --- CACHE & DB HELPERS ---
    def _load_cache(self):
        # Bank
        bank = self.db.query(Bank).filter(Bank.slug == "turkcell").first()
        if not bank:
            bank = Bank(name="Turkcell", slug="turkcell", is_active=True, logo_url="https://upload.wikimedia.org/wikipedia/en/thumb/5/53/Turkcell_logo.svg/1200px-Turkcell_logo.svg.png")
            if self.db is not None:
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
        
        if self.db is None or self.bank_cache is None:
            return None
        
        card = self.db.query(Card).filter(Card.bank_id == self.bank_cache.id, Card.name == name).first()
        if not card:
            card = Card(
                bank_id=self.bank_cache.id,
                name=name,
                slug=name.lower().replace(" ", "-"),
                is_active=True,
                image_url="https://upload.wikimedia.org/wikipedia/en/thumb/5/53/Turkcell_logo.svg/1200px-Turkcell_logo.svg.png"
            )
            if self.db is not None:
                self.db.add(card)
                self.db.flush()
        
        self.card_cache[key] = card
        return card

    def _get_sector(self, name: Any) -> Optional[Sector]:
        if not name or not isinstance(name, str): return None
        return self.sector_cache.get(name.lower()) or self.sector_cache.get("diğer")

    def _get_or_create_brands(self, names: List[str], sector_id: Optional[int]) -> List[uuid.UUID]:
        ids = []
        for n in names:
            if not n: continue
            key = n.lower().strip()
            if key in self.brand_cache:
                ids.append(self.brand_cache[key].id)
            else:
                try:
                    brand = self.db.query(Brand).filter(Brand.name.ilike(n)).first()
                    if not brand:
                        brand = Brand(name=n, slug=key.replace(" ", "-")[:50], is_active=True)
                        if self.db is not None:
                            self.db.add(brand)
                            self.db.flush()
                    self.brand_cache[key] = brand
                    ids.append(brand.id)
                except Exception as e:
                    self.db.rollback()
                    # Try to fetch it again, maybe it was created in another transaction
                    brand = self.db.query(Brand).filter(Brand.name.ilike(n)).first()
                    if brand:
                        self.brand_cache[key] = brand
                        ids.append(brand.id)
                    else:
                        print(f"         ⚠️ Could not create/find brand {n}: {e}")
        return list(set(ids)) # Deduplicate

if __name__ == "__main__":
    # Standard TEST_MODE handling
    # If TEST_MODE=1, SQLAlchemy models will automatically use test_campaigns table
    max_c = 10 if os.environ.get('TEST_MODE') == '1' else 999
    scraper = TurkcellScraper(max_campaigns=max_c, headless=True)
    scraper.run()
