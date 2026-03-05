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
from src.models import Campaign, Sector, Brand, CampaignBrand
from src.database import get_db_session
from src.services.ai_parser import parse_campaign_data, AIParser

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
        response = requests.get(url, headers=headers, timeout=15)
        response.raise_for_status()
        
        # Simple cleanup
        soup = BeautifulSoup(response.text, 'html.parser')
        for script in soup(["script", "style", "nav", "footer", "header"]):
            script.extract()
        return soup.get_text(separator=' ', strip=True)
    except Exception as e:
        print(f"      ⚠️ Failed to fetch HTML for {url}: {e}")
        return ""

def run_autofix():
    print("🚀 Starting Data Quality Auto-Fixer...")
    
    try:
        with get_db_session() as db:
            print("\n🔍 Scanning for defective campaigns...")

            from datetime import timedelta
            # Only check campaigns created in the last 2 days
            # Older defective campaigns have likely unfixable URLs — no point retrying daily
            cutoff = datetime.utcnow() - timedelta(days=2)

            # Find active campaigns with missing/poor descriptions, reward texts, or conditions
            defective_campaigns = db.query(Campaign).filter(
                Campaign.is_active == True,
                Campaign.created_at >= cutoff,
            ).all()
            print(f"   📊 Checking {len(defective_campaigns)} campaigns created in last 2 days.")
            
            to_fix = []
            for c in defective_campaigns:
                is_defective = False
                reasons = []
                
                if not c.description or len(c.description.strip()) < 15:
                    is_defective = True
                    reasons.append("Missing/Short Description")
                if not c.reward_text or c.reward_text.strip() == "":
                    is_defective = True
                    reasons.append("Missing Reward Text")
                if c.reward_value is None:
                    is_defective = True
                    reasons.append("Missing Reward Value")
                if not c.reward_type or c.reward_type.strip() == "":
                    is_defective = True
                    reasons.append("Missing Reward Type")
                if not c.eligible_cards or c.eligible_cards.strip() == "":
                    is_defective = True
                    reasons.append("Missing Eligible Cards")
                if not c.start_date:
                    is_defective = True
                    reasons.append("Missing Start Date")
                if not c.end_date:
                    is_defective = True
                    reasons.append("Missing End Date")
                if not c.conditions or c.conditions.strip() == "":
                    is_defective = True
                    reasons.append("Missing Conditions")

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

                if is_defective and c.tracking_url:
                    to_fix.append({"campaign": c, "reasons": reasons})
            
            print(f"⚠️ Found {len(to_fix)} defective campaigns requiring repair.")
            
            if not to_fix:
                print("✅ All active campaigns look healthy! Exiting.")
                return
                
            fixed_count = 0
            
            for item in to_fix:
                c_id = item["campaign"].id
                reasons = ", ".join(item["reasons"])
                
                # Re-fetch campaign to avoid ObjectDeletedError or DetachedInstanceError across rollbacks
                c = db.query(Campaign).get(c_id)
                if not c:
                    print(f"\n🛠️ Skipping: [{c_id}] (Campaign no longer in DB)")
                    continue
                    
                print(f"\n🛠️ Fixing: [{c.id}] {c.title[:40]}... (Reasons: {reasons})")
                print(f"   🔗 URL: {c.tracking_url}")
                
                # Fetch fresh HTML
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
                
                if not c.description or len(c.description.strip()) < 15:
                    if ai_data.get("description"):
                        print(f"   ✨ Repaired Description!")
                        c.description = ai_data["description"]
                        updated = True
                        
                if not c.reward_text or c.reward_text.strip() == "":
                    if ai_data.get("reward_text"):
                        print(f"   ✨ Repaired Reward Text!")
                        c.reward_text = ai_data["reward_text"]
                        updated = True
                        
                if c.reward_value is None:
                    if ai_data.get("reward_value") is not None:
                        print(f"   ✨ Repaired Reward Value: {ai_data['reward_value']}")
                        c.reward_value = ai_data["reward_value"]
                        updated = True
                        
                if not c.reward_type or c.reward_type.strip() == "":
                    if ai_data.get("reward_type"):
                        print(f"   ✨ Repaired Reward Type: {ai_data['reward_type']}")
                        c.reward_type = ai_data["reward_type"]
                        updated = True
                        
                if not c.eligible_cards or c.eligible_cards.strip() == "":
                    if ai_data.get("cards") and len(ai_data["cards"]) > 0:
                        cards_str = ", ".join(ai_data["cards"])
                        print(f"   ✨ Repaired Eligible Cards: {cards_str}")
                        c.eligible_cards = cards_str
                        updated = True

                if not c.start_date:
                    if ai_data.get("start_date"):
                        print(f"   ✨ Repaired Start Date: {ai_data['start_date']}")
                        from datetime import datetime
                        try:
                            c.start_date = datetime.strptime(ai_data["start_date"], "%Y-%m-%d")
                            updated = True
                        except: pass

                if not c.end_date:
                    if ai_data.get("end_date"):
                        print(f"   ✨ Repaired End Date: {ai_data['end_date']}")
                        from datetime import datetime
                        try:
                            c.end_date = datetime.strptime(ai_data["end_date"], "%Y-%m-%d")
                            updated = True
                        except: pass
                        
                if not c.conditions or c.conditions.strip() == "":
                    if ai_data.get("conditions"):
                        print(f"   ✨ Repaired Conditions!")
                        c.conditions = "\n".join(f"- {cond}" for cond in ai_data.get("conditions", []))
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

                if updated:
                    db.commit()
                    fixed_count += 1
                    print(f"   ✅ Campaign successfully repaired and saved!")
                else:
                    print(f"   ⚠️ AI didn't find the missing data. No changes made.")

                # Be gentle to the API limits
                time.sleep(2)
                
            print(f"\n🏁 Auto-fixer complete. Successfully repaired {fixed_count}/{len(to_fix)} campaigns.")
            
    except Exception as e:
        print(f"\n📛 CRITICAL ERROR during auto-fix: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)

if __name__ == "__main__":
    run_autofix()
