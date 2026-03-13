
import sys
import os
import time
import re
import uuid
import asyncio
import random
from datetime import datetime
from typing import Optional, Dict, Any, List, Tuple
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

class KuveytTurkScraper:
    """
    Kuveyt Türk Sağlam Kart campaign scraper - Modernized with Playwright
    Handles 'Load More' button and uses AI for parsing.
    """

    BASE_URL = "https://saglamkart.kuveytturk.com.tr"
    CAMPAIGNS_URL = "https://saglamkart.kuveytturk.com.tr/kampanyalar"
    BANK_NAME = "Kuveyt Türk"
    CARD_SLUG = "saglam-kart"

    def __init__(self, max_campaigns: int = 999, headless: bool = True):
        self.max_campaigns = max_campaigns
        self.headless = headless
        self.db: Optional[Session] = None
        self.parser = AIParser()
        
        # Cache
        self.bank_cache: Optional[Bank] = None
        self.card_cache: Dict[str, Card] = {}
        self.sector_cache: Dict[str, Sector] = {}
        self.brand_cache: Dict[str, Brand] = {}

    def run(self, limit: Optional[int] = None):
        """Entry point for synchronous execution"""
        if limit: self.max_campaigns = limit
        asyncio.run(self._run_async())

    async def _run_async(self):
        """Main async execution flow"""
        print(f"🚀 Starting {self.BANK_NAME} Scraper...")
        start_time = time.time()
        stats = {'total': 0, 'new': 0, 'updated': 0, 'failed': 0}
        
        try:
            self.db = get_db_session()
            self._load_cache()
            
            bank_id = self.bank_cache.id
            card_id = self._get_or_create_card("Sağlam Kart").id
            
            async with async_playwright() as p:
                # Using Chromium (compatible with both local and CI environments)
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
                urls, expired_urls = await self._scrape_list(page)
                await page.close()
                
                # Disable expired
                self.disable_expired_campaigns(expired_urls)
                
                print(f"   Found {len(urls)} active campaigns.")
                
                # Limit
                if len(urls) > self.max_campaigns:
                    urls = urls[:self.max_campaigns]
                
                # 2. Process Details
                for i, url in enumerate(urls, 1):
                    print(f"\n[{i}/{len(urls)}] Processing: {url}")
                    stats['total'] += 1
                    try:
                        # Existing check
                        existing = self.db.query(Campaign).filter_by(tracking_url=url).first()
                        is_test_mode = os.environ.get('TEST_MODE') == '1'
                        
                        if existing and not is_test_mode:
                            if existing.updated_at and (datetime.utcnow() - existing.updated_at).days < 2:
                                print(f"   ⏭️  Skipping recently updated campaign.")
                                continue

                        if await self._scrape_single_detail(context, url, bank_id, card_id, stats):
                            pass
                        await asyncio.sleep(random.uniform(1, 2))
                    except Exception as e:
                        print(f"      ❌ Error processing {url}: {e}")
                        stats['failed'] += 1
                
                await browser.close()
                
            elapsed = time.time() - start_time
            print(f"\n🎉 {self.BANK_NAME} scraping completed in {elapsed:.1f}s")
            print(f"📊 Stats: {stats['total']} processed | {stats['new']} new | {stats['updated']} updated | {stats['failed']} failed")
            
            log_scraper_execution(
                db=self.db,
                scraper_name=f"{self.BANK_NAME} Scraper",
                status="COMPLETED",
                total_found=stats['total'],
                total_saved=stats['new'] + stats['updated'],
                total_failed=stats['failed']
            )

        except Exception as e:
            print(f"❌ Fatal error in Kuveyt Turk scraper: {e}")
            import traceback
            traceback.print_exc()
        finally:
            if self.db:
                self.db.close()

    async def _scrape_list(self, page: Page) -> Tuple[List[str], List[str]]:
        """Handles 'Load More' button to get all campaigns"""
        print(f"   🌐 Loading campaigns list: {self.CAMPAIGNS_URL}")
        active_urls = set()
        expired_urls = set()
        
        try:
            await page.goto(self.CAMPAIGNS_URL, wait_until="networkidle", timeout=60000)
            
            # 1. Wait for regular items
            await page.wait_for_selector(".campaign-card, a[href*='/kampanyalar/']", timeout=30000)

            # Click "Daha Fazla Göster" loop - Exit when count stops increasing
            click_count = 0
            MAX_CLICKS = 30
            consecutive_no_growth = 0
            
            while click_count < MAX_CLICKS:
                try:
                    # Count current unique campaign URLs
                    current_count = await page.evaluate('''() => {
                        const links = Array.from(document.querySelectorAll("a[href*='/kampanyalar/']"));
                        const unique = new Set(links.map(a => a.href).filter(h => !h.includes('biten-kampanyalar') && h.includes('/kampanyalar/')));
                        return unique.size;
                    }''')
                    print(f"      📊 Current unique campaigns visible: {current_count}")
                    
                    button = page.locator(".show-more")
                    if await button.count() == 0:
                        print(f"      ✨ No more 'Daha Fazla Göster' button. Total clicks: {click_count}")
                        break
                    
                    # Click
                    await page.evaluate('''() => {
                        const btn = document.querySelector('.show-more');
                        if (btn) btn.click();
                    }''')
                    click_count += 1
                    print(f"      👇 Clicked 'Daha Fazla Göster' ({click_count})...")
                    
                    await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                    await asyncio.sleep(3)
                    
                    # Check if count grew
                    new_count = await page.evaluate('''() => {
                        const links = Array.from(document.querySelectorAll("a[href*='/kampanyalar/']"));
                        const unique = new Set(links.map(a => a.href).filter(h => !h.includes('biten-kampanyalar') && h.includes('/kampanyalar/')));
                        return unique.size;
                    }''')
                    
                    if new_count <= current_count:
                        consecutive_no_growth += 1
                        print(f"      ⚠️ No new campaigns after click (attempt {consecutive_no_growth}/3)...")
                        if consecutive_no_growth >= 3:
                            print(f"      ✅ Pagination done (no growth for 3 consecutive clicks).")
                            break
                    else:
                        consecutive_no_growth = 0
                        
                except Exception as b_err:
                    print(f"      ⚠️ Pagination interaction issue: {b_err}")
                    break

            # Extract Links
            content = await page.content()
            soup = BeautifulSoup(content, "html.parser")
            
            # Robust extraction - filter duplicates early
            potential_urls = set()
            all_a = soup.find_all("a", href=True)
            for a in all_a:
                href = a.get("href", "")
                if "/kampanyalar/" in href and "/biten-kampanyalar" not in href:
                    full_url = urljoin(self.BASE_URL, href)
                    potential_urls.add(full_url)
            
            print(f"      🎯 Found {len(potential_urls)} unique campaign links.")
            
            for full_url in potential_urls:
                # Check for "expired" indicators in URL
                if any(x in full_url.lower() for x in ["/arsiv", "/gecmis"]):
                    expired_urls.add(full_url)
                    continue
                
                # For Kuveyt Turk, we consider them active unless in /biten-kampanyalar (already filtered)
                active_urls.add(full_url)
                    
        except Exception as e:
            print(f"      ❌ List load failed: {e}")
            
        return list(active_urls), list(expired_urls)

    async def _scrape_single_detail(self, context, url: str, bank_id: int, card_id: int, stats: Dict) -> bool:
        # Database Pre-check (Skip Logic)
        try:
            with get_db_session() as db:
                existing = db.query(Campaign).filter(Campaign.tracking_url == url).first()
                if existing:
                    print(f"   ⏭️ Skipped (Already exists): {url}")
                    stats["skipped"] += 1
                    return True
        except Exception as e:
            print(f"   ⚠️ DB Pre-check error: {e}")

        page = await context.new_page()
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=45000)
            await asyncio.sleep(1)
            
            html_content = await page.content()
            soup = BeautifulSoup(html_content, "html.parser")
            
            title_el = soup.select_one("h1, .campaign-title, .title h2, .subpage-header h1")
            title = self._clean(title_el.text) if title_el else "Başlık Yok"
            
            # Clean unwanted elements
            for unwanted in soup.select("header, nav, .nav-wrapper, .breadcrumb, footer, .subpage-header"):
                unwanted.decompose()

            content_row = soup.select_one("div.row.search-content")
            
            main_description = ""
            conditions_text = ""
            if content_row:
                text_col = content_row.select_one("div.col-md-6:nth-child(1)")
                if text_col:
                    list_items = text_col.select("ul.list > li")
                    if list_items:
                        main_description = self._clean(list_items[0].get_text())
                        conditions_text = "\n".join([f"- {self._clean(li.get_text())}" for li in list_items[1:]])
            
            if not main_description:
                content_div = soup.select_one(".search-content, .subpage-wrapper .container, .ck-content")
                if content_div:
                    main_description = self._clean(content_div.get_text())[:800]
            
            # Image
            image_url = None
            img_candidates = soup.select("img")
            for img in img_candidates:
                src = img.get("src") or img.get("data-src")
                if not src or src.startswith("data:"): continue
                lower_src = src.lower()
                if any(x in lower_src for x in ["campaign", "kampanya", "detail", ".vsf", "banner"]):
                    image_url = urljoin(self.BASE_URL, src)
                    break
            
            if not image_url and content_row:
                img_el = content_row.select_one("img")
                if img_el:
                    image_url = urljoin(self.BASE_URL, img_el.get("src") or img_el.get("data-src"))

            # Text for AI
            full_raw_text = f"BAŞLIK: {title}\n\n"
            full_raw_text += f"KAMPANYA ANA ÖZETİ (AÇIKLAMA): {main_description}\n\n"
            full_raw_text += f"TÜM KAMPANYA KOŞULLARI VE KATILIM DETAYLARI:\n{conditions_text}\n\n"
            
            print("   🤖 Parsing with AI...")
            parsed_data = self.parser.parse_campaign_data(
                raw_text=full_raw_text, bank_name=self.BANK_NAME
            )
            
            if not parsed_data:
                print("   ❌ AI Parse failed")
                stats['failed'] += 1
                return False
                
            raw_data = {
                "title": title,
                "image_url": image_url,
                "description": main_description,
                "raw_text": full_raw_text,
                "source_url": url,
                "date_text": conditions_text # Fallback for date extraction
            }
            
            is_new, success = self._save_campaign(bank_id, card_id, parsed_data, raw_data)
            if success:
                if is_new: stats['new'] += 1
                else: stats['updated'] += 1
                return True
            else:
                stats['failed'] += 1
                return False
                
        except Exception as e:
            print(f"      ❌ Detail error: {e}")
            return False
        finally:
            await page.close()

    def _save_campaign(self, bank_id: int, card_id: int, parsed_data: Dict[str, Any], raw_data: Dict[str, Any]):
        title = raw_data["title"]
        slug = self._generate_slug(title)
        source_url = raw_data["source_url"]

        campaign = self.db.query(Campaign).filter_by(tracking_url=source_url).first()
        is_new = not campaign

        if not campaign:
            campaign = Campaign(tracking_url=source_url, card_id=card_id)
            self.db.add(campaign)

        campaign.slug = slug
        campaign.title = title
        campaign.description = parsed_data.get("description") or raw_data.get("description", "")
        campaign.image_url = raw_data.get("image_url") or parsed_data.get("image_url")
        campaign.is_active = True
        campaign.updated_at = datetime.utcnow()
        campaign.clean_text = raw_data.get("raw_text")
        
        # Meta
        campaign.start_date = self._parse_date_string(parsed_data.get("start_date")) or datetime.now().date()
        campaign.end_date = self._parse_date_string(parsed_data.get("end_date"))
        
        campaign.sector_id = self._get_sector_id(parsed_data.get("sector"))
        
        # eligible_cards: ai_parser'dan liste veya string gelebilir — her zaman string kaydet
        cards_raw = parsed_data.get("cards") or []
        if isinstance(cards_raw, list):
            campaign.eligible_cards = ", ".join([c for c in cards_raw if c]) or None
        else:
            campaign.eligible_cards = str(cards_raw).strip() or None
        if campaign.eligible_cards and len(campaign.eligible_cards) > 255:
            campaign.eligible_cards = campaign.eligible_cards[:255]

        # conditions: ai_parser'dan liste veya string gelebilir — her zaman \n-joined string kaydet
        conditions_raw = parsed_data.get("conditions") or []
        if isinstance(conditions_raw, list):
            campaign.conditions = "\n".join([c for c in conditions_raw if c]) or None
        else:
            campaign.conditions = str(conditions_raw).strip() or None

        campaign.reward_text = parsed_data.get("reward_text")
        campaign.reward_type = parsed_data.get("reward_type")
        campaign.reward_value = parsed_data.get("reward_value")
        
        campaign.ai_marketing_text = f"📱 Katılım: {parsed_data.get('participation', 'Otomatik')}"

        try:
            self.db.flush()
            
            # Brands
            brands_list = parsed_data.get("brands", [])
            if isinstance(brands_list, list):
                self.db.query(CampaignBrand).filter_by(campaign_id=campaign.id).delete()
                for brand_name in brands_list:
                    if not brand_name: continue
                    brand_obj = self._get_or_create_brand(brand_name)
                    cb = CampaignBrand(campaign_id=campaign.id, brand_id=brand_obj.id)
                    self.db.merge(cb)
                    
            self.db.commit()
            print(f"      ✅ Saved: {title}")
            return is_new, True
        except Exception as e:
            self.db.rollback()
            print(f"      ❌ DB Error: {e}")
            return False, False

    # --- HELPERS ---
    def _load_cache(self):
        bank = self.db.query(Bank).filter(Bank.slug.in_(['kuveyt-turk', 'kuveytturk'])).first()
        if not bank:
            bank = Bank(name="Kuveyt Türk", slug="kuveyt-turk", is_active=True)
            self.db.add(bank)
            self.db.commit()
        self.bank_cache = bank
        
        for s in self.db.query(Sector).all():
            self.sector_cache[s.slug] = s

    def _get_or_create_card(self, name: str) -> Card:
        key = name.lower()
        if key in self.card_cache: return self.card_cache[key]
        card = self.db.query(Card).filter(Card.bank_id == self.bank_cache.id, Card.name == name).first()
        if not card:
            card = Card(bank_id=self.bank_cache.id, name=name, slug=self._generate_slug(name), is_active=True)
            self.db.add(card)
            self.db.flush()
        self.card_cache[key] = card
        return card

    def _get_or_create_brand(self, name: str) -> Brand:
        slug = self._generate_slug(name)
        if slug in self.brand_cache: return self.brand_cache[slug]
        brand = self.db.query(Brand).filter((Brand.slug == slug) | (Brand.name == name)).first()
        if not brand:
            brand = Brand(name=name, slug=slug, is_active=True)
            self.db.add(brand)
            self.db.flush()
        self.brand_cache[slug] = brand
        return brand

    def _get_sector_id(self, slug: str) -> Optional[int]:
        if slug in self.sector_cache: return self.sector_cache[slug].id
        return self.sector_cache.get("diger", {}).get("id")

    def _parse_date_string(self, date_str: Optional[str]) -> Optional[datetime.date]:
        if not date_str or date_str == "None": return None
        try:
            return datetime.strptime(date_str.split('T')[0], "%Y-%m-%d").date()
        except: return None

    def _clean(self, text: str) -> str:
        return re.sub(r'\s+', ' ', text.replace('\xa0', ' ')).strip()

    def _generate_slug(self, title: str) -> str:
        slug = str(title).lower().replace('ı', 'i').replace('ğ', 'g').replace('ü', 'u').replace('ş', 's').replace('ö', 'o').replace('ç', 'c')
        slug = re.sub(r'[^a-z0-9\s-]', '', slug)
        return re.sub(r'[\s-]+', '-', slug).strip('-')

    def disable_expired_campaigns(self, expired_urls: List[str]):
        if not expired_urls: return
        count = 0
        for url in expired_urls:
            camp = self.db.query(Campaign).filter_by(tracking_url=url, is_active=True).first()
            if camp:
                camp.is_active = False
                count += 1
        if count: self.db.commit()

if __name__ == "__main__":
    limit_arg = int(sys.argv[1]) if len(sys.argv) > 1 else 999
    scraper = KuveytTurkScraper(headless=True)
    scraper.run(limit=limit_arg)
