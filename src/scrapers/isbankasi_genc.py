"""
İşbankası Maximum Genç Scraper
Powered by Playwright (GitHub Actions compatible, Cloudflare-resistant)
"""

import os
import time
import re
from datetime import datetime
from typing import Optional, Dict, Any, List
from urllib.parse import urljoin
from bs4 import BeautifulSoup

from src.database import get_db_session
from src.models import Campaign, Card, Sector, Bank
from src.services.ai_parser import parse_api_campaign
from src.utils.slug_generator import generate_slug


class IsbankMaximumGencScraper:
    """İşbankası Maximum Genç card campaign scraper - Playwright based"""

    BASE_URL = "https://www.maximumgenc.com.tr"
    CAMPAIGNS_URL = "https://www.maximumgenc.com.tr/kampanyalar"
    BANK_NAME = "İşbankası"
    CARD_NAME = "Maximum Genç"
    DEFAULT_IMAGE_URL = "https://www.maximumgenc.com.tr/_assets/images/logo.png"

    def __init__(self):
        self.page = None
        self.browser = None
        self.playwright = None
        self.card_id = None
        self._init_card()

    def _init_card(self):
        """Get Maximum Genç card ID from database"""
        with get_db_session() as db:
            card = db.query(Card).join(Bank).filter(
                Bank.slug == "isbankasi",
                Card.slug == "maximum-genc"
            ).first()

            if not card:
                raise ValueError(
                    f"Card '{self.CARD_NAME}' from '{self.BANK_NAME}' not found in database. Run seed_sectors.py first."
                )

            self.card_id = card.id
            print(f"✅ Found card: {self.BANK_NAME} {self.CARD_NAME} (ID: {self.card_id})")

    def _start_browser(self):
        """Launch Playwright browser"""
        from playwright.sync_api import sync_playwright
        self.playwright = sync_playwright().start()
        self.browser = self.playwright.chromium.launch(
            headless=True,
            args=[
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--disable-dev-shm-usage",
                "--disable-gpu",
                "--window-size=1920,1080",
            ]
        )
        context = self.browser.new_context(
            viewport={"width": 1920, "height": 1080},
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        )
        self.page = context.new_page()
        print("✅ Playwright browser started.")

    def _stop_browser(self):
        """Cleanup Playwright"""
        try:
            if self.browser:
                self.browser.close()
            if self.playwright:
                self.playwright.stop()
        except Exception:
            pass

    def _fetch_campaign_urls(self, limit: Optional[int] = None) -> List[str]:
        """Fetch all campaign URLs from the listing page."""
        print(f"📥 Fetching campaign list from {self.CAMPAIGNS_URL}...")

        self.page.goto(self.CAMPAIGNS_URL, wait_until="networkidle", timeout=60000)
        time.sleep(5)

        scroll_count = 0
        max_scrolls = 100

        while scroll_count < max_scrolls:
            if limit and limit > 0:
                try:
                    soup = BeautifulSoup(self.page.content(), "html.parser")
                    count = len([
                        a for a in soup.find_all("a", href=True)
                        if "/kampanyalar/" in a["href"]
                        and "gecmis" not in a["href"]
                    ])
                    if count >= limit:
                        print(f"   ✅ Reached limit ({count} >= {limit}), stopping.")
                        break
                except Exception:
                    pass

            try:
                self.page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                time.sleep(1)

                # Try load more button
                btn = self.page.query_selector(".show-more-opportunity")
                if btn and btn.is_visible():
                    btn.scroll_into_view_if_needed()
                    time.sleep(1)
                    try:
                        btn.click()
                    except Exception:
                        self.page.evaluate(
                            "arguments => arguments[0].click()",
                            [btn]
                        )
                    time.sleep(3)
                    scroll_count += 1
                    print(f"   ⏬ Loaded more campaigns (Scroll {scroll_count})...")
                else:
                    print("   ℹ️ No more 'Load More' button (End of list).")
                    break
            except Exception as e:
                print(f"   ℹ️ Load more button not found (End of list?): {e}")
                break

        soup = BeautifulSoup(self.page.content(), "html.parser")

        excluded_keywords = ["gecmis", "arsiv", "tum-kampanyalar"]

        all_links = []
        found_count = 0
        skipped_count = 0

        cards = soup.select(".opportunity-result a")
        print(f"   ℹ️ Found {len(cards)} campaign cards in DOM")

        for a in cards:
            href = a.get("href")
            if not href:
                continue
            is_excluded = any(ex in href for ex in excluded_keywords) or "javascript" in href
            if is_excluded:
                skipped_count += 1
            else:
                all_links.append(urljoin(self.BASE_URL, href))
                found_count += 1

        if not all_links:
            print("   ⚠️ No cards found with .opportunity-result, trying broad search...")
            for a in soup.find_all("a", href=True):
                href = a["href"]
                if "/kampanyalar/" in href and not any(ex in href for ex in excluded_keywords):
                    all_links.append(urljoin(self.BASE_URL, href))

        unique_urls = list(dict.fromkeys(all_links))
        print(f"   ℹ️ Links extracted: {len(unique_urls)}, Skipped: {skipped_count}")

        if limit and limit > 0:
            unique_urls = unique_urls[:limit]

        print(f"✅ Found {len(unique_urls)} unique campaigns to process")
        return unique_urls

    def _extract_campaign_data(self, url: str) -> Optional[Dict[str, Any]]:
        """Extract campaign data from detail page."""
        try:
            self.page.goto(url, wait_until="domcontentloaded", timeout=60000)
            self.page.evaluate("window.scrollTo(0, 500)")
            time.sleep(2)

            try:
                self.page.wait_for_selector("h1", timeout=8000)
            except Exception:
                print("   ⚠️ Page load timeout/error")

            soup = BeautifulSoup(self.page.content(), "html.parser")

            title_el = soup.select_one("h1")
            title = self._clean_text(title_el.text) if title_el else "Başlık Yok"

            if (
                "gecmis" in url or "#gecmis" in url
                or "geçmiş" in title.lower()
                or "süresi doldu" in title.lower()
            ):
                return None

            # Image
            image_url = None
            try:
                img_el = soup.select_one(".detail-img img") or soup.select_one("section img")
                if img_el:
                    src = img_el.get("data-original") or img_el.get("data-src") or img_el.get("src")
                    if src and not src.startswith("data:"):
                        image_url = urljoin(self.BASE_URL, src)
                if not image_url:
                    banner = soup.select_one("section.banner, div.banner")
                    if banner and "style" in banner.attrs:
                        match = re.search(r"url\(['\"]?(.*?)['\"]?\)", banner["style"])
                        if match:
                            image_url = urljoin(self.BASE_URL, match.group(1))
            except Exception as e:
                print(f"   ⚠️ Image extraction error: {e}")

            if not image_url:
                print("   ⚠️ No image found, using default fallback.")
                image_url = self.DEFAULT_IMAGE_URL

            # Date
            date_text = ""
            date_el = soup.select_one(".date, .campaign-date")
            if date_el:
                date_text = self._clean_text(date_el.text)
            if not date_text:
                full_page_text = soup.get_text()
                date_match = re.search(
                    r"(\d{1,2}\s+[a-zA-ZğüşıöçĞÜŞİÖÇ]+\s+\d{4})\s*-\s*(\d{1,2}\s+[a-zA-ZğüşıöçĞÜŞİÖÇ]+\s+\d{4})",
                    full_page_text,
                )
                if date_match:
                    date_text = date_match.group(0)

            # Participation & Conditions
            participation_text = ""
            conditions = []
            full_text = ""

            content_div = soup.select_one(".detail-text, .campaign-content, section .container")
            if content_div:
                part_label = content_div.find(
                    string=re.compile(r"Katılım Şekli|Katılmak için", re.I)
                )
                if part_label:
                    parent = part_label.find_parent("p") or part_label.find_parent("div")
                    participation_text = self._clean_text(parent.get_text()) if parent else ""

                raw_text = content_div.get_text("\n")
                conditions = [
                    self._clean_text(line)
                    for line in raw_text.split("\n")
                    if len(self._clean_text(line)) > 20
                ]
                full_text = " ".join(conditions)
            else:
                full_text = self._clean_text(soup.get_text())[:1000]

            if participation_text:
                full_text += f"\nKATILIM ŞEKLİ: {participation_text}"

            conditions = [c for c in conditions if not c.startswith("Copyright")]

            return {
                "title": title,
                "image_url": image_url,
                "date_text": date_text,
                "full_text": full_text,
                "conditions": conditions,
                "source_url": url,
                "participation": participation_text,
            }

        except Exception as e:
            print(f"   ⚠️ Error extracting {url}: {e}")
            return None

    def _parse_date(self, date_text: str, is_end: bool = False) -> Optional[str]:
        if not date_text:
            return None
        text = date_text.replace("İ", "i").lower()
        months = {
            "ocak": "01", "şubat": "02", "mart": "03", "nisan": "04",
            "mayıs": "05", "haziran": "06", "temmuz": "07", "ağustos": "08",
            "eylül": "09", "ekim": "10", "kasım": "11", "aralık": "12",
        }
        try:
            pattern = r"(\d{1,2})\s*([a-zğüşıöç]+)?\s*-\s*(\d{1,2})\s*([a-zğüşıöç]+)\s*(\d{4})"
            match = re.search(pattern, text)
            if match:
                day1, month1, day2, month2, year = match.groups()
                if not month1:
                    month1 = month2
                if is_end:
                    return f"{year}-{months.get(month2, '12')}-{str(day2).zfill(2)}"
                return f"{year}-{months.get(month1, '01')}-{str(day1).zfill(2)}"
        except Exception:
            pass
        return None

    def _clean_text(self, text: str) -> str:
        if not text:
            return ""
        text = text.replace("\n", " ").replace("\r", "")
        text = re.sub(r"\s+", " ", text).strip()
        return text

    def _process_campaign(self, url: str):
        try:
            with get_db_session() as db:
                exists = db.query(Campaign).filter(
                    Campaign.tracking_url == url,
                    Campaign.card_id == self.card_id,
                ).first()
                if exists:
                    print(f"   ⏭️  Skipped (Already exists): {url}")
                    return "skipped"
        except Exception as e:
            print(f"   ⚠️ URL check failed: {e}")

        print(f"🔍 Processing: {url}")
        data = self._extract_campaign_data(url)
        if not data:
            print("   ⏭️  Skipped (No data or closed window)")
            return "skipped"

        ai_result = parse_api_campaign(
            title=data["title"],
            short_description=data["full_text"][:500],
            content_html=data["full_text"],
            bank_name=self.BANK_NAME,
        )
        return self._save_campaign(
            data["title"], data["image_url"], data["date_text"], data["source_url"], ai_result
        )

    def _save_campaign(
        self,
        title: str,
        image_url: Optional[str],
        date_text: str,
        source_url: str,
        ai_data: Dict[str, Any],
    ):
        print(f"   💾 Saving campaign: {title[:30]}...")
        try:
            with get_db_session() as db:
                from src.models import Brand, CampaignBrand
                from src.utils.slug_generator import get_unique_slug

                slug = get_unique_slug(ai_data.get("short_title") or title, db, Campaign)

                sector_name = ai_data.get("sector", "Diğer")
                sector = db.query(Sector).filter(Sector.name == sector_name).first()
                if not sector:
                    sector = db.query(Sector).filter(Sector.slug == "diger").first()

                start_date = None
                if ai_data.get("start_date"):
                    try:
                        start_date = datetime.strptime(ai_data["start_date"], "%Y-%m-%d")
                    except Exception:
                        pass
                if not start_date:
                    sd = self._parse_date(date_text, is_end=False)
                    if sd:
                        try:
                            start_date = datetime.strptime(sd, "%Y-%m-%d")
                        except Exception:
                            pass
                if not start_date:
                    start_date = datetime.now()

                end_date = None
                if ai_data.get("end_date"):
                    try:
                        end_date = datetime.strptime(ai_data["end_date"], "%Y-%m-%d")
                    except Exception:
                        pass
                if not end_date:
                    ed = self._parse_date(date_text, is_end=True)
                    if ed:
                        try:
                            end_date = datetime.strptime(ed, "%Y-%m-%d")
                        except Exception:
                            pass

                conditions_lines = []
                participation = ai_data.get("participation")
                if participation and participation != "Detayları İnceleyin":
                    conditions_lines.append(f"KATILIM: {participation}")
                if ai_data.get("conditions"):
                    conditions_lines.extend(ai_data.get("conditions"))
                conditions_text = "\n".join(conditions_lines)

                eligible_cards_list = ai_data.get("cards", [])
                eligible_cards_str = ", ".join(eligible_cards_list) if eligible_cards_list else None

                campaign = Campaign(
                    card_id=self.card_id,
                    sector_id=sector.id if sector else None,
                    slug=slug,
                    title=ai_data.get("short_title") or title,
                    description=ai_data.get("description") or title[:200],
                    reward_text=ai_data.get("reward_text"),
                    reward_value=ai_data.get("reward_value"),
                    reward_type=ai_data.get("reward_type"),
                    conditions=conditions_text,
                    eligible_cards=eligible_cards_str,
                    image_url=image_url,
                    start_date=start_date,
                    end_date=end_date,
                    is_active=True,
                    created_at=datetime.utcnow(),
                    updated_at=datetime.utcnow(),
                    tracking_url=source_url,
                )
                db.add(campaign)
                print("   📝 Added to session...")

                if ai_data.get("brands"):
                    for brand_name in ai_data["brands"]:
                        clean_name = brand_name.strip()
                        if not clean_name:
                            continue
                        brand_slug = generate_slug(clean_name)
                        brand = db.query(Brand).filter(Brand.slug == brand_slug).first()
                        if not brand:
                            brand = db.query(Brand).filter(Brand.name == clean_name).first()
                        if not brand:
                            brand = Brand(
                                name=clean_name,
                                slug=brand_slug,
                                is_active=True,
                                aliases=[clean_name],
                            )
                            db.add(brand)
                            db.flush()
                            print(f"      ✨ Created new brand: {clean_name}")
                        else:
                            print(f"      ✓ Brand exists: {clean_name}")

                        db.flush()
                        existing_link = db.query(CampaignBrand).filter(
                            CampaignBrand.campaign_id == campaign.id,
                            CampaignBrand.brand_id == brand.id,
                        ).first()
                        if not existing_link:
                            link = CampaignBrand(campaign_id=campaign.id, brand_id=brand.id)
                            db.add(link)
                            print(f"      🔗 Linked brand: {clean_name}")

                db.commit()
                print(f"   ✅ Saved: {campaign.title} (ID: {campaign.id})")
                return "saved"
        except Exception as e:
            print(f"   ❌ Save Failed: {e}")
            import traceback
            traceback.print_exc()
            return "error"

    def run(self, limit: Optional[int] = None):
        """Main scraper entry point."""
        try:
            print("🚀 Starting İşbankası Maximum Genç Scraper (Playwright mode)...")
            self._start_browser()

            urls = self._fetch_campaign_urls(limit=limit)

            success_count = 0
            skipped_count = 0
            failed_count = 0

            for i, url in enumerate(urls, 1):
                print(f"\n[{i}/{len(urls)}]")
                try:
                    res = self._process_campaign(url)
                    if res == "saved":
                        success_count += 1
                    elif res == "skipped":
                        skipped_count += 1
                    else:
                        failed_count += 1
                except Exception as e:
                    print(f"❌ Error processing {url}: {e}")
                    failed_count += 1

                time.sleep(1.5)

            print("\n🏁 Scraping finished.")
            print(
                f"✅ Özet: {len(urls)} bulundu, {success_count} eklendi, "
                f"{skipped_count + failed_count} atlandı/hata aldı."
            )
        except Exception as e:
            print(f"❌ Scraper error: {e}")
            raise
        finally:
            self._stop_browser()


if __name__ == "__main__":
    scraper = IsbankMaximumGencScraper()
    scraper.run()
