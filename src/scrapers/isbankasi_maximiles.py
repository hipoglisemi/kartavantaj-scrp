"""
İşbankası Maximiles Scraper
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


class IsbankMaximilesScraper:
    """İşbankası Maximiles card campaign scraper - Playwright based"""

    BASE_URL = "https://www.maximiles.com.tr"
    CAMPAIGNS_URL = "https://www.maximiles.com.tr/kampanyalar"
    BANK_NAME = "İşbankası"
    CARD_NAME = "Maximiles"

    def __init__(self):
        self.page = None
        self.browser = None
        self.playwright = None
        self.card_id = None
        self._init_card()

    def _init_card(self):
        """Get Maximiles card ID from database"""
        with get_db_session() as db:
            card = db.query(Card).join(Bank).filter(
                Bank.slug == "isbankasi",
                Card.slug == "maximiles"
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
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
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

        # Scroll / load more
        scroll_count = 0
        while True:
            try:
                soup = BeautifulSoup(self.page.content(), "html.parser")
                count = len([
                    a for a in soup.find_all("a", href=True)
                    if "/kampanyalar/" in a["href"]
                    and "arsiv" not in a["href"]
                    and not a["href"].endswith("-kampanyalari")
                    and "tum-kampanyalar" not in a["href"]
                ])

                if limit and count >= limit:
                    print(f"   ✅ Reached limit ({count} >= {limit}), stopping.")
                    break

                # Try "Daha Fazla" button
                btn = self.page.query_selector("button:has-text('Daha Fazla'), a.CampAllShow")
                if btn and btn.is_visible():
                    btn.scroll_into_view_if_needed()
                    time.sleep(1)
                    btn.click()
                    time.sleep(3)
                    scroll_count += 1
                    print(f"   ⏬ Clicked 'Load More' (Scroll {scroll_count})...")
                else:
                    # Fallback: scroll to bottom and check if new content loaded
                    prev_count = count
                    self.page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                    time.sleep(3)
                    new_soup = BeautifulSoup(self.page.content(), "html.parser")
                    new_count = len([
                        a for a in new_soup.find_all("a", href=True)
                        if "/kampanyalar/" in a["href"]
                    ])
                    if new_count <= prev_count:
                        print("   ℹ️ No more campaigns loading.")
                        break
                    scroll_count += 1
                    print(f"   ⏬ Scrolled to bottom (Scroll {scroll_count})...")

                if scroll_count > 30:
                    break
            except Exception as e:
                print(f"   ⚠️ Scroll error: {e}")
                break

        soup = BeautifulSoup(self.page.content(), "html.parser")

        excluded_slugs = ["gecmis-kampanyalar", "arsiv", "kampanyalar-arsivi"]

        all_links = []
        for a in soup.find_all("a", href=True):
            href = a["href"]
            if (
                "/kampanyalar/" in href
                and "arsiv" not in href
                and not href.endswith("-kampanyalari")
                and "tum-kampanyalar" not in href
                and not any(ex in href for ex in excluded_slugs)
                and len(href) > 20
            ):
                full_url = urljoin(self.BASE_URL, href)
                all_links.append(full_url)

        unique_urls = list(dict.fromkeys(all_links))
        if limit:
            unique_urls = unique_urls[:limit]

        print(f"✅ Found {len(unique_urls)} campaigns")
        return unique_urls

    def _extract_campaign_data(self, url: str) -> Optional[Dict[str, Any]]:
        """Extract campaign data from detail page."""
        try:
            self.page.goto(url, wait_until="domcontentloaded", timeout=60000)
            self.page.evaluate("window.scrollTo(0, 500)")
            time.sleep(1.5)

            try:
                self.page.wait_for_selector("h1", timeout=5000)
            except Exception:
                pass

            soup = BeautifulSoup(self.page.content(), "html.parser")

            title_el = soup.select_one("h1")
            title = self._clean_text(title_el.text) if title_el else "Başlık Yok"

            if "gecmis" in url or "#gecmis" in url:
                return None
            if "geçmiş" in title.lower() or "süresi doldu" in title.lower():
                return None

            # Image (Background Image)
            image_url = None
            try:
                banner_section = soup.select_one("section.campaign-banner")
                if banner_section and "style" in banner_section.attrs:
                    match = re.search(r"url\(['\"]?(.*?)['\"]?\)", banner_section["style"])
                    if match:
                        image_url = urljoin(self.BASE_URL, match.group(1))
                if not image_url:
                    img_el = soup.select_one(".campaign-detail-header img, section img")
                    if img_el and img_el.get("src") and "logo" not in img_el["src"]:
                        image_url = urljoin(self.BASE_URL, img_el["src"])
            except Exception as e:
                print(f"   ⚠️ Image extraction error: {e}")

            # Date
            date_text = ""
            date_label = soup.find(string=re.compile(r"Başlangıç - Bitiş Tarihi"))
            if date_label:
                parent = date_label.parent
                if parent:
                    for sib in parent.next_siblings:
                        if sib.name and sib.get_text(strip=True):
                            date_text = self._clean_text(sib.get_text())
                            break
                    if not date_text:
                        date_text = self._clean_text(
                            parent.get_text().replace(str(date_label), "")
                        )
            if not date_text:
                for c in [soup.select_one(".campaign-date"), soup.find("div", class_="date")]:
                    if c:
                        date_text = self._clean_text(c.text)
                        break

            # Participation & Conditions
            participation_text = ""
            conditions = []
            full_text = ""

            content_divs = soup.select("section div.container div.row div")
            main_content_div = None
            max_len = 0
            for div in content_divs:
                text_len = len(div.get_text(strip=True))
                if "Başlangıç - Bitiş Tarihi" not in div.get_text() and text_len > max_len:
                    max_len = text_len
                    main_content_div = div

            if main_content_div:
                part_label = main_content_div.find(
                    string=re.compile(r"Katılım Şekli|Katılmak için", re.I)
                )
                if part_label:
                    parent_p = part_label.find_parent("p")
                    participation_text = self._clean_text(parent_p.get_text()) if parent_p else ""

                for a in main_content_div.find_all("a"):
                    if "tıklayınız" in a.get_text():
                        a.decompose()

                raw_text = main_content_div.get_text("\n")
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

            unwanted = [
                "Maximiles", "Maximiles Black", "MercedesCard",
                "Kampanyalar", "Kart Başvurusu Yap", "Giriş Yap",
            ]
            conditions = [
                c for c in conditions
                if c not in unwanted and not c.startswith("Copyright")
            ]

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
        """Parse Turkish date format to YYYY-MM-DD"""
        if not date_text:
            return None
        text = date_text.replace("İ", "i").lower().strip()
        months = {
            "ocak": "01", "şubat": "02", "mart": "03", "nisan": "04",
            "mayıs": "05", "haziran": "06", "temmuz": "07", "ağustos": "08",
            "eylül": "09", "ekim": "10", "kasım": "11", "aralık": "12",
        }
        try:
            # DD.MM.YYYY - DD.MM.YYYY
            numeric_range = r"(\d{1,2})[./-](\d{1,2})[./-](\d{4})\s*-\s*(\d{1,2})[./-](\d{1,2})[./-](\d{4})"
            match = re.search(numeric_range, text)
            if match:
                d1, m1, y1, d2, m2, y2 = match.groups()
                if is_end:
                    return f"{y2}-{m2.zfill(2)}-{d2.zfill(2)}"
                return f"{y1}-{m1.zfill(2)}-{d1.zfill(2)}"

            # DD Month - DD Month YYYY
            text_range = r"(\d{1,2})\s*([a-zğüşıöç]+)?\s*-\s*(\d{1,2})\s*([a-zğüşıöç]+)\s*(\d{4})"
            match = re.search(text_range, text)
            if match:
                day1, month1, day2, month2, year = match.groups()
                if not month1:
                    month1 = month2
                m1n, m2n = months.get(month1), months.get(month2)
                if m1n and m2n:
                    if is_end:
                        return f"{year}-{m2n}-{str(day2).zfill(2)}"
                    return f"{year}-{m1n}-{str(day1).zfill(2)}"

            # Single numeric DD.MM.YYYY
            single = r"(\d{1,2})[./-](\d{1,2})[./-](\d{4})"
            match = re.search(single, text)
            if match:
                d, m, y = match.groups()
                return f"{y}-{m.zfill(2)}-{d.zfill(2)}"
        except Exception as e:
            print(f"   ⚠️ Date parsing error: {e}")
        return None

    def _clean_text(self, text: str) -> str:
        if not text:
            return ""
        text = text.replace("\n", " ").replace("\r", "")
        text = re.sub(r"\s+", " ", text).strip()
        return text

    def _process_campaign(self, url: str):
        """Process a single campaign"""
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
            print("   ⏭️  Skipped")
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
                    from src.models import Brand, CampaignBrand

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
            print("🚀 Starting İşbankası Maximiles Scraper (Playwright mode)...")
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
    scraper = IsbankMaximilesScraper()
    scraper.run()
