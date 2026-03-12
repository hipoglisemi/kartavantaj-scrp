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
- PARTICIPATION: Primary method is "Jüzdan" app. Always look for "Jüzdan'dan Hemen Katıl" button. If not found, look for "Akbank Axess POS" instructions.
- SMS: Usually 4566. SMS keyword is usually a single word (e.g., "A101", "TEKNOSA").
- REWARD: If it says "8 aya varan taksit", it's an installment campaign. Earning: "Taksit İmkanı". 🚨 ASLA "Detayları İnceleyin" yazma.
- ELIGIBLE CARDS:
    - 🚨 TITLE TRAP: Even if title says "Axess'e Özel", check footer for "Axess, Wings, Free... dahildir".
    - ❌ KESİN YASAK: Asla "Kampanyaya Dahil Kartlar" yazma. Eğer kart listesi bulamazsan alanı BOŞ BIRAK.
    - "Ticari kartlar" / "Business" / "KOBİ" = ["Axess Business", "Wings Business"].
    - "Bank’O Card Axess" = ["Bank’O Card Axess"].
    - "Akbank Kart" / "Bankamatik" = ["Akbank Kart"].
    - If it says "tüm Akbank kredi kartları", list all relevant consumer cards.
    - ⚠️ KESİN YASAK: Kart isimlerini asla 'conditions' (koşullar) listesine yazma. Sadece 'cards' alanına yaz.
- 🚨 AKBANK REDUNDANCY ALERT (CRITICAL):
    - Akbank metinleri tarih ve kart bilgisini çok tekrar eder. 
    - 'conditions' listesine ASLA "1-31 Mart", "Axess kart", "Jüzdan" gibi bilgileri yazma.
    - Koşullar SADECE teknik kurallar içermeli (örn: "POS terminali zorunluluğu", "İndirim limiti").
- PARTICIPATION (REDUNDANCY):
    - 🚨 YASAK: "Juzdan uygulama üzerinden katılabilirsiniz." gibi jenerik metinleri tek başına yazma. Eğer butonda "Hemen Katıl" yazıyorsa "Juzdan'dan Hemen Katıl butonuna tıklayın" gibi somutlaştır.
""",
    'yapı kredi': """
🚨 YAPI KREDI (WORLD) SPECIFIC RULES:
- TERMINOLOGY: "Worldpuan" is the currency.
    - ⚠️ IMPORTANT: "TL Worldpuan" means the value is in TL. If it says "100 TL Worldpuan", earning is "100 TL Worldpuan".
- ELIGIBLE CARDS:
    - Keywords: "Yapı Kredi Kredi Kartları", "Worldcard", "Opet Worldcard", "Gold", "Platinum", "Business", "World Eko", "Play".
- PARTICIPATION:
    - "World Mobil" or "Yapı Kredi Mobil" is the primary method.
- 🚨 REDUNDANCY ALERT: DO NOT repeat card names or dates in 'conditions'.
""",
    'garanti': """
🚨 GARANTI BBVA / BONUS / MILES&SMILES SPECIFIC RULES:
- TERMINOLOGY: "Bonus" (Bonus/Flexi), "Mil" (Miles&Smiles/Shop&Fly).
- ELIGIBLE CARDS (cards):
    - 🚨 STRICT EXTRACTION: Metindeki kart isimlerini TAM olarak çıkar.
    - Miles&Smiles: "Miles & Smiles Garanti BBVA", "Miles & Smiles Garanti BBVA Business".
    - Shop&Fly: "Shop&Fly", "Shop&Fly Business".
    - Bonus: "Bonus", "Bonus Gold", "Bonus Platinum", "Bonus American Express", "Bonus Business", "Bonus Genç", "Bonus Flexi", "Paracard Bonus".
    - ❌ YASAK: "Kampanyaya Dahil Kartlar" gibi başlıkları ASLA kart listesine yazma. Sadece kartın kendi ismini yaz.
- PARTICIPATION: "BonusFlaş" app is primary. Look for "HEMEN KATIL" instructions.
- 🚨 REDUNDANCY ALERT: DO NOT repeat card names, dates, or participation methods (e.g., BonusFlaş) in 'conditions'.
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
- 🚨 REDUNDANCY ALERT: DO NOT repeat card names, dates, or participation methods (e.g., Maximum Mobil, İşCep, Pazarama) in 'conditions'.
- CONDITIONS (SUMMARY MODE):
    - ✔️ ÖZETLE: Maksimum 5-6 madde. Uzun yasal metinleri, tekrar eden kart bilgilerini ve işlem türü sayımlarını atlat.
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
""",
    'kuveyt türk': """
🚨 KUVEYT TÜRK (SAĞLAM KART) SPECIFIC RULES:
- TERMINOLOGY: "Altın Puan". 1 Altın Puan = 1 TL.
- ELIGIBLE CARDS (cards):
    - 🚨 STRICT: Extract all cards from the text (usually the 2nd bullet point in details).
    - Keywords: "Sağlam Kart", "Sağlam Kart Kampüs", "Sağlam Kart Genç", "Miles & Smiles Kuveyt Türk Kredi Kartı", "Özel Bankacılık World Elite Kart", "Tüzel Kartlar".
    - Include "sanal ve ek kartlar" if mentioned.
- PARTICIPATION (participation):
    - 🚨 PRIORITY: Check for SMS keywords (e.g. "KATIL TROYRAMAZAN") and the short number (e.g. 2044).
    - If "otomatik" or "katılım gerektirmez" is mentioned, use "Kampanya otomatik katılımlıdır."
- CONDITIONS (conditions):
    - 🚨 DETAYLI AMA NET: 'KOŞULLAR VE DETAYLAR' başlığı altındaki kritik maddeleri al.
    - 🚨 TEMİZLİK: Tarih, kart listesi ve katılım yöntemini BURADA TEKRARLAMA. Sadece harcama sınırları, sektör kısıtlamaları ve hak kazanım detaylarını yaz.
    - Minimum harcama (1.250 TL), maksimum ödül (250 TL) gibi kritik sınırları MUTLAKA dahil et.
""",
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
    """,
    'turkcell': """
🚨 TURKCELL SPECIFIC RULES:
- PARTICIPATION: Details are usually hidden in accordions.
    - 🚨 PRIORITY: Look for keywords like "Katılım Kriterleri", "Nasıl Faydalanırım", "Diğer Satın Alma Seçenekleri", "Kampanya Detayları".
    - If headers contain these, their content is the MOST IMPORTANT for the 'participation' field.
    - If the text mentions "Uygulama üzerinden", "Şifre al", "Paycell", extract these exact steps.
- ELIGIBLE CARDS: Common values: "Tüm Turkcell Müşterileri", "Paycell Kart Sahipleri", "Turkcell Pasaj Müşterileri".
- BRAND: Identify the partner brand (e.g., Obilet, Sigortam.net, Uber) clearly.
"""
}

# ── AI Provider Configuration ──────────────────────────────────────────────
from google.genai import types
from src.utils.gemini_client import get_gemini_client, generate_with_rotation

_GEMINI_MODEL_NAME = os.getenv("GEMINI_MODEL", "gemini-3.1-flash-lite-preview")
try:
    _gemini_client = get_gemini_client()
    print(f"[DEBUG] Gemini AI initialized via gemini_client module (Model: {_GEMINI_MODEL_NAME}).")
except Exception as e:
    print(f"[WARN] Gemini client init failed: {e}")
    _gemini_client = None
# ────────────────────────────────────────────────────────────────────────────


class AIParser:
    """
    Gemini AI-powered campaign parser.
    Extracts structured data from unstructured campaign text.
    Uses exponential backoff for rate limits and rotates keys.
    """

    def __init__(self, model_name: str = None):
        self._client = _gemini_client
        self.model = None
        print(f"[DEBUG] AIParser using Gemini | model: {_GEMINI_MODEL_NAME}")

    # ── Unified call helper ──────────────────────────────────────────────────
    def _call_ai(self, prompt: str, timeout_sec: int = 65) -> str:
        """Send prompt to active AI provider."""
        import time
        # Intentional delay to avoid violent RPM spikes across workers
        time.sleep(1.0) 
        
        # Token optimization settings (AI Studio web settings do NOT apply to raw API keys)
        config = types.GenerateContentConfig(
            temperature=0.0,
            top_p=0.1,
            top_k=1,
            response_mime_type="application/json",
            max_output_tokens=6000
        )

        return call_with_timeout(
            generate_with_rotation,
            kwargs={
                "prompt": prompt,
                "model": _GEMINI_MODEL_NAME, 
                "config": config
            },
            timeout_sec=timeout_sec,
        )
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
                
                # INJECT cleaned text into the result dictionary for scrapers to save to DB
                normalized["_clean_text"] = clean_text

                return normalized

            except Exception as e:
                error_str = str(e)
                if "429" in error_str or "Resource exhausted" in error_str or "rate_limit" in error_str.lower() or "503" in error_str:
                    # Key rotation is natively handled by gemini_client. If we drop here, ALL keys failed.
                    wait_time = (attempt + 1) * 3 
                    print(f"   ⚠️ API limit across all keys or 503 error. Waiting {wait_time}s... (Attempt {attempt+1}/{max_retries}) | {error_str[:100]}")
                    import time
                    time.sleep(wait_time)
                    continue

                print(f"AI Parser Error: {e}")
                fallback = self._get_fallback_data(title or "")
                fallback["_clean_text"] = clean_text
                return fallback

        print("   ❌ Max retries reached for AI Parser.")
        fallback = self._get_fallback_data(title or "")
        fallback["_clean_text"] = clean_text  # Inject to save even if AI fails
        return fallback
    
    def _clean_text(self, text: str) -> str:
        """
        Clean and normalize text before sending to AI.
        Relaxed strategy to prevent stripping critical reward/participation data.
        """
        if not text:
            return ""

        # ── Step 0: HTML parsing and decomposing ─────────────────────
        try:
            from bs4 import BeautifulSoup
            soup = BeautifulSoup(text, 'html.parser')
            # Keeping 'button' and 'a' text as they often contain participation triggers
            unwanted_tags = ['script', 'style', 'footer', 'nav', 'header', 'noscript', 'meta', 'iframe', 'svg']
            for tag in soup(unwanted_tags):
                tag.decompose()
            text = soup.get_text(separator='\n', strip=True)
        except Exception as e:
            print(f"[WARN] BeautifulSoup parsing failed in _clean_text: {e}")

        # ── Step 1: line-level boilerplate filter ────────────────────────────
        _NAV_PATTERNS = re.compile(
            r'^(ana sayfa|şubeler|iletişim|bize ulaşın|hakkımızda|kvkk|gizlilik|'
            r'çerez|copyright|tüm hakları|instagram|twitter|facebook|linkedin|'
            r'youtube|bizi takip|site haritası|kariyer|başvuru|indir|download)$',
            re.IGNORECASE
        )

        lines = text.split('\n')
        seen: set = set()
        filtered: list = []
        for line in lines:
            stripped = line.strip()
            # Relaxed length check: Keep anything over 5 chars (e.g. "100 TL", "SMS")
            if len(stripped) < 40:
                lower = stripped.lower()
                if _NAV_PATTERNS.match(lower) or len(stripped) < 5:
                    continue
            # Drop exact duplicates to save tokens
            if stripped in seen:
                continue
            seen.add(stripped)
            filtered.append(stripped)

        text = '\n'.join(filtered)

        # ── Step 2: normalise whitespace ────────────────────────────
        text = re.sub(r'[ \t]+', ' ', text)
        text = re.sub(r'\n{3,}', '\n\n', text)
        text = re.sub(r'[^\w\s\.,;:!?%₺\-/()İıĞğÜüŞşÖöÇç\n]', ' ', text)

        # ── Step 3: Length limit (reverting to a safer 8000) ──────────
        if len(text) > 8000:
            text = text[:8000]

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

VALID- SECTOR (CRITICAL):
    Valid Sectors for Validation:
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
    🚨 NOTE: If the campaign is about Sports, Matches, Football, Theatre, or Concerts (e.g., UEFA, Galatasaray, tiyatro, sinema), it MUST be categorized as 'kultur-sanat', NOT 'diger'.
    🚨 NOTE: If the campaign is about "yeni müşteri" (new customer), "kredi kartı başvurusu" (credit card application), "ihtiyaç kredisi" (loan) or any banking/financial product sale, you MUST categorize it as 'finans-yatirim'.
    🚨 SECTOR OUTPUT RULE: Your JSON `"sector"` value must ONLY be one of the slugs above (e.g. "market-gida", NOT "Market & Gıda").

⭐⭐⭐ KRİTİK KURALLAR (DOKUNULMAZ) ⭐⭐⭐
1. **DİL**: Tamamı TÜRKÇE olmalı.
2. **BRANDS**: Metinde geçen markayı TAM OLARAK al. 
    - 🚨 ÖNEMLİ YASAK: Asla kampanya sahibi bankayı (İş Bankası, Akbank, Garanti vb.) veya kart programını (Maximum, Axess, Bonus, World, Wings vb.) MARKA olarak ekleme. Sadece ortak markayı (ör. Trendyol, Migros, THY) ekle.
    - 🚨 FORMAT KURALI: Marka veya kart isimlerini asla "P, a, r, a, f" veya "A, x, e, s, s" gibi her harfi virgülle ayrılmış şekilde yazma. Sadece tam ve okunabilir ismi yaz ("Paraf", "Axess").
    - Bilinmeyen marka varsa UYDURMA, metindeki ismini kullan.
3. **SECTOR**: Yukarıdaki VALID SECTORS listesinden EN UYGUN olanı seç. Asla bu liste dışına çıkma.
4. **MARKETING**: 'description' alanı MUTLAKA 2 cümle olmalı. Samimi ve kullanıcıyı teşvik edici olmalı.
    - 🚨 KESİN YASAK: 'description' alanına tarih, kart veya katılım bilgisi ASLA EKLEME.
5. **REWARD TEXT (PUNCHY)**: 
    - 'reward_text' kısmına en kısa ve çarpıcı ödülü yaz.
    - "Peşin fiyatına" gibi detayları yazma, sadece "150 TL Puan", "+4 Taksit", "%20 İndirim" yaz.
    - Eğer "100 TL Worldpuan" diyorsa "100 TL Worldpuan" yaz. (Değer + Tür)
6. **CONDITIONS (STRICT REDUNDANCY & BOILERPLATE REMOVAL)**: 
    - 🚨 🚨 **YASAK**: Aşağıdaki alanlarda zaten olan bilgileri 'conditions' içine yazmak KESİNLİKLE YASAKTIR:
        - 'start_date' ve 'end_date' (Örn: "Şubat ayı boyunca" yazma!)
        - 'cards' (Örn: "Axess sahipleri" yazma!)
        - 'participation' (Örn: "Jüzdan'dan katılın" yazma!)
        - 'title' (Başlıkta olan bilgiyi tekrarlama!)
    - 🚨 **JURIDICAL BOILERPLATE REMOVAL (ULTRA STRICT)**: Aşağıdaki jenerik metinleri KESİNLİKLE SİL:
        - "Taksit sayısı ürün gruplarına göre yasal mevzuat çerçevesinde belirlenir."
        - "Bireysel kredi kartlarıyla gerçekleştirilecek basılı ve külçe altın, kuyum, telekomünikasyon, akaryakıt, yemek, gıda, kozmetik vb. harcamalarda taksit uygulanamaz."
        - "Yasal mevzuat gereği azami taksit sayısı..."
        - "Kampanya farklı kampanyalarla birleştirilemez."
    - ✅ SADECE SADECE KAMPANYAYA ÖZEL ŞARTLARI YAZ: "Maksimum 500 TL", "Harcama alt sınırı 2000 TL", "İade/İptal hariçtir".
    - Eğer tüm sayfa içeriği zaten bu 4 alanda varsa 'conditions' boş (boş liste) olabilir. Gereksiz kalabalık yapma.

7. **DATES**: 
    - Tüm tarihleri 'YYYY-MM-DD' formatında ver.
    - 🚨 YIL KURALI: Eğer yıl belirtilmemişse:
      * Bugünün tarihi: {current_date} (Yıl: {datetime.now().year}, Ay: {datetime.now().month})
      * Kampanya ayı < Bugünün ayı → Yıl: {datetime.now().year + 1}
      * Kampanya ayı >= Bugünün ayı → Yıl: {datetime.now().year}
    - Sadece bitiş tarihi varsa, başlangıç tarihi olarak bugünü ({current_date}) al.

8. **KATILIM (PARTICIPATION)**: 
    - 🚨 KRİTİK: SMS, Mobil, Uygulama, Katıl, Gönder gibi teknik katılım mekanizmalarını ara.
    - 🚨 ULTRA YASAK: "Hemen faydalanabilirsiniz", "Detayları inceleyin", "Mobil uygulama üzerinden katılabilirsiniz" gibi anlamsız/jenerik metinleri ASLA yazma.
    - Bulamadığında bankanın mobil uygulaması üzerinden katılımı vurgula (Örn: "BonusFlaş üzerinden Hemen Katıl butonuna tıklayarak katılın").
    - 🚨 ÖZEL: Eğer katılım için "Rezervasyon", "Axess POS terminali" gibi teknik bir şart varsa bunu 'participation' alanına yaz.
    - 🚨 DOĞRULAMA: İş Bankası için ASLA "World Mobil" yazma, "Maximum Mobil" olarak düzelt. Akbank için "Jüzdan", Garanti için "BonusFlaş", Yapı Kredi için "World Mobil" ifadelerini doğrula.
    - Varsa tam talimatı yaz: "KAZAN yazıp 4455'e SMS gönderin" veya "Maximum Mobil üzerinden Hemen Katıl butonuna tıklayın".
    - Yoksa ve metinde teknik bir detay bulunamıyorsa; bankanın mobil uygulaması üzerinden katılımı vurgula (Örn: "BonusFlaş üzerinden katılabilirsiniz").

9. **REWARD_TEXT**: 
    - 🚨 ASLA YAZMA: "Detayları İnceleyin", "Hemen Faydalanın" gibi jenerik ifadeler yasaktır. 
    - 🚨 SOURCE PRIORITY: Ödül metin içinde yoksa MUTLAKA BAŞLIKTAN (TITLE) çıkar (Örn: "3 Taksit", "%20 İndirim"). 
    - Hiçbir somut değer bulamazsan "Kampanya Fırsatı" yaz.

10. **PAZARLAMA ÖZETİ (MARKETING TEXT)**:
    - 'ai_marketing_text' alanı için: Kampanyanın avantajını özetleyen, kullanıcıyı tıklamaya teşvik eden, emojisiz, samimi ve kısa bir cümle oluştur. (Örn: "Market harcamalarınızda 500 TL'ye varan puan kazanma fırsatını kaçırmayın!")
    - Max 120 karakter.

11. **HARCAMA-KAZANÇ KURALLARI (MATHEMATIC LOGIC)**:
    - **discount**: SADECE "{{N}} Taksit" veya "+{{N}} Taksit"
    - **reward_text**: 
      - 🚨 YÜZDE + MAX LİMİT KURALI: "%10 (max 200TL)" formatında yaz.
      - 🚨 PUAN: "100 TL Worldpuan" veya "500 Mil".
      - 🚨 İNDİRİM: "200 TL İndirim".
      - 🚨 ULTRA YASAK: "Detayları İnceleyin", "Hemen Faydalanın", "Kampanyaya Dahil Kartlar" gibi jenerik ifadeler yasaktır. 
      - Metinde veya Başlıkta kampanya ödülü neyse onu yaz. Hiç bulamazsan ödülü "Kampanya Fırsatı" olarak belirt ama jenerik ibare kullanma. Bulunamayan her alanı BOŞ/NULL bırak, uydurma metin yazma.
    - **min_spend**: Kampanyadan faydalanmak için gereken minimum harcama tutarı. (Sayısal)

JSON Formatı:
{{
  "title": "Kısa ve çarpıcı başlık",
  "description": "2 cümlelik detaylı açıklama metni",
  "ai_marketing_text": "Kısa ve davetkar pazarlama özeti",
  "reward_value": 0.0,
  "reward_type": "puan/indirim/taksit/mil",
  "reward_text": "150 TL Puan",
  "min_spend": 0.0,
  "start_date": "YYYY-MM-DD",
  "end_date": "YYYY-MM-DD",
  "sector": "Sektör Slug'ı",
  "brands": ["Marka1", "Marka2"],
  "cards": ["Kart1", "Kart2"],
  "participation": "Katılım talimatı (SMS/App)",
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
        
        def _to_clean_string(val: Any, separator: str = "\n") -> str:
            if not val: return ""
            if isinstance(val, list):
                # Filter out empty/nulls and join with specified separator
                items = [str(x).strip() for x in val if x]
                return separator.join(items) if len(items) > 1 else (items[0] if items else "")
            return str(val).strip()

        def _to_clean_list(val: Any) -> list:
            """Always return a list. If val is already a list, clean it. If string, wrap in list."""
            if not val:
                return []
            if isinstance(val, list):
                return [str(x).strip() for x in val if x and str(x).strip()]
            # val is a string — wrap as single-item list (do NOT join characters!)
            cleaned = str(val).strip()
            return [cleaned] if cleaned else []

        normalized = {
            "title": data.get("title") or "Kampanya",
            "description": data.get("description") or "",
            "ai_marketing_text": data.get("ai_marketing_text") or "",
            "reward_value": self._safe_decimal(data.get("reward_value")),
            "reward_type": data.get("reward_type"),
            "reward_text": data.get("reward_text") or "Kampanya Fırsatı",
            "min_spend": self._safe_int(data.get("min_spend")),
            "start_date": self._safe_date(data.get("start_date")),
            "end_date": self._safe_date(data.get("end_date")),
            "sector": data.get("sector") or "Diğer",
            "brands": data.get("brands") or [],
            "cards": _to_clean_list(data.get("cards")),
            "participation": _to_clean_string(data.get("participation")),
            "conditions": _to_clean_list(data.get("conditions"))
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
        
    clean_text = clean_content
    
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
