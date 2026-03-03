import os
import re
import sys
import time
import requests
from typing import List, Optional
from bs4 import BeautifulSoup
from dotenv import load_dotenv

# Ensure src is in path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from sqlalchemy import create_engine, text
from services.ai_parser import AIParser
from services.brand_normalizer import cleanup_brands

# Playwright
from playwright.sync_api import sync_playwright

load_dotenv()

# --- CONFIG ---
DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    raise ValueError("DATABASE_URL is not set in .env")

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

BANK_NAME = "Türkiye Finans"
BANK_SLUG = "turkiye-finans"
BANK_LOGO = "https://www.turkiyefinans.com.tr/PublishingImages/Logo/tfkb-logo.png"

# Card definitions
CARD_DEFINITIONS = {
    "happy-card": {
        "name": "Happy Card",
        "slug": "happy-card",
        "start_url": "https://www.happycard.com.tr/kampanyalar/Sayfalar/default.aspx",
        "domain": "https://www.happycard.com.tr"
    },
    "ala-card": {
        "name": "Âlâ Kart",
        "slug": "ala-kart",
        "start_url": "https://www.turkiyefinansala.com/tr-tr/kampanyalar/Sayfalar/default.aspx",
        "domain": "https://www.turkiyefinansala.com"
    }
}


def slugify(text: str) -> str:
    text = text.lower()
    tr_map = str.maketrans("çğıöşüÇĞİÖŞÜ", "cgiosucgiosu")
    text = text.translate(tr_map)
    text = re.sub(r'[^a-z0-9\s-]', '', text)
    text = re.sub(r'[\s-]+', '-', text).strip('-')
    return text


def html_to_text(html_content: str) -> str:
    if not html_content:
        return ""
    soup = BeautifulSoup(html_content, "html.parser")
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()
    lines = [l.strip() for l in soup.get_text(separator="\n").splitlines() if l.strip()]
    return "\n".join(lines)


def filter_conditions(conditions: List[str]) -> List[str]:
    """Removes legal disclaimers and standard bank texts from conditions."""
    blacklist = [
        "değişiklik yapma hakkı",
        "saklı tutar",
        "yazım hataları",
        "sorumlu tutulamaz",
        "sorumluluk kabul edilmez",
        "banka kampanya şartlarını",
        "durdurma hakkına sahiptir",
        "sorumluluk türkiye finans"
    ]
    clean = []
    for c in conditions:
        if any(b in c.lower() for b in blacklist):
            continue
        clean.append(c)
    return clean


class TurkiyeFinansScraper:
    def __init__(self):
        self.engine = create_engine(DATABASE_URL)
        self.ai_parser = AIParser() if GEMINI_API_KEY else None
        self.bank_id = None
        self._card_cache = {}
        self.pw = None
        self.browser = None
        self.page = None

    def _start_browser(self):
        """Initializes Playwright browser."""
        print("   🌐 Initializing Playwright browser...")
        self.pw = sync_playwright().start()
        self.browser = self.pw.chromium.launch(
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
        self.page.set_default_timeout(60000)
        print("   ✅ Playwright browser started.")

    def _stop_browser(self):
        try:
            if self.browser:
                self.browser.close()
            if self.pw:
                self.pw.stop()
        except Exception:
            pass

    def _get_or_create_bank(self):
        try:
            with self.engine.begin() as conn:
                result = conn.execute(
                    text("SELECT id FROM banks WHERE slug = :slug"),
                    {"slug": BANK_SLUG}
                ).fetchone()
                if result:
                    self.bank_id = result[0]
                else:
                    print(f"   🏦 Creating Bank: {BANK_NAME}")
                    result = conn.execute(text("""
                        INSERT INTO banks (name, slug, logo_url, is_active, created_at)
                        VALUES (:name, :slug, :logo, true, NOW())
                        RETURNING id
                    """), {"name": BANK_NAME, "slug": BANK_SLUG, "logo": BANK_LOGO}).fetchone()
                    self.bank_id = result[0]
                print(f"   ✅ Bank ID: {self.bank_id}")
        except Exception as e:
            print(f"   ❌ Bank setup failed: {e}")
            raise

    def _get_or_create_card(self, card_def: dict) -> int:
        slug = card_def["slug"]
        if slug in self._card_cache:
            return self._card_cache[slug]
        try:
            with self.engine.begin() as conn:
                result = conn.execute(
                    text("SELECT id FROM cards WHERE slug = :slug"),
                    {"slug": slug}
                ).fetchone()
                if result:
                    card_id = result[0]
                else:
                    print(f"   💳 Creating Card: {card_def['name']}")
                    result = conn.execute(text("""
                        INSERT INTO cards (name, slug, bank_id, card_type, is_active, created_at)
                        VALUES (:name, :slug, :bank_id, 'credit', true, NOW())
                        RETURNING id
                    """), {"name": card_def["name"], "slug": slug, "bank_id": self.bank_id}).fetchone()
                    card_id = result[0]
                self._card_cache[slug] = card_id
                return card_id
        except Exception as e:
            print(f"   ❌ Card setup failed: {e}")
            raise

    def _resolve_sector_by_name(self, sector_name: str) -> Optional[int]:
        if not sector_name:
            return None
        try:
            with self.engine.connect() as conn:
                result = conn.execute(
                    text("SELECT id FROM sectors WHERE name ILIKE :name LIMIT 1"),
                    {"name": f"%{sector_name}%"}
                ).fetchone()
                return result[0] if result else None
        except Exception:
            return None

    def _collect_links(self, card_key: str) -> List[str]:
        """Use Playwright to navigate the campaign list and collect all campaign links."""
        card_def = CARD_DEFINITIONS[card_key]
        start_url = card_def["start_url"]
        domain = card_def["domain"]

        print(f"   🌐 [Playwright] Navigating to list page: {start_url}")
        links = set()

        try:
            self.page.goto(start_url, wait_until="networkidle", timeout=60000)
            time.sleep(3)

            # Scroll to load all lazy-loaded content
            print("   📜 Scrolling to load all campaigns...")
            prev_height = 0
            scroll_attempts = 0
            max_attempts = 20

            while scroll_attempts < max_attempts:
                self.page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                time.sleep(2.5)

                new_height = self.page.evaluate("document.body.scrollHeight")
                if new_height == prev_height:
                    # One more attempt with slightly less scroll
                    self.page.evaluate("window.scrollTo(0, document.body.scrollHeight - 200)")
                    time.sleep(1.5)
                    self.page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                    time.sleep(2)
                    final_height = self.page.evaluate("document.body.scrollHeight")
                    if final_height == new_height:
                        print(f"   ✅ Reached bottom after {scroll_attempts} scrolls.")
                        break
                prev_height = new_height
                scroll_attempts += 1
                print(f"   ⏬ Loaded more content (Scroll {scroll_attempts})...")

            time.sleep(2)

            # Extract all campaign links
            anchors = self.page.query_selector_all("a[href]")
            for a in anchors:
                try:
                    href = a.get_attribute("href")
                    if href and "/kampanyalar/" in href and "default.aspx" not in href.lower() and "spsdisco" not in href:
                        if domain in href:
                            links.add(href)
                        elif href.startswith("/"):
                            links.add(f"{domain}{href}")
                except Exception:
                    continue

            print(f"   ✅ Found {len(links)} links.")
        except Exception as e:
            print(f"   ❌ Link collection error: {e}")

        return sorted(list(links))

    def _process_campaign(self, url: str, card_key: str, card_id: int):
        card_def = CARD_DEFINITIONS[card_key]

        try:
            self.page.goto(url, wait_until="domcontentloaded", timeout=60000)
            time.sleep(2)

            html = self.page.content()
            soup = BeautifulSoup(html, "html.parser")

            # Title Extraction
            title = ""
            GENERIC_TITLES = [
                "kampanyalar", "hesaplar", "yatırım hizmetleri",
                "bankacılık hizmetleri", "âlâ kart", "âlâ hayat blog",
                "kampanya detayı:", "sıkça sorulan sorular", "iletişim"
            ]

            for tag in ["h1", "h2", "h3"]:
                el = soup.find(tag)
                if el:
                    t = el.get_text(strip=True)
                    if t and t.lower() not in GENERIC_TITLES:
                        title = t
                        break

            if not title and soup.title:
                t = soup.title.string
                if t:
                    t = t.replace("- Happy Card", "").replace("Türkiye Finans Happy Kredi Kartları Kampanyalar", "").strip()
                    if t and t.lower() not in GENERIC_TITLES:
                        title = t

            if not title or title.lower() in GENERIC_TITLES:
                print(f"      ⚠️ Valid title not found, skipping {url}")
                return "skipped"

            # Image
            image_url = None
            img_tag = (
                soup.select_one(".campaign-image img") or
                soup.select_one(".ms-rteImage-4") or
                soup.select_one("img[src*='upload']") or
                soup.select_one("img[src*='banner']") or
                soup.select_one("img[src*='kampanya']")
            )
            if img_tag:
                src = img_tag.get("src")
                if src:
                    if src.startswith("http"):
                        image_url = src
                    elif src.startswith("/"):
                        image_url = f"{card_def['domain']}{src}"

            # Content
            candidates = []
            candidates.extend(soup.select(".ms-rtestate-field"))
            candidates.extend(soup.select(".campaign-description"))
            candidates.extend(soup.select(".campaign-text"))
            candidates.extend(soup.select("#content"))
            candidates.extend(soup.select(".content"))

            content_text = ""
            max_len = 0
            for cand in candidates:
                txt = html_to_text(str(cand))
                if len(txt) > max_len:
                    max_len = len(txt)
                    content_text = txt

            if max_len < 100:
                print("      ⚠️ Candidate text too short, trying Body fallback...")
                body_copy = soup.body
                if body_copy:
                    for tag in body_copy.select("nav, footer, header, .menu, .sidebar, script, style, noscript"):
                        tag.decompose()
                    body_text = html_to_text(str(body_copy))
                    if len(body_text) > max_len:
                        content_text = body_text

            # AI Parsing
            ai_data = {}
            if self.ai_parser and content_text:
                try:
                    ai_data = self.ai_parser.parse_campaign_data(
                        raw_text=content_text,
                        title=title,
                        bank_name=BANK_NAME,
                        card_name=card_def["name"],
                    )
                except Exception as e:
                    print(f"      ⚠️ AI Error: {e}")

            link_slug = slugify(url.rstrip("/").split("/")[-1].replace(".aspx", ""))
            if not link_slug or link_slug == "default":
                link_slug = slugify(title)
            slug = f"{link_slug}-{int(time.time())}"

            conditions_lines = []
            participation = ai_data.get("participation")
            if participation:
                conditions_lines.append(f"KATILIM: {participation}")

            eligible_cards = ai_data.get("cards")
            eligible_str = ", ".join(eligible_cards) if eligible_cards else None
            if eligible_str and len(eligible_str) > 255:
                eligible_str = eligible_str[:255]

            conditions_lines.extend(ai_data.get("conditions", []))
            conditions_lines = filter_conditions(conditions_lines)

            campaign_id = None
            with self.engine.begin() as conn:
                existing = conn.execute(
                    text("SELECT id FROM campaigns WHERE tracking_url = :url"),
                    {"url": url}
                ).fetchone()

                if existing:
                    print(f"   ⏭️ Skipped (Already exists): {title[:40]}")
                    return "skipped"

                campaign_data = {
                    "title": ai_data.get("title") or title,
                    "description": ai_data.get("description") or "",
                    "image_url": image_url,
                    "tracking_url": url,
                    "start_date": ai_data.get("start_date"),
                    "end_date": ai_data.get("end_date"),
                    "sector_id": self._resolve_sector_by_name(ai_data.get("sector")) or self._resolve_sector_by_name("Diğer"),
                    "card_id": card_id,
                    "conditions": "\n".join(conditions_lines) if conditions_lines else None,
                    "eligible_cards": eligible_str,
                    "reward_text": ai_data.get("reward_text"),
                    "reward_value": ai_data.get("reward_value"),
                    "reward_type": ai_data.get("reward_type"),
                    "slug": slug,
                }

                print(f"   ✨ Creating: {campaign_data['title'][:40]}")
                result = conn.execute(text("""
                    INSERT INTO campaigns (
                        title, description, slug, image_url, tracking_url, is_active,
                        sector_id, card_id, start_date, end_date, conditions,
                        eligible_cards, reward_text, reward_value, reward_type,
                        created_at, updated_at
                    )
                    VALUES (
                        :title, :description, :slug, :image_url, :tracking_url, true,
                        :sector_id, :card_id, :start_date, :end_date, :conditions,
                        :eligible_cards, :reward_text, :reward_value, :reward_type,
                        NOW(), NOW()
                    )
                    RETURNING id
                """), campaign_data)
                campaign_id = result.fetchone()[0]

                # Brands
                if ai_data.get("brands") and campaign_id:
                    clean_brands = cleanup_brands(ai_data["brands"])
                    for brand_name in clean_brands:
                        brand_res = conn.execute(
                            text("SELECT id FROM brands WHERE name=:name"),
                            {"name": brand_name}
                        ).fetchone()
                        if brand_res:
                            bid = brand_res[0]
                        else:
                            bslug = f"{slugify(brand_name)}-{int(time.time())}"
                            brand_res = conn.execute(
                                text("INSERT INTO brands (name, slug, is_active, created_at) VALUES (:name, :slug, true, NOW()) RETURNING id"),
                                {"name": brand_name, "slug": bslug}
                            ).fetchone()
                            bid = brand_res[0]

                        link_check = conn.execute(
                            text("SELECT 1 FROM campaign_brands WHERE campaign_id=:cid AND brand_id=CAST(:bid AS uuid)"),
                            {"cid": campaign_id, "bid": bid}
                        ).fetchone()
                        if not link_check:
                            conn.execute(
                                text("INSERT INTO campaign_brands (campaign_id, brand_id) VALUES (:cid, CAST(:bid AS uuid))"),
                                {"cid": campaign_id, "bid": bid}
                            )

            return "saved"
        except Exception as e:
            print(f"   ❌ Error processing {url}: {e}")
            return "error"

    def run(self, limit: int = 1000, target: str = "all"):
        print("🚀 Starting Türkiye Finans Scraper (Playwright mode)...")
        self._get_or_create_bank()

        cards_to_process = ["happy-card", "ala-card"] if target == "all" else [target]
        
        total_found = 0
        total_saved = 0
        total_skipped = 0
        total_failed = 0
        error_details = []

        try:
            self._start_browser()

            for card_key in cards_to_process:
                card_def = CARD_DEFINITIONS[card_key]
                print(f"\n👉 Processing {card_def['name']}...")
                try:
                    card_id = self._get_or_create_card(card_def)
                except Exception as e:
                    print(f"   ❌ Card error: {e}")
                    total_failed += 1
                    error_details.append({"url": "card_init", "error": f"Card error for {card_key}: {str(e)}"})
                    continue

                links = self._collect_links(card_key)
                if not links:
                    print(f"   ⚠️ No links found for {card_def['name']}")
                    continue
                
                total_found += len(links[:limit])
                links_to_process = links[:limit]
                print(f"   🎯 Processing {len(links_to_process)} campaigns...")

                success_count = 0
                skipped_count = 0
                failed_count = 0

                for idx, url in enumerate(links_to_process, 1):
                    print(f"[{idx}/{len(links_to_process)}] {url}")
                    try:
                        res = self._process_campaign(url, card_key, card_id)
                        if res == "saved":
                            success_count += 1
                            total_saved += 1
                        elif res == "skipped":
                            skipped_count += 1
                            total_skipped += 1
                        else:
                            failed_count += 1
                            total_failed += 1
                            error_details.append({"url": url, "error": "Unknown DB DB failure or skipping condition"})
                    except Exception as e:
                        print(f"   ❌ Error: {e}")
                        failed_count += 1
                        total_failed += 1
                        error_details.append({"url": url, "error": str(e)})

                    time.sleep(1.5)

                print(f"✅ {card_def['name']} Özet: {len(links_to_process)} bulundu, {success_count} eklendi, {skipped_count} atlandı, {failed_count} hata.")
            
            print("\n🏁 Türkiye Finans Scraper Finished.")
            
            status = "SUCCESS"
            if total_failed > 0:
                 status = "PARTIAL" if (total_saved > 0 or total_skipped > 0) else "FAILED"
                 
            try:
                from src.utils.logger_utils import log_scraper_execution
                from sqlalchemy.orm import sessionmaker
                SessionLocal = sessionmaker(bind=self.engine)
                with SessionLocal() as db:
                     log_scraper_execution(
                          db=db,
                          scraper_name="turkiye-finans",
                          status=status,
                          total_found=total_found,
                          total_saved=total_saved,
                          total_skipped=total_skipped,
                          total_failed=total_failed,
                          error_details={"errors": error_details} if error_details else None
                     )
            except Exception as le:
                 print(f"⚠️ Could not save scraper log: {le}")

        except Exception as e:
            print(f"❌ Fatal error: {e}")
            try:
                from src.utils.logger_utils import log_scraper_execution
                from sqlalchemy.orm import sessionmaker
                SessionLocal = sessionmaker(bind=self.engine)
                with SessionLocal() as db:
                     log_scraper_execution(db, "turkiye-finans", "FAILED", total_found, total_saved, total_skipped, total_failed + 1, {"error": str(e), "details": error_details})
            except:
                pass
        finally:
            self._stop_browser()


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=1000)
    parser.add_argument("--target", type=str, default="all")
    args = parser.parse_args()

    scraper = TurkiyeFinansScraper()
    scraper.run(limit=args.limit, target=args.target)
