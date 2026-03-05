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

# ── Keyword → sector slug (200+ keywords, zero AI) ──────────────────────────
SECTOR_KEYWORDS: dict[str, list[str]] = {
    "market-gida": [
        "migros", "a101", "bim", "şok", "carrefour", "metro market",
        "market", "süpermarket", "gıda", "ekmek", "manav",
        "hepsimarket", "getir market", "mopaş", "macro center",
    ],
    "restoran-kafe": [
        "restoran", "kafe", "cafe", "kahve", "coffee", "starbucks",
        "mcdonald", "burger king", "popeyes", "pizza", "yemeksepeti",
        "getir yemek", "trendyol yemek", "gloria jeans", "little caesars",
        "domino", "subway", "kfc", "yeme-içme", "lounge",
    ],
    "giyim-aksesuar": [
        "giyim", "kıyafet", "moda", "fashion", "elbise", "zara", "h&m",
        "koton", "lcw", "stradivarius", "bershka", "mango", "pull&bear",
        "boyner", "defacto", "ipekyol", "altınyıldız", "damat",
        "adidas", "nike", "puma", "çanta", "aksesuar", "takı", "kemer",
    ],
    "elektronik": [
        "elektronik", "laptop", "bilgisayar", "telefon", "akıllı telefon",
        "tablet", "mediamarkt", "teknosa", "vatan", "apple", "samsung",
        "iphone", "airpods", "tv", "televizyon", "lg", "sony", "klima",
        "beyaz eşya", "çamaşır makinesi", "bulaşık makinesi",
    ],
    "mobilya-dekorasyon": [
        "mobilya", "dekorasyon", "koltuk", "masa", "sandalye", "yatak",
        "gardrop", "ikea", "bellona", "istikbal", "yataş", "doğtaş",
        "vivense", "mondi", "zara home", "english home", "madame coco",
        "kilim", "perde", "divanev", "enza home", "alfemo", "intema",
        "vitra", "masko", "baza",
    ],
    "kozmetik-saglik": [
        "kozmetik", "sağlık", "güzellik", "makyaj", "parfüm", "cilt",
        "gratis", "watsons", "rossmann", "flormar", "loreal",
        "nivea", "garnier", "saç bakım", "eczane", "vitamin",
    ],
    "e-ticaret": [
        "trendyol", "hepsiburada", "amazon", "n11", "gittigidiyor",
        "çiçeksepeti", "morhipo", "pazarama", "pttavm", "idefix",
        "online alışveriş",
    ],
    "ulasim": [
        "uçak", "havayolu", "thy", "pegasus", "sunexpress", "atlas jet",
        "tren", "tcdd", "otobüs", "taksi", "uber", "bitaksi",
        "araç kiralama", "enterprise", "budget", "sixt", "avis", "hertz",
        "transfer", "rent a car",
    ],
    "turizm-konaklama": [
        "otel", "tatil", "seyahat", "turizm", "konaklama", "ets tur",
        "jolly", "tatilsepeti", "odamax", "trivago", "booking",
        "resort", "villa", "apart", "tur paket", "cruise", "gemi",
        "enuygun", "prontotour",
    ],
    "dijital-platform": [
        "netflix", "spotify", "youtube", "disney", "bein", "blutv",
        "gain", "amazon prime", "apple tv", "tabii", "mubi",
        "oyun", "game", "steam", "dijital", "abonelik",
    ],
    "kultur-sanat": [
        "sinema", "tiyatro", "konser", "biletix", "passo", "müze",
        "festival", "eğlence", "fitness", "gym", "yoga", "pilates",
        "spor salonu", "yüzme",
    ],
    "egitim": [
        "eğitim", "kurs", "sertifika", "dershane", "dil kursu",
        "udemy", "coursera", "online eğitim", "öğrenci", "lgs", "yks",
    ],
    "sigorta": [
        "sigorta", "kasko", "trafik sigortası", "sağlık sigortası",
        "dask", "konut sigortası", "allianz", "axa", "aksigorta",
    ],
    "otomotiv": [
        "otomotiv", "yedek parça", "lastik", "oto aksesuar",
        "lassa", "bridgestone", "michelin", "bosch servis", "akü",
    ],
    "vergi-kamu": [
        "vergi", "sgk", "e-devlet", "belediye", "gib", "bono", "kamu", "ptt",
    ],
    "fatura-telekomunikasyon": [
        "turkcell", "vodafone", "türk telekom", "telefon fatura",
        "internet fatura", "doğalgaz", "elektrik fatura", "su faturası",
        "telekomünikasyon", "gsm",
    ],
    "anne-bebek-oyuncak": [
        "bebek", "anne", "çocuk oyuncak", "bebek arabası",
        "bebek maması", "lego", "toys", "toyzz",
    ],
    "kitap-kirtasiye-ofis": [
        "d&r", "pandora kitap", "kırtasiye", "ofis malzeme",
        "kalem", "defter", "yazıcı", "toner",
    ],
    "evcil-hayvan-petshop": [
        "evcil hayvan", "petshop", "kedi maması", "köpek maması",
        "petland", "petbulvar", "veteriner",
    ],
    "hizmet-bireysel-gelisim": [
        "danışmanlık", "temizlik hizmeti", "nakliye", "kurye",
        "kuaför", "masaj", "bireysel gelişim",
    ],
    "finans-yatirim": [
        "borsa", "hisse", "yatırım fonu", "altın yatırım", "döviz",
        "kripto", "mevduat", "repo",
    ],
    "akaryakit": [
        "akaryakıt", "benzin", "motorin", "lpg",
        "opet", "shell", "bp", "petrol ofisi", "total", "aytemiz",
    ],
    "kuyum-optik-ve-saat": [
        "kuyum", "mücevher", "pırlanta", "saat mağaza", "optik gözlük",
        "atasay", "gümüş", "elmas",
    ],
}


# ── Gemini fallback prompt (only when keyword match fails) ──────────────────
CLASSIFY_PROMPT = """Kampanya sınıflandırması için JSON döndür (başka hiçbir şey yazma).

Geçerli sektör slug'ları: {valid_slugs}

Başlık: {title}
Açıklama: {description}
Koşullar: {conditions}

{{"sector": "slug-buraya", "brands": ["Marka1"]}}"""


def _keyword_sector(text: str) -> str | None:
    """Return sector slug if any keyword matches, else None."""
    text_lower = text.lower()
    for slug, keywords in SECTOR_KEYWORDS.items():
        if any(kw in text_lower for kw in keywords):
            return slug
    return None


def _db_brands(db, campaign_text: str, all_brands) -> list:
    """Return Brand objects whose name appears in campaign_text."""
    text_lower = campaign_text.lower()
    return [b for b in all_brands if b.name.lower() in text_lower]


def _parse_ai_json(text: str) -> dict:
    import json
    try:
        return json.loads(text.strip())
    except Exception:
        pass
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

            # ── 2. SECTOR + BRAND CLASSIFICATION ──────────────────────────
            print("\n🤖 Phase 2: Sector & brand classification...")

            # Pre-load all brands from DB for fast substring matching
            all_brands = db.query(Brand).all()
            print(f"   🔖 {len(all_brands)} brands in DB for matching")

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
            ai_calls     = 0

            for idx, (c_obj, needs_sector, needs_brand) in enumerate(to_classify, 1):
                c_id = c_obj.id
                c = db.query(Campaign).get(c_id)
                if not c:
                    continue

                campaign_text = f"{c.title or ''} {c.description or ''} {c.conditions or ''}"
                reasons = []
                if needs_sector: reasons.append("sector")
                if needs_brand:  reasons.append("brand")
                print(f"\n[{idx}/{len(to_classify)}] [{c.id}] {c.title[:50]} (fix: {', '.join(reasons)})")

                updated = False

                # ── Sector: keyword matching first ────────────────────────
                sector_slug = None
                if needs_sector:
                    sector_slug = _keyword_sector(campaign_text)
                    if sector_slug:
                        print(f"   🔑 Keyword match → {sector_slug}")
                    else:
                        print(f"   ⚠️ No keyword match, will try Gemini fallback")

                # ── Brands: DB matching first ─────────────────────────────
                matched_brands = []
                if needs_brand:
                    matched_brands = _db_brands(db, campaign_text, all_brands)
                    if matched_brands:
                        print(f"   🔦 DB brand match → {[b.name for b in matched_brands]}")

                # ── Gemini fallback: only if still missing both ───────────
                ai_data = {}
                if (needs_sector and not sector_slug) or (needs_brand and not matched_brands):
                    ai_calls += 1
                    prompt = CLASSIFY_PROMPT.format(
                        valid_slugs=", ".join(sorted(VALID_SLUGS)),
                        title=c.title or "",
                        description=(c.description or "")[:300],
                        conditions=(c.conditions or "")[:300],
                    )
                    try:
                        raw = parser._call_ai(prompt, timeout_sec=30)
                        ai_data = _parse_ai_json(raw)
                        print(f"   🤖 Gemini fallback used (call #{ai_calls})")
                    except Exception as e:
                        print(f"   ⚠️ AI error: {e}")
                    time.sleep(1)

                # ── Apply sector ──────────────────────────────────────────
                if needs_sector:
                    final_slug = sector_slug or SECTOR_MAP.get(ai_data.get("sector", ""), None) or ai_data.get("sector")
                    if final_slug and final_slug in VALID_SLUGS and final_slug != "diger":
                        sector = db.query(Sector).filter(Sector.slug == final_slug).first()
                        if sector:
                            c.sector_id = sector.id
                            print(f"   ✨ Sector → {sector.name}")
                            sector_fixed += 1
                            updated = True

                # ── Apply brands ──────────────────────────────────────────
                brands_to_add = matched_brands or []
                # If AI returned brand names not yet in DB, create them
                if ai_data.get("brands") and not matched_brands:
                    for b_name in ai_data["brands"]:
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
                                all_brands.append(brand)  # keep local list in sync
                            brands_to_add.append(brand)
                        except Exception as be:
                            db.rollback()
                            print(f"   ⚠️ Brand create error for {b_name}: {be}")

                for brand in brands_to_add:
                    try:
                        existing_link = db.query(CampaignBrand).filter(
                            CampaignBrand.campaign_id == c.id,
                            CampaignBrand.brand_id == brand.id
                        ).first()
                        if not existing_link:
                            db.add(CampaignBrand(campaign_id=c.id, brand_id=brand.id))
                            print(f"   ✨ Brand → {brand.name}")
                            brand_fixed += 1
                            updated = True
                    except Exception as be:
                        db.rollback()
                        print(f"   ⚠️ Brand link error: {be}")

                if updated:
                    db.commit()

            print(f"\n🏁 Smart Classifier done.")
            print(f"   📅 Dates fixed   : {date_fixed}")
            print(f"   🏷️  Sectors fixed : {sector_fixed}")
            print(f"   🔖 Brands fixed  : {brand_fixed}")
            print(f"   🤖 Gemini calls  : {ai_calls} (keyword/DB handled the rest)")

    except Exception as e:
        print(f"\n📛 CRITICAL ERROR: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    run_smart_classify()
