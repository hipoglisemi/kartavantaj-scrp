"""
AI Parser Service - THE BRAIN 🧠
Uses Gemini or Groq AI to parse campaign data from raw HTML/text
Replaces 100+ lines of regex with intelligent natural language understanding
"""
import os
import json
import re
import logging
import decimal
import signal
from typing import Dict, Any, Optional, List
from datetime import datetime, timedelta
from dotenv import load_dotenv
from .text_cleaner import clean_campaign_text
from .brand_normalizer import cleanup_brands

class TimeoutException(Exception):
    pass

def timeout_handler(signum, frame):
    raise TimeoutException("Gemini API call timed out")

def call_with_timeout(func, args=(), kwargs=None, timeout_sec=60):
    if kwargs is None:
        kwargs = {}
    
    # Set the signal handler and a alarm
    old_handler = signal.signal(signal.SIGALRM, timeout_handler)
    signal.alarm(timeout_sec)
    try:
        result = func(*args, **kwargs)
        return result
    finally:
        # Disable the alarm
        signal.alarm(0)
        signal.signal(signal.SIGALRM, old_handler)

load_dotenv()

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Bank Specific Rules (Ported from kartavantaj-scraper)
BANK_RULES = {
    'akbank': """
🚨 AKBANK SPECIFIC RULES:
- TERMINOLOGY: 
    - For Axess/Free/Akbank Kart: Uses "chip-para" instead of "puan". 1 chip-para = 1 TL.
    - For Wings: Uses "Mil" or "Mil Puan". 1 Mil = 0.01 TL (unless specified as '1 TL değerinde').
- PARTICIPATION: Primary method is "Jüzdan" app. Always look for "Jüzdan'dan Hemen Katıl" button.
- SMS: Usually 4566. SMS keyword is usually a single word (e.g., "A101", "TEKNOSA").
- REWARD: If it says "8 aya varan taksit", it's an installment campaign. Earning: "Taksit İmkanı".
- ELIGIBLE CARDS:
    - 🚨 TITLE TRAP: Even if title says "Axess'e Özel", check footer for "Axess, Wings, Free... dahildir".
    - "Ticari kartlar" / "Business" / "KOBİ" = ["Axess Business", "Wings Business"].
    - "Bank’O Card Axess" = ["Bank’O Card Axess"].
    - "Akbank Kart" / "Bankamatik" = ["Akbank Kart"].
    - If it says "tüm Akbank kredi kartları", list all relevant consumer cards.
    - 🚨 CONDITIONS RULES: NEVER mention card names in 'conditions' list. They belong ONLY in 'cards' field.
""",
    'yapı kredi': """
🚨 YAPI KREDI (WORLD) SPECIFIC RULES:
- TERMINOLOGY: "Worldpuan" is the currency.
    - ⚠️ IMPORTANT: "TL Worldpuan" means the value is in TL. If it says "100 TL Worldpuan", earning is "100 TL Worldpuan".
    - If it says "1000 Worldpuan", check context. Usually 1 Worldpuan = 0.005 TL. prefer explicitly stated TL value if available.
- ELIGIBLE CARDS:
    - Keywords: "Yapı Kredi Kredi Kartları", "Worldcard", "Opet Worldcard", "Gold", "Platinum", "Business", "World Eko", "Play".
    - "Bireysel kredi kartları" implies all consumer cards.
    - "Business" / "Ticari" implies World Business.
- PARTICIPATION:
    - "World Mobil" or "Yapı Kredi Mobil" is the primary method. Look for "Hemen Katıl", "Katıl" button.
    - SMS: Look for SMS keywords sent to 4454.
""",
    'garanti': """
🚨 GARANTI BBVA/BONUS SPECIFIC RULES:
- TERMINOLOGY: "Bonus" is the currency. 1 Bonus = 1 TL. "Mil" for Shop&Fly/Miles&Smiles.
- ELIGIBLE CARDS:
    - Keywords: "Bonus", "Bonus Gold", "Bonus Platinum", "Bonus American Express", "Shop&Fly", "Miles&Smiles", "Flexi", "Money Bonus".
    - "Ticari" means "Bonus Business".
- PARTICIPATION:
    - Primary: "BonusFlaş" app. Look for "Hemen Katıl" button in app.
    - SMS: Often 3340.
""",
    'işbankası': """
🚨 IS BANKASI/MAXIMUM/MAXIMİLES SPECIFIC RULES:
- TERMINOLOGY: "Maxipuan" (Points) or "MaxiMil" (Miles).
- ELIGIBLE CARDS (cards):
    - 🚨 BASİT VE NET OL: Kampanya sitesindeki "Kampanyaya dâhil olan kartlar" veya "Geçerli Kartlar" kısmında ne yazıyorsa DİREKT ONU YAZ.
    - Örnek: "Bankamatik Kartı, İş Bankası Maximum özellikli kredi kartları (Maximum, Maximiles, Maximiles Black, MercedesCard, İş’te Üniversiteli, Maximum Pati Kart, Maximum Genç)" yazıyorsa AYNEN AL.
    - Sadece "ek kartlar, sanal kartlar, ticari kartlar" gibi genel ibareleri "Ek Kartlar", "Sanal Kartlar", "Ticari Kredi Kartları" şeklinde özetleyip listeye ekleyebilirsin.
    - ❌ KESİN YASAK: Fibabanka, Ziraat gibi diğer banka kartlarını ASLA YAZMA. Sadece İş Bankası kartlarını listele.
- PARTICIPATION (katilim_sekli):
    - 🚨 PRIORITY ORDER:
      1. Primary App: Look for "Katıl" button in "Maximum Mobil", "İşCep" or "Pazarama". → Extract as "Maximum Mobil, İşCep veya Pazarama'dan katılabilirsiniz."
      2. SMS: Look for "4402'ye SMS" → Extract as "4402'ye [KEYWORD] yazıp SMS gönderin."
      3. Automatic: If "katılım gerektirmez" or "otomatik" → Use "Otomatik Katılım".
      4. Fallback: If no button/SMS/app is mentioned but there is a clear instruction like "Kampanya detaylarını inceleyin", write exactly that instruction.
    - 🚨 STRICT APP NAMES: ONLY use "Maximum Mobil", "İşCep", or "Pazarama".
    - ⛔ NEGATIVE CONSTRAINT: NEVER use "World Mobil", "Jüzdan", "BonusFlaş", "Yapı Kredi". If you see these, it's a hallucination or cross-promotion; ignore them.
- 🚨 DISCOUNT CODES: If there is an "İndirim Kodu" (e.g., TRBAN25, TROY2024), **MUTLAKA** both 'conditions' listesine ekle hem de 'description' içinde belirt.
- CONDITIONS (SUMMARY MODE):
    - ✔️ ÖZETLE: Maksimum 5-6 madde. Uzun yasal metinleri, tekrar eden kartım bilgilerini ve işlem türü sayımlarını atlat.
    - 🚨 İÇERİK: Sadece şunları yaz:
      * Minimum harcama eşiği ("2.000 TL harcamaya 200 MaxiMil")
      * Maksimum kazanç limiti ("Maks. 1.500 MaxiMil")
      * Kampanya dışı işlem türleri ("Nakit çekim, havale, iptal/iade işlemleri hariçtir")
      * Hariç tutulan kart grupları ("Ticari Kredi Kartları kampanyaya dahil değildir")
    - ⛔ YAZMA: Tarihleri, katılım yöntemini, zaten ayrı bir listede verdiğin dahil kart isimlerini tekrar YAZMA.
- BRANDS (SECTOR TAGGING):
    - 🚨 ÖNEMLI: Kampanya belirli bir marka/zincir içinse (Zara, Emirates, Migros vb.) o marka ismini 'brands' listesine ekle.
    - Sektör için: "MaxiMil" → Turizm veya Ulaşım olabilir (metne bak); "Duty Free" → Turizm & Konaklama veya Ulaşım; "Pazarama" → E-Ticaret.
""",
    'vakıfbank': """
🚨 VAKIFBANK/WORLD SPECIFIC RULES:
- TERMINOLOGY: "Worldpuan". 1 Worldpuan = 0.005 TL usually. "TL Worldpuan" = TL value.
- ELIGIBLE CARDS (CRITICAL):
    - 📍 LOCATION: Info is usually in the **very first sentence/paragraph** of the text.
    - EXTRACT: "VakıfBank Worldcard", "Platinum", "Rail&Miles", "Bankomat Kart", "Business".
    - IGNORE: General phrases like "Tüm kartlar" if specific ones are listed.
- CONDITIONS (SUMMARY MODE):
    - ✂️ SUMMARIZE: The source text is very long. Convert it into max 4-5 bullet points.
    - SCOPE: Include dates, min spend, reward limit, and exclusions.
- PARTICIPATION:
    - Primary: "Cepte Kazan" app or "VakıfBank Mobil".
    - SMS: Often 6635.
""",
    'ziraat': """
🚨 ZIRAAT BANKKART SPECIFIC RULES:
- TERMINOLOGY: "Bankkart Lira". 1 Bankkart Lira = 1 TL.
- ELIGIBLE CARDS:
    - 🚨 STRICT: EXTRACT ONLY cards explicitly mentioned in the text.
    - If text says "Bankkart'ınız ile", use "Bankkart".
    - Do NOT add "Bankkart Genç", "Başak" etc. unless explicitly listed.
    - 🚨 EXCLUSION: Check for "dahil değildir". "Bankkart Business" and "Ücretsiz" are usually EXCLUDED.
- PARTICIPATION:
    - SMS: Look for specific keywords (e.g., "SUBAT2500", "RAMAZAN", "MARKET") sent to **4757**.
    - App: "Bankkart Mobil", "bankkart.com.tr".
    - Format: "KEYWORD yazıp 4757'ye SMS gönderin" or "Bankkart Mobil uygulamasından katılın".
    - 🚨 FALLBACK: If NO specific method (SMS/App) is found, and it seems like a general campaign (e.g., "İlk Kart", "Taksit"), assume "Otomatik Katılım".
- CONDITIONS:
    - 🚨 FORMAT: SUMMARIZE into 5-6 clear bullet points.
    - 🚨 CONTENT: MUST include numeric limits (max earners, min spend) and dates.
    - Avoid long paragraphs. Use concise language.
"""
,
    'halkbank': """
🚨 HALKBANK (PARAF / PARAFLY) SPECIFIC RULES:
- TERMINOLOGY: "ParafPara". 1 ParafPara = 1 TL.
- ELIGIBLE CARDS:
    - 🚨 STRICT: Look for "Dahil:" or "Geçerli kartlar:" section in conditions.
    - Common INCLUSIONS: "Paraf", "Parafly", "sanal kartlar", "ek kartlar".
    - Common EXCLUSIONS: "Paraf Genç", "banka kartları", "debit", "ticari kartlar", "commercial", "Halkcardlar".
    - 🚨 EXTRACTION LOGIC:
      * If you see "Dahil: Paraf, Parafly, sanal kartlar..." → Extract ["Paraf", "Parafly"]
      * If you see "Hariç: Paraf Genç, banka kartları..." → Exclude those from the list
      * If text says "Tüm Paraf kartları" but excludes some → List main types minus exclusions
    - 🚨 DEFAULT: If no specific cards mentioned, use ["Paraf", "Parafly"]
- PARTICIPATION (katilim_sekli):
    - 🚨 PRIORITY ORDER:
      1. SMS: Look for "3404'e SMS" or "3404'e KEYWORD" → Extract as "3404'e [KEYWORD] SMS"
      2. App: Look for "Paraf Mobil'den HEMEN KATIL" or "Halkbank Mobil'den katılın" → Extract as "Paraf Mobil" or "Halkbank Mobil"
      3. Automatic: If "katılım gerektirmez" or "otomatik" → Use "Otomatik Katılım"
    - 🚨 FORMAT: Be specific. Examples:
      * "Paraf Mobil'den HEMEN KATIL butonuna tıklayın"
      * "3404'e RAMAZAN yazıp SMS gönderin"
      * "Otomatik Katılım"
- CONDITIONS:
    - 🚨 CRITICAL: DO NOT repeat information already shown in separate sections (dates, eligible cards, participation method)
    - 🚨 FOCUS ON UNIQUE DETAILS ONLY:
      * Excluded cards (e.g., "Paraf Genç, banka kartları hariç")
      * Earning tiers (e.g., "5.000 TL'ye 500 TL, 10.000 TL'ye 1.000 TL")
      * Maximum limits (e.g., "Maksimum 2.000 TL kazanç")
      * Special conditions (e.g., "İlk kez başvuranlar", "Sadece yurt içi işlemler")
      * Exclusions (e.g., "Nakit çekim, havale hariç")
      * Usage restrictions (e.g., "ParafPara 6 ay içinde kullanılmalı")
    - 🚨 FORMAT: 3-5 concise bullet points
    - 🚨 AVOID: Repeating dates, card names, or participation method already extracted separately
- DATE LOGIC:
     - If year is missing, look for context (e.g. current year {current_date}).
"""
    ,
    'denizbank': """
🚨 DENIZBANK (DENIZBONUS) SPECIFIC RULES:
- TERMINOLOGY: "Bonus". 1 Bonus = 1 TL.
- ELIGIBLE CARDS:
    - 🚨 STRICT: "DenizBonus", "DenizBonus Gold", "DenizBonus Platinum", "DenizBank Black", "DenizBank TROY".
    - "Ticari Kartlar" = ["DenizBonus Business"].
    - 🚨 EXCLUSION: "Net Kart", "Bankamatik", "Ptt Bonus" are often EXCLUDED.
- PARTICIPATION:
    - 🚨 PRIORITY:
      1. App: "MobilDeniz" or "DenizKartım". Look for "Hemen Katıl" button.
      2. SMS: Look for keywords sent to **3280**. (e.g. "KATIL yazıp 3280'e gönder").
      3. Automatic: If "katılım gerekmemektedir" or "otomatik", use "Otomatik Katılım".
- CONDITIONS:
    - 🚨 FORMAT: Summarize into 3-5 bullets.
    - Include: Max earning limit, start/end dates, valid sectors.
""",
    'qnb': """
🚨 QNB FİNANSBANK SPECIFIC RULES:
- TERMINOLOGY: "ParaPuan". 1 ParaPuan = 1 TL.
- ELIGIBLE CARDS:
    - 🚨 STRICT: Extract ONLY cards explicitly mentioned in the text.
    - Common cards: "QNB Kredi Kartı", "QNB Nakit Banka Kartı", "TROY Kart", "QNB First Kredi Kartı".
    - "Bireysel kredi kartları" = ["QNB Kredi Kartı"].
    - 🚨 EXCLUSION: "Ticari kartlar" are often EXCLUDED unless explicitly mentioned.
- PARTICIPATION:
    - 🚨 PRIORITY ORDER:
      1. SMS: Look for a keyword + "2273" (e.g. "RAMAZAN yazıp 2273'e SMS gönderin").
      2. App: "QNB Mobil" or "QNB Finansbank Mobil". Look for "HEMEN KATIL" button.
      3. Checkout/Sepet: If text says "sepet sayfasında ... seçilmeli" or "ödeme adımında ... seçin" or "ilk 6 hane" → use "Sepet sayfasında QNB İndirimleri seçin ve kart numarasının ilk 6 hanesini girin."
      4. Automatic: ONLY if none of the above apply AND text says "katılım gerektirmez" or "otomatik".
    - ⛔ NEGATIVE: Do NOT write "Otomatik Katılım" if there is any checkout/sepet/6-hane instruction in the text.
    - 🚨 FORMAT: Be specific. Example: "RAMAZAN yazıp 2273'e SMS gönderin veya QNB Mobil'den HEMEN KATIL butonuna tıklayın."
- CONDITIONS:
    - 🚨 CRITICAL: DO NOT repeat information already in dates, eligible cards, or participation sections.
    - 🚨 FOCUS ON UNIQUE DETAILS ONLY:
      * Minimum spend thresholds (e.g. "Her 2.500 TL harcamaya 200 TL ParaPuan")
      * Maximum earning limits (e.g. "Maksimum 3.000 TL ParaPuan")
      * Excluded transaction types (e.g. "Nakit çekim, havale hariç")
      * Excluded card types (e.g. "Ticari kartlar hariç")
      * ParaPuan usage restrictions (e.g. "ParaPuan 30 gün içinde yüklenir")
    - 🚨 FORMAT: 3-5 concise bullet points. NO long paragraphs.
    - 🚨 AVOID: Repeating dates, card names, or SMS/app instructions already extracted.
"""
    ,
    'teb': """
🚨 TEB (TÜRK EKONOMİ BANKASI) SPECIFIC RULES:
- TERMINOLOGY: "Bonus". 1 Bonus = 1 TL. "TEB Bonus" is the reward program name.
- ELIGIBLE CARDS:
    - 🚨 STRICT: Extract ONLY cards explicitly mentioned in the text.
    - Common cards: "TEB Kredi Kartı", "TEB Bonus Kart", "TEB Banka Kartı", "CEPTETEB".
    - "Bireysel kredi kartları" = ["TEB Kredi Kartı"].
    - 🚨 EXCLUSION: "Ticari kartlar" are often EXCLUDED unless explicitly mentioned.
- PARTICIPATION:
    - 🚨 PRIORITY ORDER:
      1. Campaign Code + SMS: If text contains "Kampanya Kodu: XXXXX" at the top, the participation is "XXXXX yazıp 5350'ye SMS gönderin."
      2. App: "TEB Mobil" or "CEPTETEB". Look for "Hemen Katıl" button.
      3. Checkout/Sepet: If text says "ödeme adımında ... seçin" or "sepet sayfasında" → describe the checkout step.
      4. Automatic: ONLY if text explicitly says "katılım gerektirmez" or "otomatik".
    - ⛔ NEGATIVE: Do NOT write "Otomatik Katılım" if there is a campaign code or any checkout instruction.
    - 🚨 FORMAT: Be specific. Example: "MARKET2026 yazıp 5350'ye SMS gönderin veya TEB Mobil'den Hemen Katıl butonuna tıklayın."
- CONDITIONS:
    - 🚨 CRITICAL: DO NOT repeat information already in dates, eligible cards, or participation sections.
    - 🚨 FOCUS ON UNIQUE DETAILS ONLY:
      * Minimum spend thresholds (e.g. "Her 500 TL harcamaya 50 TL Bonus")
      * Maximum earning limits (e.g. "Maksimum 500 TL Bonus")
      * Excluded transaction types (e.g. "Nakit çekim, taksitli işlemler hariç")
      * Bonus loading timeline (e.g. "Bonus 30 gün içinde yüklenir")
    - 🚨 FORMAT: 3-5 concise bullet points. NO long paragraphs.
    - 🚨 AVOID: Repeating dates, card names, or SMS instructions already extracted.
"""
    ,
    'turkiye-finans': """
🚨 TÜRKİYE FİNANS (HAPPY CARD / ÂLÂ KART) SPECIFIC RULES:
- TERMINOLOGY: 
    - "Bonus": Used often for Happy Card (uses Bonus network). 1 Bonus = 1 TL.
    - "ParaPuan": Sometimes used. 1 ParaPuan = 1 TL.
- ELIGIBLE CARDS:
    - 🚨 STRICT: Extract ONLY cards mentioned.
    - Common: "Happy Card", "Happy Zero", "Happy Gold", "Happy Platinum", "Âlâ Kart".
    - "Türkiye Finans Kredi Kartları" = ["Happy Card", "Âlâ Kart"].
- PARTICIPATION:
    - 🚨 PRIORITY ORDER:
      1. SMS: Look for keyword + "2442" (e.g. "KATIL yazıp 2442'ye SMS").
      2. App: "Mobil Şube" or "İnternet Şubesi". Look for "Kampanyalar" menu.
      3. Automatic: ONLY if "otomatik katılım" or if no SMS/App instruction exists AND text implies auto.
    - 🚨 FORMAT: 3-5 concise bullet points.
    """,
    "chippin": """
🚨 CHIPPIN SPECIFIC RULES:
- TERMINOLOGY:
    - "Chippuan": Reward currency. 1 Chippuan = 1 TL.
    - "Nakit İade": Cash back to credit card.
- ELIGIBLE CARDS:
    - Usually "Tüm kredi kartları" or specific bank cards added to Chippin.
- PARTICIPATION:
    - 🚨 PRIORITY ORDER:
      1. App Payment: "Chippin ile ödeme yapmanız gerekmektedir."
      2. QR Code: "Chippin numaranızı söyleyin" or "QR kodunu okutun".
- CONDITIONS:
    - 🚨 CRITICAL: Extract minimum spend, max reward, and specific branch/online restrictions.
    - 🚨 FORMAT: 3-5 concise bullet points.
    """,
    "enpara": """
🚨 ENPARA SPECIFIC RULES:
- TERMINOLOGY: "İade" or "Geri Ödeme" is commonly used. Rewards are usually TL value.
- ELIGIBLE CARDS: 
    - 🚨 STRICT: "Enpara.com Kredi Kartı" or "Enpara Kredi Kartı".
    - 🚨 NOTE: If "Enpara.com Nakit Kart" is mentioned, include it.
- PARTICIPATION:
    - 🚨 PRIORITY: "Ayın Enparalısı". 
    - Almost all campaigns require being "Ayın Enparalısı". 
    - 🚨 FORMAT: If you see "Ayın Enparalısı olmanız yeterli", the participation method is "Ayın Enparalısı olma şartlarını yerine getirin."
    - No SMS or "Katıl" button is typically needed. 
- CONDITIONS:
    - 🚨 🚨 **CRITICAL**: Extract every important point from the specific section "Nelere Dikkat Etmelisiniz".
    - 🚨 FORMAT: 4-6 concise bullet points.
    - Include: Spend limits, dates, "Ayın Enparalısı" requirement, and brand-specific exclusions.
    """,
    "param": """
🚨 PARAM SPECIFIC RULES:
- TERMINOLOGY: "Nakit İade". 
- ELIGIBLE CARDS:
    - 🚨 STRICT: Extract ONLY cards mentioned, typically "ParamKart" or "Param TROY Kart".
- BRANDS & SECTOR:
    - 🚨 CRITICAL: Extract the brand name accurately (e.g., 'Koton', 'Pazarama', 'IKEA') and put it in the `brands` array. Do NOT put 'Param' as a brand.
    - Sector: Pick the correct sector from the valid list based on the brand or general context (e.g., 'Koton' -> 'Giyim & Aksesuar').
- PARTICIPATION:
    - Primary method is typically clicking "Katıl" in "Param Mobil" or checking out with "TROY indirim kodu".
    """,
    "masterpass": """
🚨 MASTERPASS SPECIFIC RULES:
- TERMINOLOGY: "İndirim", "Kupon", "İade". Rewards are usually TL value or Percent.
- ELIGIBLE CARDS: 
    - 🚨 STRICT: Extract ONLY the cards mentioned, typically "Masterpass'e kayıtlı Mastercard", "Maestro kartlar", "Troy kartlar", vb. Do NOT write "Tüm kartlar" unless explicitly stated.
- BRANDS & SECTOR:
    - 🚨 CRITICAL: Extract the brand name accurately (e.g., 'Martı', 'Boyner', 'Uber', 'Getir', 'Galatasaray') and put it in the `brands` array. Do NOT put 'Masterpass' or 'Mastercard' as a brand.
    - Sector: Pick the correct sector from the valid list based on the brand or general context. If it's a sports event, match, or team (like UEFA, Galatasaray), categorize as 'Kültür & Sanat' or 'Eğlence'.
- PARTICIPATION:
    - Look for "Masterpass ile ödeme" or "Masterpass'e kayıtlı kartınızla".
    - Often requires clicking "Kupon Al". Write participation instructions exactly as described.
    """,
    "dunyakatilim": """
🚨 DÜNYA KATILIM SPECIFIC RULES:
- TERMINOLOGY: Rewards are often "İndirim", "Taksit", "Nakit İade" or physical rewards like "Altın". Write exactly what's offered (e.g., "Altın Hediye", "9 Ay Taksit", "%18 Nakit İade").
    - 🚨 CRITICAL: `reward_text` alanı ASLA "Detayları İnceleyin" olmamalıdır. Başlıktan veya içerikten mutlak bir kampanya özeti çıkar.
- SECTOR & BRANDS:
    - 🚨 CRITICAL: If the campaign is about "Altın", "Fiziki Altın", "FX", or Foreign Exchange, classify it as "Kuyum, Optik ve Saat", NEVER "Hizmet".
- ELIGIBLE CARDS:
    - Often "Dünya Katılım Kartı", "DKart Debit" or "Dünya Katılım Ticari Kart". Extract the exact card name mentioned.
- DATES:
    - If the campaign doesn't explicitly mention an end date, or says something like "Süresiz", MUST return null for `end_date`. Do NOT invent 9999-12-31.
    - If `end_date` is given or the campaign is clearly active but `start_date` is not mentioned, use `{current_date}` for `start_date`.
- PARTICIPATION:
    - 🚨 CRITICAL: Look very carefully for SMS instructions (e.g., "TROY boşluk ... yazarak 2345'e SMS gönderilmesi"). If present, extract the exact SMS text.
    - If Mobile/Internet app check-in is required, mention it.
    - If there are no specific participation steps mentioned, output "Otomatik Katılım".
- CONDITIONS:
    - Always generate at least 1-2 bullet points for conditions summarizing the title or text.
    """
}

# ── AI Provider Configuration ──────────────────────────────────────────────
from google import genai as _genai_sdk

_GEMINI_MODEL_NAME = os.getenv("GEMINI_MODEL", "gemini-2.5-flash-lite")
_gemini_key = os.getenv("GEMINI_API_KEY")

if not _gemini_key:
    # try fallback
    for i in range(1, 20):
        k = os.getenv(f"GEMINI_API_KEY_{i}")
        if k:
            _gemini_key = k
            break

if not _gemini_key:
    raise ValueError("No GEMINI_API_KEY found. Set GEMINI_API_KEY in .env")

_gemini_client = _genai_sdk.Client(api_key=_gemini_key)
print(f"[DEBUG] Gemini initialized (model: {_GEMINI_MODEL_NAME}).")
# ────────────────────────────────────────────────────────────────────────────



class AIParser:
    """
    Gemini AI-powered campaign parser.
    Extracts structured data from unstructured campaign text.
    Uses exponential backoff for rate limits.
    """

    def __init__(self, model_name: str = None):
        self._client = _gemini_client
        self.model = None
        print(f"[DEBUG] AIParser using Gemini | model: {_GEMINI_MODEL_NAME}")

    # ── Unified call helper ──────────────────────────────────────────────────
    def _call_ai(self, prompt: str, timeout_sec: int = 65) -> str:
        """Send prompt to active AI provider."""
        import time
        # Small intentional delay to ensure we do not violently hit 1000 RPM instantly across parallel workers
        time.sleep(0.5) 
        response = call_with_timeout(
            self._client.models.generate_content,
            args=(),
            kwargs={
                "model": _GEMINI_MODEL_NAME, 
                "contents": prompt,
                "config": {"temperature": 0.1, "response_mime_type": "application/json"}
            },
            timeout_sec=timeout_sec,
        )
        return response.text.strip()
    # ────────────────────────────────────────────────────────────────────────
        
    def parse_campaign_data(
        self,
        raw_text: str,
        title: str = None,
        bank_name: str = None,
        card_name: str = None
    ) -> Dict[str, Any]:
        """
        Parse campaign data using Gemini AI
        
        Args:
            raw_text: Raw HTML/text from campaign page
            title: Campaign title (optional, helps with context)
            bank_name: Bank name (optional, helps identify cards)
            card_name: Card name (optional, for context)
            
        Returns:
            Dictionary with structured campaign data
        """
        # Clean text
        clean_text = self._clean_text(raw_text)
        
        # Build prompt
        prompt = self._build_prompt(clean_text, datetime.now().strftime("%Y-%m-%d"), bank_name, title)
        
        max_retries = 5
        for attempt in range(max_retries):
            try:
                result_text = self._call_ai(prompt, timeout_sec=65)

                if not result_text:
                    print("   ⚠️ Empty response text.")
                    result_text = "{}"

                # Extract JSON from response
                json_data = self._extract_json(result_text)

                # Validate and normalize
                normalized = self._normalize_data(json_data)

                return normalized

            except Exception as e:
                error_str = str(e)
                if "429" in error_str or "Resource exhausted" in error_str or "rate_limit" in error_str.lower() or "503" in error_str:
                    # Exponential backoff for typical API failures / server congestion
                    wait_time = (attempt + 1) * 3 
                    print(f"   ⚠️ API limit or 503 error. Waiting {wait_time}s... (Attempt {attempt+1}/{max_retries}) | {error_str[:100]}")
                    import time
                    time.sleep(wait_time)
                    continue

                print(f"AI Parser Error: {e}")
                return self._get_fallback_data(title or "")

        print("   ❌ Max retries reached for AI Parser.")
        return self._get_fallback_data(title or "")
    
    def _clean_text(self, text: str) -> str:
        """
        Clean and normalize text before sending to AI.

        Strategy (token optimization):
        0. Remove noisy HTML elements (script, style, footer) if text is raw HTML
        1. Split into lines and drop boilerplate lines:
           - Very short lines (< 40 chars) → likely nav links, breadcrumbs, footer items
           - Lines that look like pure navigation / copyright noise
           - Duplicate lines
        2. Rejoin and apply a tighter character limit (6 000 chars instead of 10 000).

        Expected result: ~50-55 % fewer input tokens with no loss of campaign content.
        """
        if not text:
            return ""

        # ── Step 0: HTML parsing and decomposing ─────────────────────
        try:
            from bs4 import BeautifulSoup
            # Parse as HTML (if it's plain text, soup will just return it safely)
            soup = BeautifulSoup(text, 'html.parser')
            unwanted_tags = ['script', 'style', 'footer', 'nav', 'header', 'noscript', 'meta', 'iframe', 'svg', 'button']
            for tag in soup(unwanted_tags):
                tag.decompose()
            # Extract clean text, separating blocks with newlines
            text = soup.get_text(separator='\n', strip=True)
        except Exception as e:
            print(f"[WARN] BeautifulSoup parsing failed in _clean_text: {e}")

        # ── Step 1: line-level boilerplate filter ────────────────────────────
        # Common Turkish nav/footer noise patterns  (case-insensitive check)
        _NAV_PATTERNS = re.compile(
            r'^(ana sayfa|şubeler|iletişim|bize ulaşın|hakkımızda|kvkk|gizlilik|'
            r'çerez|copyright|tüm hakları|instagram|twitter|facebook|linkedin|'
            r'youtube|bizi takip|duyurular|haberler|aktif kampanya|kampanyalarımız|'
            r'kampanyalar|ürünler|bireysel|kurumsal|faq|sıkça sorulan|yardım|'
            r'site haritası|kariyer|basvuru|başvuru|indir|download|appstore|'
            r'google play|app store|playstore)$',
            re.IGNORECASE
        )

        lines = text.split('\n')
        seen: set = set()
        filtered: list = []
        for line in lines:
            stripped = line.strip()
            # Drop blank or very short lines (probable single menu items)
            if len(stripped) < 40:
                lower = stripped.lower()
                if _NAV_PATTERNS.match(lower) or len(stripped) < 15:
                    continue
            # Drop exact duplicates
            if stripped in seen:
                continue
            seen.add(stripped)
            filtered.append(stripped)

        text = '\n'.join(filtered)

        # ── Step 2: normalise whitespace + remove non-content characters ──────
        text = re.sub(r'[ \t]+', ' ', text)           # collapse spaces/tabs
        text = re.sub(r'\n{3,}', '\n\n', text)         # max 2 blank lines
        text = re.sub(r'[^\w\s\.,;:!?%₺\-/()İıĞğÜüŞşÖöÇç\n]', ' ', text)

        # ── Step 3: tighter length limit (was 10 000) ────────────────────────
        if len(text) > 6000:
            text = text[:6000]

        return text.strip()
    
    def _build_prompt(self, raw_text: str, current_date: str, bank_name: Optional[str], page_title: Optional[str] = None) -> str:
        # 1. Clean Text (Remove boilerplate)
        cleaned_text = clean_campaign_text(raw_text)
        
        # 2. Get Bank Specific Instructions
        bank_instructions = ""
        if bank_name:
            bank_name_lower = bank_name.lower()
            for bank_key, rules in BANK_RULES.items():
                if bank_key in bank_name_lower:
                    bank_instructions = rules
                    break

        # 3. If page h1 title provided, lock it in the prompt
        title_instruction = ""
        if page_title and page_title.strip() and page_title.strip() != "Başlık Yok":
            title_instruction = f"""
🔒 BAŞLIK KILIDI: Bu kampanyanın resmi başlığı sayfadan alındı:
"{page_title.strip()}"
'title' alanına SADECE bu başlığı yaz. Metinden farklı bir başlık TÜRETME. Kısaltabilir veya dilbilgisi düzeltmesi yapabilirsin ama anlamı değiştirme.
"""

        return f"""
Sen uzman bir kampanya analistisin. Aşağıdaki kampanya metnini analiz et ve JSON formatında yapısal veriye dönüştür.
Bugünün tarihi: {current_date} (Yıl: {datetime.now().year})

{bank_instructions}
{title_instruction}

VALID- SECTOR:
    Pick ONE matching sector from this exact dictionary and output ONLY its SLUG:
    {{
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
    }}
    🚨 NOTE: If the campaign is about Sports, Matches, Football, Theatre, or Concerts (e.g., UEFA, Galatasaray, tiyatro, sinema), it MUST be categorized as 'Kültür & Sanat' or 'Eğlence', NOT 'Diğer'.

⭐⭐⭐ KRİTİK KURALLAR (DOKUNULMAZ) ⭐⭐⭐
1. **DİL**: Tamamı TÜRKÇE olmalı.
2. **BRANDS**: Metinde geçen markayı TAM OLARAK al. 
    - 🚨 ÖNEMLİ YASAK: Asla kampanya sahibi bankayı (İş Bankası, Akbank, Garanti vb.) veya kart programını (Maximum, Axess, Bonus, World, Wings vb.) MARKA olarak ekleme. Sadece ortak markayı (ör. Trendyol, Migros, THY) ekle.
    - Bilinmeyen marka varsa UYDURMA, metindeki ismini kullan.
3. **SECTOR**: Yukarıdaki VALID SECTORS listesinden EN UYGUN olanı seç. Asla bu liste dışına çıkma.
4. **MARKETING**: 'description' alanı MUTLAKA 2 cümle olmalı. Samimi ve kullanıcıyı teşvik edici olmalı.
5. **REWARD TEXT (PUNCHY)**: 
    - 'reward_text' kısmına en kısa ve çarpıcı ödülü yaz.
    - "Peşin fiyatına" gibi detayları yazma, sadece "150 TL Puan", "+4 Taksit", "%20 İndirim" yaz.
    - Eğer "100 TL Worldpuan" diyorsa "100 TL Worldpuan" yaz. (Değer + Tür)
6. **CONDITIONS**: 
    - Koşulları **maksimum 6-7 madde** olarak özetle. Uzun yasal metinleri atla.
    - 🚨 İÇER: Minimum harcama eşiği, maksimum kazanç limiti, kampanya dışı işlem/kart türleri.
    - 🚨 KESİN YASAK (REDUNDANCY FILTER): 'start_date', 'end_date', 'cards', 'participation' alanlarında zaten olan bilgiyi BURAYA ASLA TAAŞIMA. 
        * ❌ "Kampanya 1-28 Şubat tarihlerindedir." (Yazma, zaten date alanında var)
        * ❌ "Maximum Kartlar dahildir." (Yazma, zaten cards alanında var)
        * ❌ "Maximum Mobil'den katılabilirsiniz." (Yazma, zaten participation alanında var)
    - Bankaya özel kural varsa (yukarıda belirtilmişse) o kuralı öncelikle uygula.
7. **DATES (KRİTİK)**: 
    - Tüm tarihleri 'YYYY-MM-DD' formatında ver.
    - 🚨 YIL KURALI: Eğer yıl belirtilmemişse:
      * Bugünün tarihi: {current_date} (Yıl: {datetime.now().year}, Ay: {datetime.now().month})
      * Kampanya ayı < Bugünün ayı → Yıl: {datetime.now().year + 1}
      * Kampanya ayı >= Bugünün ayı → Yıl: {datetime.now().year}
      * Örnek 1: Bugün 17 Şubat 2026. "1-28 Şubat" → 2026-02-01 ve 2026-02-28
      * Örnek 2: Bugün 17 Mart 2026. "1-28 Şubat" → 2027-02-01 ve 2027-02-28
    - Sadece bitiş tarihi varsa, başlangıç tarihi olarak bugünü ({current_date}) al.
    - "1-28 Şubat" gibi aralıklar için: 2026-02-01 ve 2026-02-28 (Yılı ekle).

8. **KATILIM (PARTICIPATION)**:
    - Metin içinde "SMS", "Mobil", "Jüzdan", "Katıl" gibi ifadeleri ara.
    - 🚨 DOĞRULAMA: İş Bankası için ASLA "World Mobil" yazma. Metinde "World Mobil" geçse bile (ki bu bir hatadır), bunu "Maximum Mobil" olarak düzelt. Banka kurallarına (yukarıdaki) uy.
    - Varsa tam talimatı yaz: "KAZAN yazıp 4455'e SMS gönderin" veya "Maximum Mobil üzerinden Katıl butonuna tıklayın".
    - Yoksa boş bırakma, "Otomatik Katılım" veya metinde "Kampanya detaylarını inceleyin" diyorsa aynen bunu yaz. Tahmin yürütme.

9. **HARCAMA-KAZANÇ KURALLARI (MATHEMATIC LOGIC)**:
   - **discount**: SADECE "{{"N"}} Taksit" veya "+{{"N"}} Taksit"
   - **reward_text**: 
     - 🚨 YÜZDE + MAX LİMİT KURALI: "%10 (max 200TL)" formatında yaz.
     - 🚨 PUAN: "100 TL Worldpuan" veya "500 Mil".
     - 🚨 İNDİRİM: "200 TL İndirim".
   - **min_spend**: Kampanyadan faydalanmak için (veya belirtilen ödülü kazanmak için) gereken minimum harcama tutarı. (Sayısal)

JSON Formatı:
{{
  "title": "Kısa ve çarpıcı başlık",
  "description": "2 cümlelik pazarlama metni",
  "reward_value": 0.0,
  "reward_type": "puan/indirim/taksit/mil",
  "reward_text": "150 TL Puan",
  "min_spend": 0.0,
  "start_date": "YYYY-MM-DD",
  "end_date": "YYYY-MM-DD",
  "sector": "Sektör Slug'ı",
  "brands": ["Marka1", "Marka2"],
  "cards": ["Kart1", "Kart2"],
  "participation": "Katılım talimatı",
  "conditions": ["Madde 1", "Madde 2"]
}}

ANALİZ EDİLECEK METİN:
"{cleaned_text}"
"""
    
    def _extract_json(self, text: str) -> Dict[str, Any]:
        """Extract JSON from AI response"""
        # Try to find JSON in response
        json_match = re.search(r'\{.*\}', text, re.DOTALL)
        if json_match:
            json_str = json_match.group(0)
            return json.loads(json_str)
        
        # If no JSON found, try parsing entire response
        return json.loads(text)
    
    def _normalize_data(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Normalize and validate parsed data"""
        normalized = {
            "title": data.get("title") or "Kampanya",
            "description": data.get("description") or "",
            "reward_value": self._safe_decimal(data.get("reward_value")),
            "reward_type": data.get("reward_type"),
            "reward_text": data.get("reward_text") or "Detayları İnceleyin",
            "min_spend": self._safe_int(data.get("min_spend")),
            "start_date": self._safe_date(data.get("start_date")),
            "end_date": self._safe_date(data.get("end_date")),
            "sector": data.get("sector") or "Diğer",
            "brands": data.get("brands") or [],
            "cards": data.get("cards") or [],
            "participation": data.get("participation") or "Detayları İnceleyin",
            "conditions": data.get("conditions") or []
        }
        
        return normalized
    
    def _safe_decimal(self, value: Any) -> Optional[float]:
        """Safely convert to decimal"""
        if value is None:
            return None
        try:
            return float(value)
        except (ValueError, TypeError):
            return None
    
    def _safe_int(self, value: Any) -> Optional[int]:
        """Safely convert to integer"""
        if value is None:
            return None
        try:
            return int(value)
        except (ValueError, TypeError):
            return None
    
    def _safe_date(self, value: Any) -> Optional[str]:
        """Safely validate date string"""
        if not value:
            return None
        
        # Check if it's already in YYYY-MM-DD format
        if isinstance(value, str) and re.match(r'^\d{4}-\d{2}-\d{2}$', value):
            return value
        
        return None
    
    def _get_fallback_data(self, title: str) -> Dict[str, Any]:
        """Return fallback data if AI parsing fails — marked with _ai_failed=True"""
        return {
            "_ai_failed": True,         # ← scrapers use this to skip saving
            "title": title or "Kampanya",
            "description": "",
            "reward_value": None,
            "reward_type": None,
            "reward_text": "Detayları İnceleyin",
            "min_spend": None,
            "start_date": None,
            "end_date": None,
            "sector": "Diğer",
            "brands": [],
            "cards": [],
            "participation": "Detayları İnceleyin",
            "conditions": []
        }


# Singleton instance
_parser_instance = None


def get_ai_parser() -> AIParser:
    """Get singleton AI parser instance"""
    global _parser_instance
    if _parser_instance is None:
        _parser_instance = AIParser()
    return _parser_instance


def parse_campaign_data(
    raw_text: str,
    title: str = None,
    bank_name: str = None,
    card_name: str = None
) -> Dict[str, Any]:
    """
    Convenience function to parse campaign data (full HTML mode)
    """
    parser = get_ai_parser()
    return parser.parse_campaign_data(raw_text, title, bank_name, card_name)


def parse_api_campaign(
    title: str,
    short_description: str,
    content_html: str,
    bank_name: str = None,
    scraper_sector: Optional[str] = None
) -> Dict[str, Any]:
    """
    API-First Lightweight Parser.
    Takes structured data from bank APIs (title, description, content)
    and only asks Gemini for what the API doesn't provide:
    reward_value, reward_type, reward_text, sector, brands, conditions, cards, participation.
    
    Args:
        scraper_sector: Optional sector hint from bank website/API (will be mapped to our 18 sectors)
    
    Token usage: ~200-300 tokens (vs ~4000 for full HTML mode)
    """
    parser = get_ai_parser()
    
    # Clean HTML tags from content to get plain text conditions
    import re as _re
    clean_content = _re.sub(r'<[^>]+>', '\n', content_html or '')
    clean_content = _re.sub(r'\n+', '\n', clean_content).strip()
    # Limit content length
    # For Garanti BBVA, we need more context (sidebar info often gets cut off)
    # User requested no limit for Garanti
    limit = 25000 if bank_name == "Garanti BBVA" else 6000
    
    if len(clean_content) > limit:
        clean_content = clean_content[:limit]
    
    # Get bank-specific rules
    bank_instructions = ""
    if bank_name:
        bank_name_lower = bank_name.lower()
        for bank_key, rules in BANK_RULES.items():
            if bank_key in bank_name_lower:
                bank_instructions = rules
                break
    
    today = datetime.now()
    current_date = today.strftime("%Y-%m-%d")
    
    # Add scraper sector hint if available
    sector_hint = ""
    if scraper_sector and scraper_sector.strip():
        sector_hint = f"""
🎯 SEKTÖR İPUCU (Banka Sitesinden):
Banka bu kampanyayı "{scraper_sector}" kategorisinde gösteriyor.
Bu ipucunu kullanarak aşağıdaki VALID SECTORS listesinden EN UYGUN olanı seç.
"""
    
    prompt = f"""Sen uzman bir kampanya analistisin. Aşağıdaki kampanya bilgilerini analiz et.
Bugünün tarihi: {current_date} (Yıl: {today.year})

{bank_instructions}

{sector_hint}

VALID SECTORS (BİRİNİ SEÇ — SADECE bu listeden, PARANTEZ İÇİNDEKİLERİ YAZMA):
- Market & Gıda
- Akaryakıt
- Giyim & Aksesuar
- Restoran & Kafe
- Elektronik
- Mobilya & Dekorasyon
- Kozmetik & Sağlık
- E-Ticaret
- Ulaşım
- Dijital Platform
- Kültür & Sanat
- Eğitim
- Sigorta
- Otomotiv
- Vergi & Kamu
- Turizm & Konaklama
- Kuyum, Optik ve Saat
- Diğer

⚠️ ÖNEMLİ: Sektör ismini AYNEN yukarıdaki listeden seç. Parantez içindeki açıklamaları YAZMA!
   ✅ DOĞRU: "Restoran & Kafe"
   ❌ YANLIŞ: "Restoran & Kafe (Fast Food, Yemek Siparişi)"


KURALLAR:
1. short_title: Başlığı KISA ve ÇARPICI hale getir. Kartlarda 2 satır dolduracak uzunlukta (40-70 karakter).
   ❌ Çok kısa: "Market Fırsatı" (1 satır)
   ✅ İdeal: "Market Alışverişinde 300 TL'ye Varan Puan!" (2 satır)
   ❌ Çok uzun: "Yapı Kredi Play ile her 300 TL ve üzeri market alışverişlerinde 60 TL puan" (3+ satır)
2. description: 2 cümlelik, samimi ve teşvik edici pazarlama metni. Kullanıcıyı kampanyaya katılmaya ikna etmeli.
3. reward_value: Sayısal değer. "75 TL" → 75.0, "%20" → 20.0
4. reward_type: "puan", "indirim", "taksit", veya "mil"
5. reward_text: Kısa ve çarpıcı. "75 TL Worldpuan", "%20 İndirim", "300 TL'ye Varan Puan"
6. sector: VALID SECTORS listesinden seç.
7. brands: Metinde geçen marka isimlerini çıkar. Yoksa boş liste.
8. conditions: Koşulları kısa maddeler halinde özetle (max 5 madde). ⚠️ ÖNEMLİ: "Geçerli Kartlar" bilgisini buraya YAZMA, çünkü ayrı bir alanda (cards) tutuyoruz.
9. cards: Hangi kartlarla geçerli? Metinde belirtilen kartları listele.
10. participation: 🚨 KRİTİK — Detay İçerik'te "SMS", "4454", "Mobil", "Katıl", "Jüzdan", "World Mobil" gibi ifadeleri ARA.
   - SMS varsa: "KEYWORD yazıp NUMARA'ya SMS gönderin" formatında yaz.
   - Mobil uygulama varsa: "World Mobil uygulamasından Kampanyalar bölümünde Katıl butonuna tıklayın" yaz.
   - Her ikisi de varsa: "World Mobil'den Katıl butonuna tıklayın veya KEYWORD yazıp NUMARA'ya SMS gönderin" yaz.
   - Hiçbiri yoksa: "Otomatik katılım" yaz.
10. dates: Metinde geçen başlangıç ve bitiş tarihlerini bul. Format: "YYYY-MM-DD". Bulamazsan null yap.

KAMPANYA BİLGİLERİ:
Başlık: "{title}"
Açıklama: "{short_description}"
Detay İçerik:
{clean_content}

JSON olarak cevap ver:
{{
  "short_title": "40-70 karakter kısa başlık",
  "description": "2 cümlelik pazarlama metni",
  "reward_value": 0.0,
  "reward_type": "puan/indirim/taksit/mil",
  "reward_text": "Kısa ödül metni",
  "sector": "Sektör",
  "brands": [],
  "conditions": ["Madde 1", "Madde 2"],
  "cards": ["Kart1"],
  "participation": "Katılım talimatı",
  "start_date": "YYYY-MM-DD",
  "end_date": "YYYY-MM-DD"
}}}}"""
    
    try:
        result_text = parser._call_ai(prompt, timeout_sec=65)
        json_data = parser._extract_json(result_text)
        
        return {
            "short_title": json_data.get("short_title") or title,
            "description": json_data.get("description") or short_description,
            "reward_value": parser._safe_decimal(json_data.get("reward_value")),
            "reward_type": json_data.get("reward_type"),
            "reward_text": json_data.get("reward_text") or "Detayları İnceleyin",
            "sector": json_data.get("sector") or "Diğer",
            "brands": json_data.get("brands") or [],
            "conditions": json_data.get("conditions") or [],
            "cards": json_data.get("cards") or [],
            "participation": json_data.get("participation") or "Detayları İnceleyin",
            "start_date": parser._safe_date(json_data.get("start_date")),
            "end_date": parser._safe_date(json_data.get("end_date"))
        }
    except Exception as e:
        print(f"API Parser Error: {e}")
        return {
            "_ai_failed": True,
            "title": title,
            "short_title": title,
            "description": short_description,
            "reward_value": None,
            "reward_type": None,
            "reward_text": "Detayları İnceleyin",
            "sector": "Diğer",
            "brands": [],
            "conditions": [],
            "cards": [],
            "participation": "Detayları İnceleyin",
            "start_date": None,
            "end_date": None
        }
