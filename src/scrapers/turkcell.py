import asyncio  # type: ignore # pyre-ignore[21]
import random  # type: ignore # pyre-ignore[21]
import time  # type: ignore # pyre-ignore[21]
import os
import re  # type: ignore # pyre-ignore[21]
import uuid  # type: ignore # pyre-ignore[21]
import sys
from typing import List, Dict, Any, Optional  # type: ignore # pyre-ignore[21]
from datetime import datetime  # type: ignore # pyre-ignore[21]
from decimal import Decimal  # type: ignore # pyre-ignore[21]
from urllib.parse import urljoin  # type: ignore # pyre-ignore[21]

import sys
import os
# Path setup to ensure imports work correctly
project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from playwright.async_api import async_playwright, Page  # type: ignore # pyre-ignore[21]
from bs4 import BeautifulSoup  # type: ignore # pyre-ignore[21]
from sqlalchemy.orm import Session  # type: ignore # pyre-ignore[21]

from src.database import get_db_session  # type: ignore # pyre-ignore[21]
from src.models import Bank, Card, Sector, Brand, Campaign, CampaignBrand  # type: ignore # pyre-ignore[21]
from src.services.ai_parser import parse_api_campaign  # type: ignore # pyre-ignore[21]
from src.utils.logger_utils import log_scraper_execution  # type: ignore # pyre-ignore[21]
from src.services.brand_normalizer import cleanup_brands  # type: ignore # pyre-ignore[21]
from src.utils.slug_generator import get_unique_slug  # type: ignore # pyre-ignore[21]

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
        
        # Initialize bank and card
        with get_db_session() as db:
            bank = db.query(Bank).filter(Bank.slug == "turkcell").first()  # type: ignore # pyre-ignore[16]
            if not bank:
                bank = Bank(name="Turkcell", slug="turkcell", is_active=True, logo_url="https://upload.wikimedia.org/wikipedia/en/thumb/5/53/Turkcell_logo.svg/1200px-Turkcell_logo.svg.png")
                db.add(bank)  # type: ignore # pyre-ignore[16]
                db.commit()  # type: ignore # pyre-ignore[16]
                db.refresh(bank)
            self.bank_id = bank.id  # type: ignore # pyre-ignore[16]
            
            card = db.query(Card).filter(Card.bank_id == bank.id, Card.slug == "turkcell").first()  # type: ignore # pyre-ignore[16]
            if not card:
                card = Card(
                    bank_id=bank.id,  # type: ignore # pyre-ignore[16]
                    name="Turkcell",
                    slug="turkcell",
                    is_active=True,
                    logo_url="https://upload.wikimedia.org/wikipedia/en/thumb/5/53/Turkcell_logo.svg/1200px-Turkcell_logo.svg.png"
                )
                db.add(card)  # type: ignore # pyre-ignore[16]
                db.commit()  # type: ignore # pyre-ignore[16]
                db.refresh(card)
            self.card_id = card.id  # type: ignore # pyre-ignore[16]

    def run(self):
        asyncio.run(self._run_async())

    async def _run_async(self):
        print(f"🚀 Starting Turkcell Scraper...")
        
        success_count: int = 0
        failed_count: int = 0
        total_found: int = 0
        error_details: List[Dict[str, Any]] = []  # type: ignore # pyre-ignore[16,6]

        try:
            async with async_playwright() as p:
                browser = await p.chromium.launch(
                    headless=self.headless,
                    args=["--no-sandbox", "--disable-dev-shm-usage"]
                )
                context = await browser.new_context(
                    user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                    viewport={'width': 1280, 'height': 800}
                )
                
                page = await context.new_page()
                links = await self._scrape_list(page)
                await page.close()  # type: ignore # pyre-ignore[16]
                total_found = len(links)
                print(f"   Found {total_found} campaigns in total.")
                
                if links and self.max_campaigns:
                    links = cast(List[str], links)[:self.max_campaigns]  # type: ignore # pyre-ignore[16,6]
                
                for i, url in enumerate(links, 1):
                    try:
                        res = await self._scrape_detail(context, url)
                        if res == "saved":
                            success_count += 1  # type: ignore # pyre-ignore[58]
                        elif res == "skipped":
                            pass
                        else:
                            failed_count += 1  # type: ignore # pyre-ignore[58]
                        await asyncio.sleep(random.uniform(1, 2))
                    except Exception as e:
                        print(f"      ❌ Error processing {url}: {e}")
                        failed_count += 1  # type: ignore # pyre-ignore[58]
                        error_details.append({"url": url, "error": str(e)})
                
                await browser.close()  # type: ignore # pyre-ignore[16]
                
            print(f"\n✅ Scraping complete! Saved {success_count} campaigns.")

            # Log execution
            status = "SUCCESS" if failed_count == 0 else ("PARTIAL" if success_count > 0 else "FAILED")  # type: ignore # pyre-ignore[58]
            with get_db_session() as db:
                log_scraper_execution(
                    db=db,
                    scraper_name="turkcell",
                    status=status,
                    total_found=total_found,
                    total_saved=success_count,
                    total_skipped=total_found - success_count - failed_count,
                    total_failed=failed_count,
                    error_details={"errors": error_details} if error_details else None
                )

        except Exception as e:
            print(f"❌ Fatal error in Turkcell scraper: {e}")
            import traceback  # type: ignore # pyre-ignore[21]
            traceback.print_exc()

    async def _scrape_list(self, page: Page) -> List[str]:  # type: ignore # pyre-ignore[16,6]
        print(f"   🌐 Loading listing page: {self.LISTING_URL}")
        try:
            await page.goto(self.LISTING_URL, wait_until="networkidle", timeout=90000)
            
            # Lazy loading
            last_height = await page.evaluate("document.body.scrollHeight")
            for i in range(5):
                await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                await asyncio.sleep(2)
                new_height = await page.evaluate("document.body.scrollHeight")
                if new_height == last_height:
                    break
                last_height = new_height
                print(f"      🔃 Scrolled ({i+1})...")

            elements = await page.query_selector_all('a:has(h4[class*="title"])')  # type: ignore # pyre-ignore[16,6]
            links = []
            for el in elements:
                href = await el.get_attribute('href')
                if href:
                    full_url = urljoin(self.BASE_URL, href)
                    if full_url not in links:
                        links.append(full_url)
            
            return links  # type: ignore # pyre-ignore[7]
        except Exception as e:
            print(f"   ❌ List extraction failed: {e}")
            return []  # type: ignore # pyre-ignore[7]

    async def _scrape_detail(self, context, url: str) -> str:
        with get_db_session() as db:
            existing = db.query(Campaign).filter(Campaign.tracking_url == url).first()  # type: ignore # pyre-ignore[16]
            if existing:
                return "skipped"  # type: ignore # pyre-ignore[7]

        page = await context.new_page()
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=45000)
            await asyncio.sleep(1)
            
            title = await page.inner_text("h1") if await page.query_selector("h1") else "Turkcell Kampanyası"
            
            image_url = await page.evaluate('''() => {
                const img = document.querySelector('.Detail_detail__image__omC5p img, [class*="Detail_detail__image"] img');
                return img ? img.src : null;  # type: ignore # pyre-ignore[7]
            }''')
            
            headers = await page.query_selector_all('div.ant-collapse-header')
            content_parts: List[str] = []  # type: ignore # pyre-ignore[16,6]
            participation_text: str = ""
            
            for header in headers:
                try:
                    header_text = (await header.inner_text()).strip()
                    if not header_text: continue
                    
                    await page.evaluate('(h) => h.click()', header)  # type: ignore # pyre-ignore
                    await asyncio.sleep(0.5)
                    
                    text = await page.evaluate('''(header) => {  # type: ignore # pyre-ignore
                        const item = header.closest('.ant-collapse-item');
                        return item && item.querySelector('.ant-collapse-content') ? item.querySelector('.ant-collapse-content').innerText : "";  # type: ignore # pyre-ignore[7]
                    }''', header)
                    
                    if text.strip():
                        content_parts.append(f"### {header_text}\n{text}")
                        if any(x in header_text.lower() for x in ["katılım", "faydalan", "nasıl"]):  # type: ignore # pyre-ignore[16,6]
                            participation_text += f"\n{text}"  # type: ignore # pyre-ignore[58]
                except:
                    pass

            raw_text = "\n\n".join(content_parts)
            
            ai_data = parse_api_campaign(
                title=title,
                short_description=title,
                content_html=raw_text,
                bank_name="Turkcell"
            )
            
            if participation_text:
                ai_data['participation'] = participation_text.strip()[:1000]  # type: ignore # pyre-ignore[16,6]

            return self._save_campaign(ai_data, url, image_url)  # type: ignore # pyre-ignore[7]
            
        except Exception as e:
            print(f"      ❌ Detail error: {e}")
            return "error"  # type: ignore # pyre-ignore[7]
        finally:
            await page.close()  # type: ignore # pyre-ignore[16]
            
        return "error"  # type: ignore # pyre-ignore[7]

    def _save_campaign(self, ai_data: Dict[str, Any], url: str, image_url: Optional[str]) -> str:  # type: ignore # pyre-ignore[16,6]
        try:
            with get_db_session() as db:
                # Map sector
                sector_name = ai_data.get('sector', 'Diğer')
                sector = db.query(Sector).filter((Sector.slug == sector_name) | (Sector.name.ilike(sector_name))).first()  # type: ignore # pyre-ignore[16]
                if not sector:
                    sector = db.query(Sector).filter(Sector.slug == 'diger').first()  # type: ignore # pyre-ignore[16]
                sector_id = sector.id if sector else None  # type: ignore # pyre-ignore[16]

                slug = get_unique_slug(ai_data.get('short_title') or ai_data.get('title'), db, Campaign)

                campaign = Campaign(
                    card_id=self.card_id,
                    sector_id=sector_id,
                    title=ai_data.get("short_title") or ai_data.get("title"),
                    slug=slug,
                    description=ai_data.get("description"),
                    conditions="\n".join(ai_data.get("conditions", [])),
                    reward_text=ai_data.get("reward_text", "Fırsatı Kaçırmayın"),
                    reward_value=ai_data.get("reward_value"),
                    reward_type=ai_data.get("reward_type"),
                    start_date=ai_data.get("start_date"),
                    end_date=ai_data.get("end_date"),
                    image_url=image_url or ai_data.get("image_url") or "https://www.turkcell.com.tr/assets/img/turkcell-logo.png",
                    tracking_url=url,
                    is_active=True,
                    ai_marketing_text=ai_data.get("marketing_text"),
                    clean_text=ai_data.get("_clean_text")
                )
                
                db.add(campaign)  # type: ignore # pyre-ignore[16]
                db.commit()  # type: ignore # pyre-ignore[16]

                # Brands
                if ai_data.get('brands'):
                    clean_brands = cleanup_brands(ai_data.get('brands'))
                    for b_name in clean_brands:
                        brand = db.query(Brand).filter(Brand.name == b_name).first()  # type: ignore # pyre-ignore[16]
                        if not brand:
                            brand = Brand(name=b_name, slug=get_unique_slug(b_name, db, Brand), is_active=True)
                            db.add(brand)  # type: ignore # pyre-ignore[16]
                            db.commit()  # type: ignore # pyre-ignore[16]
                        
                        link = db.query(CampaignBrand).filter_by(campaign_id=campaign.id, brand_id=brand.id).first()  # type: ignore # pyre-ignore[16]
                        if not link:
                            db.add(CampaignBrand(campaign_id=campaign.id, brand_id=brand.id))  # type: ignore # pyre-ignore[16]
                            db.commit()  # type: ignore # pyre-ignore[16]

            return "saved"  # type: ignore # pyre-ignore[7]
        except Exception as e:
            print(f"      ❌ DB Save Error: {e}")
            return "error"  # type: ignore # pyre-ignore[7]

if __name__ == "__main__":
    max_c = 5 if os.environ.get('TEST_MODE') == '1' else 20
    scraper = TurkcellScraper(max_campaigns=max_c, headless=True)
    scraper.run()
