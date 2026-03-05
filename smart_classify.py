"""
Smart Campaign Classifier

Lightweight alternative to data_quality_autofix.py.
Works ENTIRELY from existing DB fields — no URL fetching, no Playwright.

Tasks:
1. Sector / Brand fix  → sends title+description+conditions to Gemini (very small prompt)
2. Date completion     → rule-based, zero AI calls:
     start only, no end  → end_date set to last day of start_date's month
     end only, no start  → start_date set to today (scrape date)
"""

import os
import sys
import re
import time
from calendar import monthrange
from datetime import datetime, date

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.models import Campaign, Sector, Brand, CampaignBrand
from src.database import get_db_session
from src.services.ai_parser import AIParser

# ── Sector slug map (display name → slug) ──────────────────────────────────
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
    "Diğer": "diger",
}
VALID_SLUGS = set(SECTOR_MAP.values())

# ── Gemini prompt ───────────────────────────────────────────────────────────
CLASSIFY_PROMPT = """Sen bir kampanya sınıflandırma asistanısın. Aşağıdaki kampanya bilgilerinden:
1. En uygun sektör slug'ını (geçerli listeden BİR tane)
2. Kampanyada geçen marka adlarını (liste)
belirle. Sadece JSON döndür, başka hiçbir şey yazma.

Geçerli sektör slug'ları:
{valid_slugs}

Kampanya Başlığı: {title}
Açıklama: {description}
Koşullar: {conditions}

JSON formatı:
{{"sector": "slug-buraya", "brands": ["Marka1", "Marka2"]}}"""


def _build_prompt(campaign) -> str:
    description = (campaign.description or "").strip()[:300]
    conditions  = (campaign.conditions  or "").strip()[:400]
    return CLASSIFY_PROMPT.format(
        valid_slugs=", ".join(sorted(VALID_SLUGS)),
        title=campaign.title or "",
        description=description,
        conditions=conditions,
    )


def _parse_ai_json(text: str) -> dict:
    """Robustly extract JSON from AI response."""
    import json
    try:
        return json.loads(text.strip())
    except Exception:
        pass
    # Try extracting first JSON block
    match = re.search(r'\{.*?\}', text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group())
        except Exception:
            pass
    return {}


def _last_day_of_month(dt: date) -> date:
    """Return the last day of the month of dt."""
    last = monthrange(dt.year, dt.month)[1]
    return date(dt.year, dt.month, last)


# ── Main ────────────────────────────────────────────────────────────────────

def run_smart_classify():
    print("🚀 Starting Smart Classifier...")
    today = date.today()

    parser = AIParser()

    try:
        with get_db_session() as db:
            all_active = db.query(Campaign).filter(Campaign.is_active == True).all()
            print(f"   📊 Total active campaigns in DB: {len(all_active)}")

            # ── 1. DATE COMPLETION (rule-based, zero AI) ──────────────────
            print("\n📅 Phase 1: Date completion (rule-based)...")
            date_fixed = 0

            for c in all_active:
                updated = False

                # start only → end = last day of start's month
                if c.start_date and not c.end_date:
                    end = _last_day_of_month(c.start_date.date() if hasattr(c.start_date, 'date') else c.start_date)
                    c.end_date = datetime(end.year, end.month, end.day)
                    print(f"   📅 [{c.id}] {c.title[:40]}: end_date ← {end}")
                    updated = True

                # end only → start = today
                elif c.end_date and not c.start_date:
                    c.start_date = datetime(today.year, today.month, today.day)
                    print(f"   📅 [{c.id}] {c.title[:40]}: start_date ← {today}")
                    updated = True

                if updated:
                    date_fixed += 1

            if date_fixed:
                db.commit()
            print(f"   ✅ Date phase: {date_fixed} campaigns fixed.")

            # ── 2. SECTOR + BRAND CLASSIFICATION (Gemini) ─────────────────
            print("\n🤖 Phase 2: Sector & brand classification (Gemini)...")

            to_classify = []
            for c in all_active:
                needs_sector = (
                    not c.sector_id
                    or (c.sector and c.sector.slug == "diger")
                    or (c.sector and c.sector.slug not in VALID_SLUGS)
                )
                needs_brand = not c.brands
                if needs_sector or needs_brand:
                    to_classify.append((c, needs_sector, needs_brand))

            print(f"   🔍 Found {len(to_classify)} campaigns needing classification.")

            if not to_classify:
                print("   ✅ All campaigns already classified!")
                return

            sector_fixed = 0
            brand_fixed  = 0

            for idx, (c_obj, needs_sector, needs_brand) in enumerate(to_classify, 1):
                c_id = c_obj.id
                # Re-fetch to avoid stale session issues
                c = db.query(Campaign).get(c_id)
                if not c:
                    continue

                reasons = []
                if needs_sector:
                    reasons.append("sector")
                if needs_brand:
                    reasons.append("brand")
                print(f"\n[{idx}/{len(to_classify)}] [{c.id}] {c.title[:50]} (fix: {', '.join(reasons)})")

                prompt = _build_prompt(c)
                try:
                    raw = parser._call_ai(prompt, timeout_sec=30)
                    ai = _parse_ai_json(raw)
                except Exception as e:
                    print(f"   ⚠️ AI error: {e}")
                    time.sleep(2)
                    continue

                updated = False

                # Sector
                if needs_sector:
                    slug = ai.get("sector", "diger")
                    if slug not in VALID_SLUGS:
                        # Try display name → slug mapping
                        slug = SECTOR_MAP.get(slug, "diger")
                    if slug != "diger":
                        sector = db.query(Sector).filter(Sector.slug == slug).first()
                        if sector:
                            c.sector_id = sector.id
                            print(f"   ✨ Sector → {sector.name}")
                            sector_fixed += 1
                            updated = True

                # Brands
                if needs_brand and ai.get("brands"):
                    for b_name in ai["brands"]:
                        b_name = b_name.strip()
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
                            existing_link = db.query(CampaignBrand).filter(
                                CampaignBrand.campaign_id == c.id,
                                CampaignBrand.brand_id == brand.id
                            ).first()
                            if not existing_link:
                                db.add(CampaignBrand(campaign_id=c.id, brand_id=brand.id))
                                print(f"   ✨ Brand → {b_name}")
                                brand_fixed += 1
                                updated = True
                        except Exception as be:
                            db.rollback()
                            print(f"   ⚠️ Brand error for {b_name}: {be}")

                if updated:
                    db.commit()

                # Be gentle to limits
                time.sleep(1)

            print(f"\n🏁 Smart Classifier done.")
            print(f"   📅 Dates fixed  : {date_fixed}")
            print(f"   🏷️  Sectors fixed: {sector_fixed}")
            print(f"   🔖 Brands fixed : {brand_fixed}")

    except Exception as e:
        print(f"\n📛 CRITICAL ERROR: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    run_smart_classify()
