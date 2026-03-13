# pyre-ignore-all-errors
# type: ignore

import os
import re
import sys
import time
import json
import requests # type: ignore
import urllib3 # type: ignore
import random
from datetime import datetime
from typing import Optional, List, Dict, Any
from bs4 import BeautifulSoup # type: ignore
from dotenv import load_dotenv # type: ignore

# Ensure src is in path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from sqlalchemy import create_engine, text # type: ignore
from services.ai_parser import AIParser # type: ignore
from services.brand_normalizer import normalize_brand_name, cleanup_brands # type: ignore

load_dotenv()
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# --- CONFIG ---
DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    raise ValueError("DATABASE_URL is not set in .env")

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

BANK_NAME = "Chippin"
BANK_SLUG = "chippin"
BANK_LOGO = "https://www.chippin.com.tr/assets/img/logo.png"

# Card definitions
CARD_DEFINITIONS = {
    "chippin": {
        "name": "Chippin", 
        "slug": "chippin",
        "domain": "https://www.chippin.com.tr"
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
    text = soup.get_text(separator="\n", strip=True)
    return text

def filter_conditions(conditions: List[str]) -> List[str]:
    """Removes legal disclaimers and standard texts."""
    blacklist = [
        "değişiklik yapma hakkı", 
        "saklı tutar", 
        "yazım hataları", 
        "sorumlu tutulamaz", 
        "sorumluluk kabul edilmez",
        "banka kampanya şartlarını",
        "durdurma hakkına sahiptir"
    ]
    
    clean = []
    for c in conditions:
        c_lower = c.lower()
        if any(b in c_lower for b in blacklist):
            continue
        clean.append(c)
    return clean

class ChippinScraper:
    def __init__(self):
        self.engine = create_engine(DATABASE_URL)
        self.ai_parser = AIParser() if GEMINI_API_KEY else None
        self.bank_id = None
        self._card_cache = {}

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
        """Find sector ID by slug. (AI parser returns a sector slug like 'market-gida')"""
        if not sector_name:
            return None
        try:
            with self.engine.connect() as conn:
                # Search by slug since AI is strictly instructed to return valid slugs
                result = conn.execute(
                    text("SELECT id FROM sectors WHERE slug = :slug LIMIT 1"),
                    {"slug": sector_name}
                ).fetchone()
                return result[0] if result else None
        except Exception:
            return None

    def run(self, limit: int = 1000):
        print("🚀 Starting Chippin Scraper (Requests + JSON)...")
        self._get_or_create_bank()
        
        card_key = "chippin"
        card_def = CARD_DEFINITIONS[card_key]
        card_id = self._get_or_create_card(card_def)
        
        url = "https://www.chippin.com.tr/kampanyalar"
        print(f"   🌐 Fetching: {url}")
        
        success_count = 0
        skipped_count = 0
        failed_count = 0
        error_details = []
        
        headers = {
            "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.0 Mobile/15E148 Safari/604.1"
        }
        
        try:
            response = requests.get(url, headers=headers, verify=False, timeout=20)
            if response.status_code != 200:
                print(f"   ❌ HTTP Error: {response.status_code}")
                return

            # Extract JSON
            soup = BeautifulSoup(response.text, "html.parser")
            script = soup.find("script", {"id": "__NEXT_DATA__"})
            if not script:
                 print("   ❌ __NEXT_DATA__ not found!")
                 return

            data = json.loads(script.string)
            campaigns = data.get("props", {}).get("pageProps", {}).get("campaigns", [])
            
            print(f"   ✅ Found {len(campaigns)} campaigns in JSON.")
            
            campaigns_to_process = campaigns[:limit]
            
            for idx, c in enumerate(campaigns_to_process):
                title = c.get("webName")
                if not title: continue
                
                print(f"[{idx+1}/{len(campaigns_to_process)}] {title[:50]}...")
                
                # Image Handling (Vector Placeholders)
                placeholder_idx = random.randint(1, 9)
                image_url = f"/placeholders/cp-{placeholder_idx:02d}.png"
                
                # Slug & URL Handling
                cid = c.get("id")
                if not cid: continue
                tracking_url = f"https://www.chippin.com.tr/kampanyalar/{cid}"

                # Database Ops - EARLIER CHECK TO SAVE AI CALLS
                try:
                    with self.engine.begin() as conn:
                        existing = conn.execute(text("SELECT id FROM campaigns WHERE tracking_url = :url"), {"url": tracking_url}).fetchone()
                        if existing:
                            print(f"   ⏭️ Skipped (Already exists): {tracking_url}")
                            skipped_count += 1
                            continue
                except Exception as e:
                    print(f"   ❌ DB Pre-check Error: {e}")

                slug_base = slugify(title)
                slug = f"{slug_base}-{cid}"

                content_raw = c.get("webDescription") or ""
                content_text = html_to_text(content_raw)
                
                # AI Parsing
                ai_data = {}
                parser = self.ai_parser
                if parser and content_text:
                    try:
                        ai_data = parser.parse_campaign_data(
                            raw_text=content_text,
                            title=title,
                            bank_name=BANK_NAME,
                            card_name=card_def["name"],
                            tracking_url=tracking_url
                        ) or {}
                    except Exception as e:
                        print(f"      ⚠️  AI Error: {e}")

                # Combine Conditions
                conditions_lines = []
                participation = ai_data.get("participation")
                if participation: conditions_lines.append(f"KATILIM: {participation}")
                    
                eligible_cards = ai_data.get("cards")
                eligible_str = ", ".join(eligible_cards) if eligible_cards else "Chippin"
                if eligible_str and len(eligible_str) > 255: 
                    eligible_str = eligible_str[:255] # type: ignore
                    
                conditions_lines.extend(ai_data.get("conditions", []))
                conditions_lines = filter_conditions(conditions_lines)

                # Reward Handling
                reward_value_raw = ai_data.get("reward_value") or (str(c.get("rebateAmount") or c.get("rebatePercent")) if (c.get("rebateAmount") or c.get("rebatePercent")) else "0")
                reward_val = 0.0
                try:
                    if reward_value_raw:
                        if isinstance(reward_value_raw, str):
                            num_match = re.search(r'[\d\.,]+', reward_value_raw.replace('.', '').replace(',', '.'))
                            reward_val = float(num_match.group()) if num_match else 0.0
                        else:
                            reward_val = float(reward_value_raw)
                except:
                    reward_val = 0.0

                # Database Ops - Insertion
                try:
                    with self.engine.begin() as conn:
                        campaign_data = {
                            "title": ai_data.get("title") or title,
                            "description": ai_data.get("description") or "",
                            "slug": slug,
                            "image_url": image_url,
                            "tracking_url": tracking_url,
                            "start_date": ai_data.get("start_date") or c.get("campaignStartDate"),
                            "end_date": ai_data.get("end_date") or c.get("campaignEndDate"),
                            "sector_id": self._resolve_sector_by_name(str(ai_data.get("sector") or "Diğer")) or self._resolve_sector_by_name("Diğer"),
                            "card_id": card_id,
                            "conditions": "\n".join(conditions_lines) if conditions_lines else None,
                            "eligible_cards": eligible_str,
                            "reward_text": ai_data.get("reward_text"),
                            "reward_value": reward_val,
                            "reward_type": ai_data.get("reward_type"),
                            "clean_text": ai_data.get("_clean_text")
                        }

                        print(f"      ✨ Creating...")
                        result = conn.execute(text("""
                            INSERT INTO campaigns (
                                title, description, slug, image_url, tracking_url, is_active,
                                sector_id, card_id, start_date, end_date, conditions,
                                eligible_cards, reward_text, reward_value, reward_type,
                                clean_text, created_at, updated_at
                            )
                            VALUES (
                                :title, :description, :slug, :image_url, :tracking_url, true,
                                :sector_id, :card_id, :start_date, :end_date, :conditions,
                                :eligible_cards, :reward_text, :reward_value, :reward_type,
                                :clean_text, NOW(), NOW()
                            )
                            RETURNING id
                        """), campaign_data)
                        campaign_id = result.fetchone()[0]
                        success_count += 1

                        # Brands
                        if ai_data.get("brands") and campaign_id:
                            clean_brands = cleanup_brands(ai_data["brands"])
                            for brand_name in clean_brands:
                                brand_res = conn.execute(text("SELECT id FROM brands WHERE name=:name"), {"name": brand_name}).fetchone()
                                if brand_res:
                                    bid = brand_res[0]
                                else:
                                    bslug = f"{slugify(brand_name)}-{int(time.time())}"
                                    brand_res = conn.execute(text("INSERT INTO brands (name, slug, is_active, created_at) VALUES (:name, :slug, true, NOW()) RETURNING id"), {"name": brand_name, "slug": bslug}).fetchone()
                                    bid = brand_res[0]
                                
                                link_check = conn.execute(text("SELECT 1 FROM campaign_brands WHERE campaign_id=:cid AND brand_id=CAST(:bid AS uuid)"), {"cid": campaign_id, "bid": bid}).fetchone()
                                if not link_check:
                                    conn.execute(text("INSERT INTO campaign_brands (campaign_id, brand_id) VALUES (:cid, CAST(:bid AS uuid))"), {"cid": campaign_id, "bid": bid})
                except Exception as e:
                    print(f"   ❌ DB Error: {e}")
                    failed_count += 1
                    error_details.append({"url": tracking_url, "error": f"DB Error: {str(e)}"})

        except Exception as e:
            print(f"   ❌ Error: {e}")
            failed_count += 1
            error_details.append({"url": url, "error": str(e)})
            
        print(f"✅ Özet: {len(campaigns_to_process)} bulundu, {success_count} eklendi, {skipped_count} atlandı, {failed_count} hata aldı.")

        status = "SUCCESS"
        if failed_count > 0:
             status = "PARTIAL" if (success_count > 0 or skipped_count > 0) else "FAILED"

        try:
             from src.utils.logger_utils import log_scraper_execution # type: ignore
             from sqlalchemy.orm import sessionmaker # type: ignore
             Session = sessionmaker(bind=self.engine)
             with Session() as session:
                  log_scraper_execution(
                      db=session,
                      scraper_name="chippin",
                      status=status,
                      total_found=len(campaigns_to_process),
                      total_saved=success_count,
                      total_skipped=skipped_count,
                      total_failed=failed_count,
                      error_details={"errors": error_details} if error_details else None
                  )
        except Exception as log_e:
             print(f"⚠️ Could not save scraper log: {log_e}")

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=1000)
    args = parser.parse_args()

    scraper = ChippinScraper()
    scraper.run(limit=args.limit)
