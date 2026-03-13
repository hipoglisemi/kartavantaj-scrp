# pyre-ignore-all-errors
# type: ignore

"""
İşbankası Maximiles Scraper
Powered by Playwright (GitHub Actions compatible, Cloudflare-resistant)
"""

import os
import sys
import time
import re
import uuid
import traceback
from datetime import datetime
from typing import Optional, Dict, Any, List
from urllib.parse import urljoin
from bs4 import BeautifulSoup

# Fix sys.path to ensure src is discoverable
current_dir = os.path.dirname(os.path.abspath(__file__))
# Check if we are in src/scrapers or root
if "src" in current_dir:
    project_root = os.path.dirname(os.path.dirname(current_dir))
else:
    project_root = current_dir

if project_root not in sys.path:
    sys.path.insert(0, project_root)

from dotenv import load_dotenv
load_dotenv(os.path.join(project_root, '.env'))

from sqlalchemy import create_engine, text, func
from sqlalchemy.orm import sessionmaker

# Import unified models and database session
from src.database import engine, get_db_session
from src.models import Bank, Card, Sector, Brand, Campaign, CampaignBrand

# AIParser is lazy-imported in __init__ to avoid google.generativeai hang
AIParser = None


SECTOR_MAP = {
    "Market & Gıda": "Market", "Giyim & Aksesuar": "Giyim",
    "Restoran & Kafe": "Restoran & Kafe", "Seyahat": "Seyahat",
    "Turizm & Konaklama": "Seyahat", "Elektronik": "Elektronik",
    "Mobilya & Dekorasyon": "Mobilya & Dekorasyon",
    "Kozmetik & Sağlık": "Kozmetik & Sağlık", "E-Ticaret": "E-Ticaret",
    "Otomotiv": "Otomotiv", "Sigorta": "Sigorta", "Eğitim": "Eğitim",
    "Diğer": "Diğer",
}


class IsbankMaximilesScraper:
    """İşbankası Maximiles scraper - Playwright based"""

    BASE_URL = "https://www.maximiles.com.tr"
    CAMPAIGNS_URL = "https://www.maximiles.com.tr/kampanyalar"
    BANK_NAME = "İşbankası"
    CARD_SLUG = "maximiles"

    def __init__(self):
        self.engine = engine
        self.db = get_db_session()
        
        # Lazy import of AIParser to avoid google.generativeai hanging at module import time
        try:
            from src.services.ai_parser import AIParser as _AIParser
            print("[DEBUG] AIParser lazy-imported via src.services")
        except ImportError:
            try:
                from services.ai_parser import AIParser as _AIParser
                print("[DEBUG] AIParser lazy-imported via services")
            except ImportError as e:
                print(f"[DEBUG] AIParser import FAILED: {e}")
                raise
        self.parser = _AIParser()
        print("[DEBUG] AIParser initialized")

        self.page = None
        self.browser = None
        self.playwright = None
        self.card_id = None
        self._init_card()

    def _init_card(self):
        bank = self.db.query(Bank).filter(
            Bank.slug.in_([
                'i-sbankasi',   # gerçek DB slug
                'isbank', 'isbankasi', 'is-bankasi', 'turkiye-is-bankasi',
            ])
        ).first()
        if not bank:
            bank = self.db.query(Bank).filter(
                Bank.name.ilike('%İş Bank%') | Bank.name.ilike('%İşbank%')
            ).first()
        if not bank:
            print(f"⚠️  İşbankası not found in DB, creating...")
            bank = Bank(name='İş Bankası', slug='isbank')
            self.db.add(bank)
            self.db.commit()
        print(f"✅ Bank: {bank.name} (ID: {bank.id}, slug: {bank.slug})")

        card = self.db.query(Card).filter(
            Card.slug.in_([
                'maximiles', 'maximiles-card', 'isbank-maximiles',
                'isbankasi-maximiles', 'maximilescard',
            ])
        ).first()
        if not card:
            card = self.db.query(Card).filter(
                Card.name.ilike('%Maximiles%'),
                Card.bank_id == bank.id
            ).first()
        if not card:
            print(f"⚠️  Card 'maximiles' not found, creating...")
            card = Card(bank_id=bank.id, name='Maximiles Card', slug='maximiles', is_active=True)
            self.db.add(card)
            self.db.commit()
        self.card_id = card.id
        print(f"✅ Card: {card.name} (ID: {self.card_id}, slug: {card.slug})")



    def _start_browser(self):
        from playwright.sync_api import sync_playwright
        self.playwright = sync_playwright().start()
        
        is_ci = os.getenv("GITHUB_ACTIONS") == "true" or os.getenv("CI") == "true"
        connected = False

        if not is_ci:
            try:
                print("   🔌 Attempting to connect to local Chrome debug instance at http://localhost:9222...")
                self.browser = self.playwright.chromium.connect_over_cdp("http://localhost:9222")
                connected = True
                print("   ✅ Connected to local existing Chrome instance")
                
                # Use existing context if available
                if len(self.browser.contexts) > 0:
                    context = self.browser.contexts[0]
                else:
                    context = self.browser.new_context()
                    
                self.page = context.new_page()
                self.page.set_default_timeout(120000)
                return
            except Exception as e:
                print(f"   ⚠️  Could not connect to debug Chrome, launching headless... ({e})")
                
        if not connected:
            self.browser = self.playwright.chromium.launch(
                headless=True,
                args=["--no-sandbox", "--disable-setuid-sandbox",
                      "--disable-dev-shm-usage", "--disable-gpu", "--window-size=1920,1080",
                      "--disable-blink-features=AutomationControlled",
                      "--disable-extensions", "--disable-web-security"]
            )
            context = self.browser.new_context(
                viewport={"width": 1920, "height": 1080},
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
                locale="tr-TR",
                timezone_id="Europe/Istanbul",
                extra_http_headers={"Accept-Language": "tr-TR,tr;q=0.9,en;q=0.8"}
            )
            context.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
            self.page = context.new_page()
            self.page.set_default_timeout(120000)
            print("✅ Playwright browser started.")

    def _stop_browser(self):
        try:
            if hasattr(self, 'browser') and self.browser:
                self.browser.close()
            if hasattr(self, 'playwright') and self.playwright:
                self.playwright.stop()
        except Exception:
            pass

    def _fetch_campaign_urls(self, limit: Optional[int] = None) -> tuple[List[str], List[str]]:
        print(f"📥 Fetching campaign list from {self.CAMPAIGNS_URL} ...")
        self.page.goto(self.CAMPAIGNS_URL, wait_until="domcontentloaded", timeout=120000)
        time.sleep(5)

        EXPIRED_MARKERS = ["sona ermiştir", "bitmiştir", "sona erdi", "süresi doldu", "kampanya sona"]

        prev_count = 0
        scroll_count = 0
        while scroll_count < 30:
            soup = BeautifulSoup(self.page.content(), "html.parser")
            valid = []
            for c in soup.select(".campaign-item, .col-xl-4, .card"):
                valid.extend(c.find_all("a", href=True))
            count = len([a for a in valid if "/kampanyalar/" in a.get("href", "") and "arsiv" not in a.get("href", "")])
            if limit is not None and isinstance(limit, int):
                if count >= limit:
                    break

            # ── Yeni ekleme: Son eklenen kampanyalar expired bölgesine girdi mi? ──
            try:
                # Check the most recently loaded cards (last 10-15)
                valid_items = []
                for c in soup.select(".campaign-item, .col-xl-4, .card"):
                    valid_items.extend(c.find_all("a", href=lambda href: href and "/kampanyalar/" in href))
                campaign_items = valid_items
                if len(campaign_items) > 10:
                    recent = list(campaign_items)[-10:]  # type: ignore
                    expired_count: int = 0
                    for a in recent:
                         parent = a.find_parent("div", class_="campaign-item") or a.find_parent("div", class_="col-xl-4") or a.find_parent("div", class_="card") or a.parent
                         parent_text = parent.get_text(separator=" ", strip=True).lower() if parent else ""
                         if any(m in parent_text for m in EXPIRED_MARKERS):
                             expired_count = int(expired_count or 0) + 1  # type: ignore
                    
                    if expired_count >= 3:  # type: ignore
                         print(f"   🛑 Reached expired campaigns section ({expired_count}/10 expired). Stopping scroll.")
                         break
            except Exception as e:
                pass
            # ─────────────────────────────────────────────────────────



            # Try load more button
            btn = None
            if self.page:
                btn = self.page.query_selector("button:has-text('Daha Fazla'), a.CampAllShow")
            
            if btn and btn.is_visible():
                btn.scroll_into_view_if_needed()
                time.sleep(1)
                btn.click()
                time.sleep(3)
                scroll_count += 1
                print(f"   ⏬ Clicked 'Load More' (round {scroll_count})...")
            else:
                # Scroll fallback
                if self.page:
                    self.page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                time.sleep(3)
                new_soup = BeautifulSoup(self.page.content(), "html.parser")
                valid_new = []
                for c in new_soup.select(".campaign-item, .col-xl-4, .card"):
                    valid_new.extend(c.find_all("a", href=True))
                new_count = len([a for a in valid_new if "/kampanyalar/" in a.get("href", "")])
                if new_count <= prev_count:
                    break
                prev_count = new_count
                scroll_count += 1
                print(f"   ⏬ Scrolled (round {scroll_count}) — {new_count} links found...")

        soup = BeautifulSoup(self.page.content(), "html.parser")
        
        excluded_suffixes = [
            "-kampanyalari",
            "-kampanyalar",
            "premium-kampanyalar",
            "tum-kampanyalar"
        ]
        
        excluded_paths = [
            "/kampanyalar/seyahat",
            "/kampanyalar/turizm",
            "/kampanyalar/akaryakit",
            "/kampanyalar/giyim-aksesuar",
            "/kampanyalar/market",
            "/kampanyalar/elektronik",
            "/kampanyalar/beyaz-esya",
            "/kampanyalar/mobilya-dekorasyon",
            "/kampanyalar/egitim-kirtasiye",
            "/kampanyalar/online-alisveris",
            "/kampanyalar/otomotiv",
            "/kampanyalar/vergi-odemeleri",
            "/kampanyalar/maximum-mobil",
            "/kampanyalar/diger",
            "/kampanyalar/yeme-icme",
            "/kampanyalar/maximum-pati-kart",
            "/kampanyalar/arac-kiralama",
            "/kampanyalar/bankamatik"
        ]

        all_links = []
        expired_links = []
        
        valid_a_tags = []
        for container in soup.select(".campaign-item, .col-xl-4, .card"):
            valid_a_tags.extend(container.find_all("a", href=True))
            
        for a in valid_a_tags:
            href = a.get("href", "").lower()
            
            if (
                ("/kampanyalar/" in href or "kampanyalar/" in href)
                and "arsiv" not in href
                and "gecmis" not in href
                and "past" not in href
            ):
                is_exact_category = any(href.endswith(path) for path in excluded_paths)
                is_category_suffix = any(href.endswith(suffix) for suffix in excluded_suffixes)
                is_common_page = "ozellikler" in href or "basvuru" in href or href.endswith("/kampanyalar")
                
                if not is_exact_category and not is_category_suffix and not is_common_page and len(href) > 25:
                    full_url = urljoin(self.BASE_URL, a["href"])
                    
                    parent = a.find_parent("div", class_="campaign-item") or a.find_parent("div", class_="col-xl-4") or a.find_parent("div", class_="card") or a.parent
                    parent_text = parent.get_text(separator=" ", strip=True).lower() if parent else ""

                    if "gecmis" in href or "geçmiş" in a.text.lower() or any(m in parent_text for m in EXPIRED_MARKERS):
                        expired_links.append(full_url)
                    else:
                        all_links.append(full_url)

        unique_urls = list(dict.fromkeys(all_links))
        unique_expired = list(dict.fromkeys(expired_links))
        if limit is not None:
            unique_urls = list(unique_urls)[:int(limit)]  # type: ignore
            
        print(f"✅ Found {len(unique_urls)} active campaigns, and {len(unique_expired)} expired campaigns")
        return unique_urls, unique_expired


    def _extract_campaign_data(self, url: str) -> Optional[Dict[str, Any]]:
        try:
            success = False
            for attempt in range(3):
                try:
                    if self.page:
                        self.page.goto(url, wait_until="domcontentloaded", timeout=60000)
                        success = True
                        break
                    else:
                        print("      ❌ self.page is None")
                        return None
                except Exception as e:
                    print(f"      ⚠️ Detail load attempt {attempt+1}/3 failed: {e}. Retrying...")
                    time.sleep(3 + attempt * 2)
            
            if not success:
                print(f"      ❌ Could not load detail page after 3 attempts: {url}")
                return None
                
            # Scroll to bottom to trigger lazy loading of content
            if self.page:
                self.page.evaluate("""async () => {
                    await new Promise((resolve) => {
                        let totalHeight = 0;
                        let distance = 300;
                        let timer = setInterval(() => {
                            let scrollHeight = document.body.scrollHeight;
                            window.scrollBy(0, distance);
                            totalHeight += distance;
                            if(totalHeight >= scrollHeight){
                                clearInterval(timer);
                                resolve();
                            }
                        }, 100);
                    });
                }""")
                time.sleep(2)
            else:
                print("      ❌ self.page is None, cannot extract content")
                return None

            soup = BeautifulSoup(self.page.content(), "html.parser")
            title_el = soup.select_one("h1")
            title = self._clean(title_el.text) if title_el else "Başlık Yok"

            if "gecmis" in url or "geçmiş" in title.lower():
                return None

            # Image (try background-image style first)
            image_url = None
            banner = soup.select_one("section.campaign-banner")
            if banner and "style" in banner.attrs:
                match = re.search(r"url\(['\"]?(.*?)['\"]?\)", banner["style"])
                if match:
                    image_url = urljoin(self.BASE_URL, match.group(1))
            if not image_url:
                img_el = soup.select_one(".campaign-detail-header img, section img")
                if img_el and img_el.get("src") and "logo" not in img_el.get("src", ""):
                    image_url = urljoin(self.BASE_URL, img_el["src"])

            # Date
            date_text = ""
            date_label = soup.find(string=re.compile(r"Başlangıç - Bitiş Tarihi"))
            if date_label:
                parent = date_label.parent
                if parent:
                    for sib in parent.next_siblings:
                        if hasattr(sib, 'name') and sib.name and sib.get_text(strip=True):
                            date_text = self._clean(sib.get_text())
                            break
            if not date_text:
                for sel in [".campaign-date", ".date"]:
                    el = soup.select_one(sel)
                    if el:
                        date_text = self._clean(el.text)
                        break

            # Content Extraction Logic
            content_parts = []
            
            # Find all containers that might have content
            # Added '.content' and '.content-part' based on browser inspection
            selectors = [".page-content", "section div.container", ".detail-text", ".campaign-content", ".text-area", ".content", ".content-part", "table"]
            
            for sel in selectors:
                containers = soup.select(sel)
                for container in containers:
                    text = container.get_text(separator="\n", strip=True)
                    if "Üzgünüz, aradığınız sayfayı bulamadık." in text or "Aradığınız sayfa sitemizden kaldırılmış" in text:
                        print(f"      ⚠️ 404 Page Detected (Üzgünüz): {url}")
                        return None
                        
                    if len(text) > 150 and "Ana Sayfa" not in text[:80] and "Maximum Mobil" not in text[:50]:
                        # Check if this part is already substantially covered
                        is_duplicate = False
                        for existing_part in content_parts:
                            if text[:100] in existing_part or existing_part[:100] in text:
                                is_duplicate = True
                                break
                        if not is_duplicate:
                            content_parts.append(text)

            # Fallback to all sections/divs if nothing significant found
            if not content_parts:
                candidate_tags = soup.find_all(["section", "div"], recursive=False)
                if not candidate_tags:
                    # If recursive false finds nothing, try deeper
                    candidate_tags = soup.find_all(["section", "div"])
                    
                for tag in candidate_tags:
                    t = tag.get_text(separator="\n", strip=True)
                    if "Üzgünüz, aradığınız sayfayı bulamadık." in t or "Aradığınız sayfa sitemizden kaldırılmış" in t:
                        print(f"      ⚠️ 404 Page Detected (Üzgünüz): {url}")
                        return None
                        
                    if len(t) > 200 and "Üzgünüz" not in t and "Ana Sayfa" not in t[:50]:
                        content_parts.append(t)

            # Join all parts
            full_text = "\n\n".join(content_parts)
            
            # Clean up conditions by splitting into lines
            # Ensure we don't accidentally join everything into one line in _clean
            lines = full_text.split("\n")
            conditions = []
            for line in lines:
                cleaned = self._clean(line)
                if len(cleaned) > 20 and not cleaned.startswith("Copyright"):
                    conditions.append(cleaned)

            return {
                "title": title, 
                "image_url": image_url,
                "date_text": date_text, 
                "full_text": full_text,
                "conditions": conditions, 
                "source_url": url,
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
            # DD.MM.YYYY - DD.MM.YYYY
            m = re.search(r"(\d{1,2})[./-](\d{1,2})[./-](\d{4})\s*-\s*(\d{1,2})[./-](\d{1,2})[./-](\d{4})", text)
            if m:
                d1, m1, y1, d2, m2, y2 = m.groups()
                return f"{y2}-{m2.zfill(2)}-{d2.zfill(2)}" if is_end else f"{y1}-{m1.zfill(2)}-{d1.zfill(2)}"
            # DD Month - DD Month YYYY
            m = re.search(r"(\d{1,2})\s*([a-zğüşıöç]+)?\s*-\s*(\d{1,2})\s*([a-zğüşıöç]+)\s*(\d{4})", text)
            if m:
                day1, month1, day2, month2, year = m.groups()
                if not month1:
                    month1 = month2
                m1n, m2n = months.get(month1), months.get(month2)
                if m1n and m2n:
                    return f"{year}-{m2n}-{str(day2).zfill(2)}" if is_end else f"{year}-{m1n}-{str(day1).zfill(2)}"
        except Exception:
            pass
        return None

    def _clean(self, text: str) -> str:
        if not text:
            return ""
        return re.sub(r"\s+", " ", text.replace("\n", " ").replace("\r", "")).strip()

    def _to_title_case(self, text: Any) -> str:
        if not text: return ""
        text_str = str(text)
        replacements = {"I": "ı", "İ": "i"}
        lower_text = text_str
        for k, v in replacements.items(): lower_text = lower_text.replace(k, v)
        lower_text = lower_text.lower()
        words = lower_text.split()
        capitalized = []
        for word in words:
            if not word: continue
            if word[0] == 'i': capitalized.append('İ' + word[1:])
            elif word[0] == 'ı': capitalized.append('I' + word[1:])
            else: capitalized.append(word.capitalize())
        return " ".join(capitalized)

    def _get_or_create_slug(self, title: str) -> str:
        base = re.sub(r'[^a-z0-9]+', '-', title.lower()).strip('-')
        slug = base
        counter = 1
        while self.db.query(Campaign).filter(Campaign.slug == slug).first():
            slug = f"{base}-{counter}"
            counter += 1
        return slug

    def _process_campaign(self, url: str, force: bool = False) -> str:
        existing = self.db.query(Campaign).filter(
            Campaign.tracking_url == url, Campaign.card_id == self.card_id
        ).first()
        
        if existing and not force:
            print("   ⏭️  Skipped (Already exists)")
            return "skipped"
        
        if existing and force:
            print(f"   🔄 Updating existing campaign: {existing.title}")

        print(f"🔍 Processing: {url}")
        data = self._extract_campaign_data(url)
        if not data:
            print("   ⏭️  Skipped")
            return "skipped"

        try:
            ai_data = self.parser.parse_campaign_data(
                raw_text=data["full_text"], 
                bank_name=self.BANK_NAME, 
                title=data["title"],
                tracking_url=url, # for global cache
                force=force
            ) or {}
        except Exception as e:
            self.db.rollback()
            print(f"   ⚠️ AI parse error: {e}")
            ai_data = {}

        try:
            raw_title = ai_data.get("title") or data.get("title") or ""
            formatted_title = self._to_title_case(raw_title)
            slug = self._get_or_create_slug(formatted_title)
            ai_cat = ai_data.get("sector", "Diğer")
            sector = self.db.query(Sector).filter(Sector.slug == ai_cat).first()
            if not sector:
                sector = self.db.query(Sector).filter(Sector.slug == 'diger').first()

            start_date, end_date = None, None
            for key, field, is_end in [("start_date", None, False), ("end_date", None, True)]:
                val = ai_data.get(key)
                dt = None
                if val:
                    try:
                        dt = datetime.strptime(val, "%Y-%m-%d")
                    except Exception:
                        pass
                if not dt:
                    parsed = self._parse_date(data["date_text"], is_end=is_end)
                    if parsed:
                        try:
                            dt = datetime.strptime(parsed, "%Y-%m-%d")
                        except Exception:
                            pass
                if key == "start_date":
                    start_date = dt
                else:
                    end_date = dt

            conds = ai_data.get("conditions", [])
            part = ai_data.get("participation")
            if part and "Detayları İnceleyin" not in part:
                conds.insert(0, f"KATILIM: {part}")
            final_conditions = "\n".join(conds)

            if existing:
                # Update existing record
                existing.sector_id = sector.id if sector else None
                existing.title = formatted_title
                existing.description = ai_data.get("description") or data["title"][:200]
                existing.reward_text = ai_data.get("reward_text")
                existing.reward_value = ai_data.get("reward_value")
                existing.reward_type = ai_data.get("reward_type")
                existing.conditions = final_conditions
                existing.eligible_cards = ", ".join(ai_data.get("cards", [])) or None
                existing.image_url = data["image_url"]
                existing.start_date = start_date
                existing.end_date = end_date
                existing.updated_at = func.now()
                self.db.commit()
                print(f"   ✅ Updated: {existing.title[:50]}")
            else:
                campaign = Campaign(
                    card_id=self.card_id, sector_id=sector.id if sector else None,
                    slug=slug, title=formatted_title,
                    description=ai_data.get("description") or data["title"][:200],
                    reward_text=ai_data.get("reward_text"),
                    reward_value=ai_data.get("reward_value"),
                    reward_type=ai_data.get("reward_type"),
                    conditions=final_conditions,
                    eligible_cards=", ".join(ai_data.get("cards", [])) or None,
                    image_url=data["image_url"],
                    start_date=start_date, end_date=end_date,
                    is_active=True, tracking_url=url,
                    created_at=func.now(), updated_at=func.now(),
                )
                self.db.add(campaign)
                self.db.commit()
                print(f"   ✅ Saved: {campaign.title[:50]}")

            # Brands
            for b_name in ai_data.get("brands", []):
                if len(b_name) < 2:
                    continue
                b_slug = re.sub(r'[^a-z0-9]+', '-', b_name.lower()).strip('-')

                try:
                    brand = self.db.query(Brand).filter(
                        (Brand.slug == b_slug) | (Brand.name.ilike(b_name))
                    ).first()
                    if not brand:
                        brand = Brand(name=self._to_title_case(b_name), slug=b_slug)
                        self.db.add(brand)
                        self.db.commit()
                except Exception as e:
                    self.db.rollback()
                    print(f"   ⚠️ Brand save failed for {b_name}: {e}")
                    continue

                try:
                    link = self.db.query(CampaignBrand).filter(
                        CampaignBrand.campaign_id == campaign.id,
                        CampaignBrand.brand_id == brand.id
                    ).first()
                    if not link:
                        self.db.add(CampaignBrand(campaign_id=campaign.id, brand_id=brand.id))
                        self.db.commit()
                except Exception as e:
                    self.db.rollback()
                    print(f"   ⚠️ CampaignBrand link failed: {e}")
                    continue

            print(f"   ✅ Saved: {campaign.title[:50]}")
            return "saved"
        except Exception as e:
            self.db.rollback()
            print(f"   ❌ Save failed: {e}")
            traceback.print_exc()
            return "error"

    def run(self, limit: Optional[int] = None, urls: Optional[List[str]] = None, force: bool = False):
        try:
            print("🚀 Starting İşbankası Maximiles Scraper (Playwright)...")
            self._start_browser()
            
            # Close DB session to prevent idle connection timeout during long Playwright scroll
            if self.db:
                self.db.commit()
                self.db.close()
                
            if urls:
                print(f"🎯 Running specific URLs: {len(urls)}")
                active_urls = urls
                expired_urls = []
            else:
                active_urls, expired_urls = self._fetch_campaign_urls(limit=limit)
            
            # Evaluate expired campaigns logic
            if expired_urls:
                print(f"🛑 Found {len(expired_urls)} expired campaigns on list page. Checking DB for early end...")
                for e_url in expired_urls:
                    try:
                        existing = self.db.query(Campaign).filter(
                            Campaign.tracking_url == e_url,
                            Campaign.card_id == self.card_id,
                            Campaign.is_active == True
                        ).first()
                        if existing:
                            print(f"   🛑 Deleting expired campaign from DB: {existing.title}")
                            self.db.delete(existing)
                            self.db.commit()
                    except Exception as e:
                        if self.db:
                            self.db.rollback()
                        print(f"   ⚠️ Could not update expired campaign {e_url}: {e}")
                        
            urls = active_urls
            success: int = 0
            skipped: int = 0
            failed: int = 0
            error_details: List[Dict[str, Any]] = []
            for i, url in enumerate(urls, 1):
                print(f"\n[{i}/{len(urls)}]")
                try:
                    res = self._process_campaign(url, force=force)
                    if res == "saved":
                        success = int(success or 0) + 1  # type: ignore
                    elif res == "skipped":
                        skipped = int(skipped or 0) + 1  # type: ignore
                    else:
                        failed = int(failed or 0) + 1  # type: ignore
                        error_details.append({"url": url, "error": "Unknown DB failure"})
                except Exception as e:
                    print(f"❌ Error: {e}")
                    failed += 1
                    error_details.append({"url": url, "error": str(e)})
                time.sleep(1.5)
            print(f"\n🏁 Finished. {len(urls)} found, {success} saved, {skipped} skipped, {failed} errors")
            
            status = "SUCCESS"
            if int(failed or 0) > 0:  # type: ignore
                 status = "PARTIAL" if (int(success or 0) > 0 or int(skipped or 0) > 0) else "FAILED"  # type: ignore
                 
            try:
                from src.utils.logger_utils import log_scraper_execution  # type: ignore
                Session = sessionmaker(bind=self.engine)
                with Session() as db:
                     log_scraper_execution(
                          db=db,
                          scraper_name="maximiles",
                          status=status,
                          total_found=len(urls),
                          total_saved=success,
                          total_skipped=skipped,
                          total_failed=failed,
                          error_details={"errors": error_details} if error_details else None
                     )
            except Exception as le:
                 print(f"⚠️ Could not save scraper log: {le}")
                 
        except Exception as e:
            print(f"❌ Scraper error: {e}")
            try:
                from src.utils.logger_utils import log_scraper_execution  # type: ignore
                Session = sessionmaker(bind=self.engine)
                with Session() as db:
                     log_scraper_execution(db, "maximiles", "FAILED", 0, 0, 0, 1, {"error": str(e)})
            except:
                pass
            raise
        finally:
            self._stop_browser()


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=None, help="Limit the number of campaigns to scrape")
    parser.add_argument("--urls", type=str, default=None, help="Comma separated list of URLs to scrape")
    parser.add_argument("--force", action="store_true", help="Force update existing campaigns")
    args = parser.parse_args()
    
    url_list = None
    if args.urls:
        url_list = [u.strip() for u in args.urls.split(",") if u.strip()]
        
    scraper = IsbankMaximilesScraper()
    scraper.run(limit=args.limit, urls=url_list, force=args.force)
