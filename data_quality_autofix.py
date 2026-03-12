"""
Data Quality Auto-Fixer

This script scans active campaigns in the database for missing vital information
(such as short/missing description, missing reward text, etc.). If it finds a
defective campaign, it attempts to fetch the HTML from its tracking_url and
passes it back through the Gemini AI parser to repair the missing fields.
"""

import os
import sys
import time
import requests
from bs4 import BeautifulSoup

# Add the parent directory to Python path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import re
import uuid
import logging

# Suppress noisy INFO logs from underlying AI libraries
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("google_genai.models").setLevel(logging.WARNING)

from src.models import Campaign, Sector, Brand, CampaignBrand
from src.database import get_db_session
from src.services.ai_parser import parse_campaign_data, AIParser
from sqlalchemy.orm import joinedload

# Shared cleaner — same preprocessing scrapers use (filters boilerplate, dedup, 6K limit)
_clean_text = AIParser._clean_text

SECTOR_MAP = {
    "Market & Gıda": "market-gida",
    "Akaryakıt": "akaryakit",
    "Giyim & Aksesuar": "giyim-aksesuar",
    "Restoran & Kafe": "restoran-kafe",
    "Elektronik": "elektronik",
    "Mobilya, Dekorasyon & Yapı Market": "mobilya-dekorasyon",
    "Sağlık, Kozmetik & Kişisel Bakım": "kozmetik-saglik",
    "E-Ticaret": "e-ticaret",
    "Ulaşım": "ulasim",
    "Dijital Platform & Oyun": "dijital-platform",
    "Spor, Kültür & Eğlence": "kultur-sanat",
    "Eğitim": "egitim",
    "Sigorta": "sigorta",
    "Otomotiv": "otomotiv",
    "Vergi & Kamu": "vergi-kamu",
    "Turizm, Konaklama & Seyahat": "turizm-konaklama",
    "Mücevherat, Optik & Saat": "kuyum-optik-ve-saat",
    "Fatura & Telekomünikasyon": "fatura-telekomunikasyon",
    "Anne, Bebek & Oyuncak": "anne-bebek-oyuncak",
    "Kitap, Kırtasiye & Ofis": "kitap-kirtasiye-ofis",
    "Evcil Hayvan & Petshop": "evcil-hayvan-petshop",
    "Hizmet & Bireysel Gelişim": "hizmet-bireysel-gelisim",
    "Finans & Yatırım": "finans-yatirim",
    "Diğer": "diger"
}

def fetch_html(url: str) -> str:
    """Attempts to fetch the HTML content of a URL."""
    try:
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36'
        }
        import urllib3
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
        response = requests.get(url, headers=headers, timeout=15, verify=False)
        response.raise_for_status()
        
        # Ensure correct encoding (often ISO-8859-9 or UTF-8 for Turkish sites)
        if response.encoding == 'ISO-8859-1':
            response.encoding = response.apparent_encoding
            
        # Simple cleanup
        soup = BeautifulSoup(response.text, 'html.parser')
        for script in soup(["script", "style", "nav", "footer", "header"]):
            script.extract()
        
        # Remove multiple spaces and newlines
        text = soup.get_text(separator=' ', strip=True)
        text = re.sub(r'\s+', ' ', text)
        return text
    except Exception as e:
        print(f"      ⚠️ Failed to fetch HTML for {url}: {e}")
        return ""

def run_autofix():
    print("🚀 Starting Data Quality Auto-Fixer...")
    
    try:
        with get_db_session() as db:
            print("\n🔍 Scanning for defective campaigns...")

            # Find active campaigns with missing/poor descriptions, reward texts, or conditions
            # Skip any that have already been auto-corrected to avoid infinite loops for 'Diğer' sectors
            # Use eager loading to prevent N+1 query hangs
            # Find active campaigns. 
            # We check ALL active campaigns now to catch those marked auto_corrected but still having bad data (like generic participation)
            defective_campaigns = db.query(Campaign).options(
                joinedload(Campaign.sector),
                joinedload(Campaign.brands)
            ).filter(
                Campaign.is_active == True
            ).all()
            print(f"   📊 Checking {len(defective_campaigns)} active campaigns for defects.")
            
            SCAN_ONLY = False # If True, will only count defects without fixing
            FORCE_ALL = False # If True, will fix all active campaigns regardless of status
            
            to_fix_ids = []
            for c in defective_campaigns:
                is_defective = False
                reasons = []
                
                # New detection pattern: character-level corruption (e.g., 'P, a, r, a, f')
                corrupted_regex = re.compile(r'([a-zA-ZçğıüşöÇĞİÜŞÖ0-9], ){2,}')
                generic_participation = "Mobil uygulama üzerinden veya banka kanallarından kampanya detaylarındaki talimatları izleyerek katılabilirsiniz."
                useless_participations = [
                    generic_participation, 
                    "Hemen faydalanabilirsiniz.", 
                    "Hemen faydalanabilirsiniz", 
                    "Kampanya dahilinde.",
                    "Detayları İnceleyin",
                    "Detayları inceleyin",
                    "Hemen faydalanmaya başlayın.",
                    "Axess Mobil uygulama üzerinden katılabilirsiniz.",
                    "Harcamadan önce mobil uygulama üzerinden katılın.",
                    "Harcamadan önce Mobilden katılın.",
                    "Juzdan uygulama üzerinden katılabilirsiniz.",
                    "Juzdan üzerinden katılabilirsiniz.",
                    "Mobil Şube üzerinden Kampanyaya Katıl butonuna tıklayın",
                    "Kampanyaya katılmak için Mobil Şube üzerinden Kampanyaya Katıl butonuna tıklamanız yeterlidir."
                ]
                
                is_corrupted = False
                if c.description and corrupted_regex.search(c.description): is_corrupted = True
                if c.conditions and corrupted_regex.search(c.conditions): is_corrupted = True
                if c.eligible_cards and corrupted_regex.search(c.eligible_cards): is_corrupted = True
                if c.ai_marketing_text and corrupted_regex.search(c.ai_marketing_text): is_corrupted = True
                
                if is_corrupted:
                    is_defective = True
                    reasons.append("Character-level Corruption (comma-separated letters)")

                if not c.description or len(c.description.strip()) < 15:
                    is_defective = True
                    reasons.append("Missing/Short Description")
                
                # Check for Default Reward Text
                is_reward_bad = not c.reward_text or c.reward_text.strip() == "" or "Detayları İnceleyin" in (c.reward_text or "") or "Hemen Faydalanın" in (c.reward_text or "")
                if is_reward_bad:
                    is_defective = True
                    reasons.append("Missing/Default Reward Text")
                
                if c.reward_value is None:
                    is_defective = True
                    reasons.append("Missing Reward Value")
                if not c.reward_type or c.reward_type.strip() == "":
                    is_defective = True
                    reasons.append("Missing Reward Type")
                
                # Check for Missing/Corrupted/Generic Eligible Cards
                is_cards_bad = not c.eligible_cards or c.eligible_cards.strip() == "" or "Kampanyaya Dahil Kartlar" in (c.eligible_cards or "") or corrupted_regex.search(c.eligible_cards or "")
                if is_cards_bad:
                    is_defective = True
                    reasons.append("Missing/Corrupted/Generic Eligible Cards")
                
                if not c.start_date:
                    is_defective = True
                    reasons.append("Missing Start Date")
                if not c.end_date:
                    is_defective = True
                    reasons.append("Missing End Date")
                if not c.conditions or c.conditions.strip() == "" or corrupted_regex.search(c.conditions or ""):
                    is_defective = True
                    if "Missing Conditions" not in reasons: reasons.append("Missing/Corrupted Conditions")
                
                # Check for Generic/Missing Participation (in the NEW column)
                is_participation_bad = not c.participation or c.participation.strip() == "" or any(p in (c.participation or "") for p in useless_participations) or "Detayları İnceleyin" in (c.participation or "")
                if is_participation_bad:
                    is_defective = True
                    reasons.append("Missing/Generic Participation Text")
                
                # Check for Missing AI Marketing Summary
                if not c.ai_marketing_text or len(c.ai_marketing_text.strip()) < 10:
                    is_defective = True
                    reasons.append("Missing Marketing Summary")
                
                # Check for Missing Clean Text
                if not c.clean_text or len(c.clean_text.strip()) < 50:
                    is_defective = True
                    reasons.append("Missing Clean Text (Optimize for Search)")

                # Sektör kontrolü: boş, Diğer veya güncel 24 sektör harici
                valid_slugs = set(SECTOR_MAP.values())
                if not c.sector_id:
                    is_defective = True
                    reasons.append("Missing Sector")
                elif c.sector and c.sector.slug == "diger":
                    is_defective = True
                    reasons.append("Sector=Diğer (needs reclassification)")
                elif c.sector and c.sector.slug not in valid_slugs:
                    is_defective = True
                    reasons.append(f"Deprecated Sector ({c.sector.slug})")

                # Marka kontrolü: campaign_brands boş
                if not c.brands:
                    is_defective = True
                    reasons.append("Missing Brands")

                # Mojibake check (UTF-8/ISO mismatch)
                mojibake_pattern = re.compile(r'[ÄÃÅ][\u0080-\u00bf]')
                has_mojibake = False
                
                # Sadece daha önce TAMİR EDİLMEMİŞ kampanyalarda clean_text mojibake kontrolü yap
                # Çünkü AI parser clean_text'i tamir etmez, sadece description'ı tamir eder. Bu da sonsuz döngü yaratır.
                if c.clean_text and not c.auto_corrected and mojibake_pattern.search(c.clean_text): 
                    has_mojibake = True
                
                # Ancak kullanıcıların gördüğü asıl açıklamada mojibake varsa her zaman tamir et
                if c.description and mojibake_pattern.search(c.description): 
                    has_mojibake = True
                
                if has_mojibake:
                    is_defective = True
                    reasons.append("Character Encoding Issue (Mojibake)")

                if is_defective and c.tracking_url:
                    # If auto_corrected is True, only fix if it's due to generic/bad data
                    if c.auto_corrected:
                        # Only re-fix if it's one of the "persistent" generic issues
                        if is_cards_bad or is_participation_bad or is_corrupted or has_mojibake:
                            to_fix_ids.append((c.id, c.tracking_url, reasons))
                    else:
                        to_fix_ids.append((c.id, c.tracking_url, reasons))
            
            print(f"⚠️ Total campaigns to process: {len(to_fix_ids)} (FORCE_ALL={FORCE_ALL})")
            
            if not to_fix_ids:
                print("✅ All active campaigns look healthy! Exiting.")
                return
                
        fixed_count = 0
            
        for c_id, tracking_url, reasons_list in to_fix_ids:
            summary_reasons = ", ".join(reasons_list)
            
            with get_db_session() as db:
                c = db.get(Campaign, c_id)
                if not c:
                    print(f"\n🛠️ Skipping: [{c_id}] (Campaign no longer in DB)")
                    continue
                    
                print(f"\n🛠️ Fixing: [{c.id}] {c.title[:40]}... (Reasons: {summary_reasons})")
                print(f"   🔗 URL: {c.tracking_url}")
                
                # Use optimized clean_text from DB if available
                text_to_parse = ""
                if c.clean_text and len(c.clean_text) > 50:
                    print(f"   ⚡ Using pre-cleaned text from DB ({len(c.clean_text)} chars)")
                    text_to_parse = c.clean_text
                else:
                    # Fallback to fetching fresh HTML for old unoptimized campaigns
                    print(f"   🌐 Fetching HTML fallback for old campaign...")
                    html_text = fetch_html(c.tracking_url)
                    if not html_text or len(html_text) < 50:
                        print(f"   ❌ Could not extract meaningful text from URL. Skipping.")
                        continue
                    
                    # Clean text with the same preprocessor scrapers use
                    text_to_parse = _clean_text(None, html_text)

                print(f"   🤖 Sending {len(text_to_parse)} characters to AI for re-parsing...")
                ai_data = parse_campaign_data(
                    raw_text=text_to_parse,
                    title=c.title,
                )
                
                if not ai_data:
                    print(f"   ❌ Gemini AI failed to return data. Skipping.")
                    continue
                    
                # Update logic
                updated = False
                
                # Update Description
                if not c.description or len(c.description.strip()) < 15 or FORCE_ALL:
                    if ai_data.get("description"):
                        print(f"   ✨ Repaired Description!")
                        c.description = ai_data["description"]
                        updated = True
                        
                # Update Reward Text
                is_reward_bad = not c.reward_text or c.reward_text.strip() == "" or "Detayları İnceleyin" in c.reward_text
                if is_reward_bad or FORCE_ALL:
                    if ai_data.get("reward_text"):
                        print(f"   ✨ Repaired Reward Text: {ai_data['reward_text']}")
                        c.reward_text = ai_data["reward_text"]
                        updated = True
                        
                if c.reward_value is None or FORCE_ALL:
                    if ai_data.get("reward_value") is not None:
                        print(f"   ✨ Repaired Reward Value: {ai_data['reward_value']}")
                        c.reward_value = ai_data["reward_value"]
                        updated = True
                        
                if not c.reward_type or c.reward_type.strip() == "" or FORCE_ALL:
                    if ai_data.get("reward_type"):
                        print(f"   ✨ Repaired Reward Type: {ai_data['reward_type']}")
                        c.reward_type = ai_data["reward_type"]
                        updated = True
                        
                # Update Eligible Cards if missing, corrupted or generic
                if not c.eligible_cards or c.eligible_cards.strip() == "" or "Kampanyaya Dahil Kartlar" in (c.eligible_cards or "") or corrupted_regex.search(c.eligible_cards or ""):
                    if ai_data.get("cards") and len(ai_data["cards"]) > 0:
                        cards_str = ", ".join(ai_data["cards"])
                        print(f"   ✨ Repaired Eligible Cards: {cards_str}")
                        c.eligible_cards = cards_str
                        updated = True

                if not c.start_date or FORCE_ALL:
                    if ai_data.get("start_date"):
                        print(f"   ✨ Repaired Start Date: {ai_data['start_date']}")
                        from datetime import datetime
                        try:
                            c.start_date = datetime.strptime(ai_data["start_date"], "%Y-%m-%d")
                            updated = True
                        except: pass

                if not c.end_date or FORCE_ALL:
                    if ai_data.get("end_date"):
                        print(f"   ✨ Repaired End Date: {ai_data['end_date']}")
                        from datetime import datetime
                        try:
                            c.end_date = datetime.strptime(ai_data["end_date"], "%Y-%m-%d")
                            updated = True
                        except: pass
                        
                # Update Conditions if missing, corrupted or FORCE_ALL
                if not c.conditions or c.conditions.strip() == "" or corrupted_regex.search(c.conditions) or FORCE_ALL:
                    if ai_data.get("conditions"):
                        print(f"   ✨ Repaired Conditions!")
                        c.conditions = "\n".join(f"- {cond}" for cond in ai_data.get("conditions", []))
                        updated = True

                # --- Participation and Eligible Cards skip logic bypass ---
                is_cards_defective = not c.eligible_cards or c.eligible_cards.strip() == "" or "Kampanyaya Dahil Kartlar" in (c.eligible_cards or "")
                is_participation_defective = not c.participation or c.participation.strip() == "" or any(p in (c.participation or "") for p in useless_participations)
                
                # Double check for corruption or generic placeholders
                mojibake_pattern = re.compile(r'[ÄÃÅ][\u0080-\u00bf]')
                has_mojibake = False
                if c.clean_text and mojibake_pattern.search(c.clean_text): has_mojibake = True
                if c.description and mojibake_pattern.search(c.description): has_mojibake = True

                # If already auto_corrected, skip ONLY IF it has good data for cards and participation
                # AND it doesn't have corruption/mojibake
                if not FORCE_ALL and c.auto_corrected:
                    if not is_cards_defective and not is_participation_defective and not is_corrupted and not has_mojibake:
                        continue

                # Clean and update Participation
                is_curr_p_bad = not c.participation or c.participation.strip() == "" or any(p in (c.participation or "") for p in useless_participations) or corrupted_regex.search(c.participation)
                if is_curr_p_bad or FORCE_ALL:
                    if ai_data.get("participation"):
                        print(f"   ✨ Repaired Participation: {ai_data['participation'][:50]}...")
                        c.participation = ai_data["participation"]
                        updated = True

                # --- AI Marketing Text (Marketing Summary) update ---
                if ai_data.get("ai_marketing_text"):
                    # We always update this to get fresh summaries
                    c.ai_marketing_text = ai_data["ai_marketing_text"]
                    updated = True

                # --- Clean Text Update ---
                if not c.clean_text or len(c.clean_text.strip()) < 50:
                    if text_to_parse:
                        c.clean_text = text_to_parse
                        updated = True

                # --- Sektör tamiri ---
                ai_sector_raw = ai_data.get("sector", "diger")
                if isinstance(ai_sector_raw, list):
                    ai_sector_raw = ai_sector_raw[0] if len(ai_sector_raw) > 0 else "diger"
                
                # Try to map if AI returned a display name, otherwise assume it's a slug
                final_sector_slug = SECTOR_MAP.get(ai_sector_raw, ai_sector_raw)
                
                if final_sector_slug not in SECTOR_MAP.values():
                    final_sector_slug = "diger"
                    
                needs_sector_fix = (
                    not c.sector_id or
                    (c.sector and c.sector.slug != final_sector_slug)
                )
                if needs_sector_fix and final_sector_slug != "diger":
                    sector = db.query(Sector).filter(Sector.slug == final_sector_slug).first()
                    if not sector:
                        sector = db.query(Sector).filter(Sector.slug == 'diger').first()
                    if sector:
                        c.sector_id = sector.id
                        print(f"   ✨ Repaired Sector: {sector.name}")
                        updated = True

                # --- Marka tamiri ---
                if not c.brands and ai_data.get("brands"):
                    for b_name in ai_data["brands"]:
                        if len(b_name) < 2:
                            continue
                        b_slug = re.sub(r'[^a-z0-9]+', '-', b_name.lower()).strip('-')
                        try:
                            brand = db.query(Brand).filter(
                                (Brand.slug == b_slug) | (Brand.name.ilike(b_name))
                            ).first()
                            if not brand:
                                brand = Brand(name=b_name, slug=b_slug)
                                db.add(brand)
                                db.flush()
                            link = db.query(CampaignBrand).filter(
                                CampaignBrand.campaign_id == c.id,
                                CampaignBrand.brand_id == brand.id
                            ).first()
                            if not link:
                                db.add(CampaignBrand(campaign_id=c.id, brand_id=brand.id))
                                print(f"   ✨ Added Brand: {b_name}")
                                updated = True
                        except Exception as be:
                            db.rollback()
                            print(f"   ⚠️ Brand fix failed for {b_name}: {be}")

                # ALWAYS mark as auto_corrected so we don't try again forever (even if Gemini failed to find missing data)
                c.auto_corrected = True
                updated = True

                if updated:
                    db.commit()
                    fixed_count += 1
                    print(f"   ✅ Campaign successfully repaired and saved! (Marked as auto_corrected)")
                else:
                    print(f"   ⚠️ AI didn't find the missing data. No changes made.")

            # Be gentle to the API limits
            time.sleep(2)
            
        print(f"\n🏁 Auto-fixer complete. Successfully repaired {fixed_count}/{len(to_fix_ids)} campaigns.")
            
    except Exception as e:
        print(f"\n📛 CRITICAL ERROR during auto-fix: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)

if __name__ == "__main__":
    run_autofix()
