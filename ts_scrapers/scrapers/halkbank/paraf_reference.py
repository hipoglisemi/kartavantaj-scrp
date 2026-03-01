import time
import json
import re
import math
from datetime import datetime
from urllib.parse import urljoin
from bs4 import BeautifulSoup
from concurrent.futures import ThreadPoolExecutor

# --- GEREKLİ KÜTÜPHANELER ---
from selenium import webdriver 
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.common.action_chains import ActionChains
from webdriver_manager.chrome import ChromeDriverManager

# --- AYARLAR ---
BASE_URL = "https://www.paraf.com.tr"
START_URL = "https://www.paraf.com.tr/tr/kampanyalar.html"
OUTPUT_FILE = "paraf_restored_v25.json" # Final sürüm
IMPORT_SOURCE_NAME = "Halkbank Paraf"
WORKER_COUNT = 4 

# --- YARDIMCI FONKSİYONLAR ---

def tr_lower(text):
    return text.replace('I', 'ı').replace('İ', 'i').lower()

def temizle_metin(text):
    if not text: return ""
    text = text.replace('\n', ' ').replace('\r', '')
    text = re.sub(r'\s+', ' ', text).strip()
    text = re.sub(r'^(Kampanya Detayları|Katılım Koşulları)[:\s-]*', '', text, flags=re.IGNORECASE)
    return text.strip(' ,.:;-')

def format_rakam(rakam_int):
    if rakam_int is None: return None
    try: 
        if isinstance(rakam_int, str):
            rakam_int = int(re.sub(r'[^\d]', '', rakam_int))
        return f"{int(rakam_int):,}".replace(",", ".")
    except: return None

def format_tarih_iso(tarih_str, is_end=False):
    if not tarih_str: return None
    ts = tr_lower(tarih_str)
    aylar = {'ocak':'01','şubat':'02','mart':'03','nisan':'04','mayıs':'05','haziran':'06',
             'temmuz':'07','ağustos':'08','eylül':'09','ekim':'10','kasım':'11','aralık':'12'}
    
    try:
        y_match = re.search(r'(202[5-9])', ts)
        year = y_match.group(1) if y_match else str(datetime.now().year)
        current_year = str(datetime.now().year)

        # 1. Tam Aralık
        m_full = re.search(r'(\d{1,2})\s+([a-zğüşıöç]+)\s+(\d{4})\s*[-–]\s*(\d{1,2})\s+([a-zğüşıöç]+)\s+(\d{4})', ts)
        if m_full:
            g1, a1, y1, g2, a2, y2 = m_full.groups()
            if is_end: return f"{y2}-{aylar.get(a2,'12')}-{str(g2).zfill(2)}T23:59:59Z"
            else: return f"{y1}-{aylar.get(a1,'01')}-{str(g1).zfill(2)}T00:00:00Z"

        # 2. Tek Yıl Aralık
        m_range = re.search(r'(\d{1,2})\s*-\s*(\d{1,2})\s*([a-zğüşıöç]+)\s*(\d{4})', ts)
        if m_range:
            g1, g2, ay, yil = m_range.groups()
            if is_end: return f"{yil}-{aylar.get(ay,'12')}-{str(g2).zfill(2)}T23:59:59Z"
            else: return f"{yil}-{aylar.get(ay,'01')}-{str(g1).zfill(2)}T00:00:00Z"

        # 3. Yılsız Aralık
        m_noyear = re.search(r'(\d{1,2})\s*-\s*(\d{1,2})\s*([a-zğüşıöç]+)', ts)
        if m_noyear:
            g1, g2, ay = m_noyear.groups()
            if is_end: return f"{current_year}-{aylar.get(ay,'12')}-{str(g2).zfill(2)}T23:59:59Z"
            else: return f"{current_year}-{aylar.get(ay,'01')}-{str(g1).zfill(2)}T00:00:00Z"

        # 4. Tek Tarih (Bitiş)
        m_single = re.search(r'(\d{1,2})\s+([a-zğüşıöç]+)\s+(\d{4})', ts)
        if m_single:
            g, ay, yil = m_single.groups()
            if is_end: return f"{yil}-{aylar.get(ay,'12')}-{str(g).zfill(2)}T23:59:59Z"
            else: return datetime.now().strftime("%Y-%m-%dT00:00:00Z")

        return None
    except: return None

def extract_dates(text): 
    return format_tarih_iso(text, False), format_tarih_iso(text, True)

def get_category(text, title):
    t = tr_lower(title + " " + text)
    def check(keyword):
        if keyword in t:
            idx = t.find(keyword)
            context = t[idx:idx+150] 
            if "hariç" in context or "geçerli değil" in context:
                if 'taksit' in t: return False
                return False
            return True
        return False
    
    if "vergi" in t or "emlak" in t or "fatura" in t or "sgk" in t or "sigorta" in t: return "Diğer" 
    if check("akaryakıt") or check("benzin") or check("otogaz") or "moil" in t or "totalenergies" in t: return "Yakıt"
    if check("eğitim") or check("okul") or check("üniversite") or "kırtasiye" in t: return "Eğitim"
    if check("sağlık") or check("eczane") or check("poliklinik") or "güzellik hizmetleri" in t: return "Sağlık" 
    if any(x in t for x in ["trendyol","amazon","hepsiburada","n11","pazarama","e-ticaret"]): return "Online Alışveriş"
    if check("seyahat") or check("otel") or check("tur") or "paraflytravel" in t or "gezinomi" in t or "raffles" in t or "prontotour" in t: return "Seyahat"
    if check("elektronik") or check("bilgisayar") or check("beyaz eşya") or "vestel" in t or "miele" in t or "dyson" in t: return "Elektronik"
    if check("restoran") or check("kafe") or check("yemek") or "bigchefs" in t or "ranchero" in t: return "Restoran & Kafe"
    if check("giyim") or check("kozmetik") or check("saat") or "network" in t: return "Giyim & Moda"
    if check("market") or check("gıda"): return "Market"
    return "Diğer"

# --- FİNANSAL MOTOR V25 (Gelişmiş Döngüsel Algılama) ---
def extract_financials_v25(text, title):
    t_low = tr_lower(text) 
    title_low = tr_lower(title)
    
    min_s, max_d, earn, disc = 0, 0, None, None
    
    # 1. Taksit
    if any(x in title_low for x in ["taksit", "erteleme", "faizsiz"]):
        tm = re.findall(r'(\d+)\s*taksit', t_low)
        if tm: disc = f"{max(map(int, tm))} Taksit"
        if not re.search(r'parafpara|indirim|puan|hediye|kazan|%', t_low):
            return 0, 0, disc, 0, 0

    # 2. Max Discount
    max_matches = re.findall(r'(?:toplam(?:da)?|en fazla|azami|varan)\s*(\d+(?:\.\d{3})*)\s*(?:tl|parafpara|indirim)', t_low)
    possible_max = [int(m.replace('.', '')) for m in max_matches]
    if possible_max:
        max_d = max(possible_max)
    else:
        # Toplam kelimesi yoksa tekil ödülleri ara
        single_rewards = re.findall(r'(\d+(?:\.\d{3})*)\s*tl\s*(?:indirim|puan|parafpara)', t_low)
        valid_rewards = [int(r.replace('.', '')) for r in single_rewards if int(r.replace('.', '')) < 50000]
        if valid_rewards: max_d = max(valid_rewards)

    calculated_spend = 0

    # A. DÖNGÜSEL HESAPLAMA (Regex Güçlendirildi)
    # Desen 1: "Her X TL'ye Y TL" (Klasik)
    cycle_match_1 = re.search(r'her\s*(\d+(?:\.\d{3})*)\s*tl.*?(\d+(?:\.\d{3})*)\s*tl', t_low)
    
    # Desen 2: "X TL ve üzeri her harcamaya Y TL" (ID 8 için kritik)
    cycle_match_2 = re.search(r'(\d+(?:\.\d{3})*)\s*tl\s*(?:ve üzeri)?\s*her\s*harcamaya\s*(\d+(?:\.\d{3})*)\s*tl', t_low)
    
    # En iyi eşleşmeyi seç
    cycle_match = cycle_match_1 or cycle_match_2
    
    if cycle_match and max_d > 0:
        u_spend = int(cycle_match.group(1).replace('.', ''))
        u_earn = int(cycle_match.group(2).replace('.', ''))
        
        if u_earn > 0:
            count = max_d / u_earn
            # ID 8 gibi durumlarda (1000/125 = 8, 8*2000 = 16000)
            calculated_spend = int(count * u_spend)

    # B. YÜZDESEL TERSİNE HESAPLAMA
    mp = re.search(r'(?:%\s*(\d+)|(\d+)\s*%)', t_low)
    perc = 0
    if mp:
        p1, p2 = mp.groups()
        perc = int(p1) if p1 else int(p2)
        
        if max_d > 0 and perc > 0 and calculated_spend == 0:
            calculated_spend = int((max_d * 100) / perc)
            
        earn_suffix = "İndirim" if "indirim" in t_low else "ParafPara"
        earn = f"%{perc} {earn_suffix}"
        if max_d > 0: earn += f" (Max {format_rakam(max_d)} TL)"

    # C. EN YÜKSEK BAREM EŞLEŞTİRME (ID 9, 67)
    if calculated_spend == 0 and max_d > 0:
        # En yüksek ödül için en yüksek harcamayı bul
        spend_matches = re.findall(r'(\d+(?:\.\d{3})*)\s*tl\s*(?:ve üzeri|üzeri|arası)', t_low)
        spends_int = [int(s.replace('.', '')) for s in spend_matches]
        if spends_int:
             # Genellikle en büyük ödül en büyük harcamaya verilir, bu yüzden max() güvenlidir.
             calculated_spend = max(spends_int)

    # 4. Değer Atama
    if calculated_spend > 0:
        min_s = calculated_spend
    else:
        # Hiçbir şey bulunamazsa en düşük giriş
        all_spends = re.findall(r'(\d+(?:\.\d{3})*)\s*tl\s*(?:ve üzeri|üzeri|arası)', t_low)
        spends_int = [int(s.replace('.', '')) for s in all_spends]
        if spends_int: min_s = min(spends_int)

    if not earn and max_d > 0:
        earn_suffix = "İndirim" if "indirim" in t_low else "ParafPara"
        earn = f"{format_rakam(max_d)} TL {earn_suffix}"

    if min_s == 0 and disc: pass

    return min_s, earn, disc, 0, max_d

def extract_cards(text):
    cards = []
    t = tr_lower(text)
    if "platinum" in t: cards.append("Paraf Platinum"); cards.append("Parafly Platinum") if "fly" in t else None
    if "premium" in t: cards.append("Paraf Premium")
    if "parafly" in t and "platinum" not in t: cards.append("Parafly")
    if "sadece" not in t:
        if "ticari" in t: cards.append("Paraf Ticari")
        if "esnaf" in t: cards.append("Paraf Esnaf")
        if "kobi" in t: cards.append("Paraf KOBİ")
        if "genç" in t: cards.append("Paraf Genç")
        if "troy" in t: cards.append("Paraf Troy")
    if not cards: cards.append("Paraf Kartları")
    return list(set(cards))

def extract_participation(text):
    methods = []
    t_low = tr_lower(text)
    if "paraf mobil" in t_low: methods.append("Paraf Mobil")
    match_code = re.search(r'([a-z0-9]{3,})\s*yazıp\s*3404', t_low)
    if match_code:
        code = match_code.group(1).upper()
        methods.append(f"SMS ({code} -> 3404)")
    return ", ".join(methods) if methods else "Detayları kontrol ediniz"

# --- WORKER ---
def worker_task(urls, worker_id):
    print(f"   🤖 İşçi #{worker_id} başladı... ({len(urls)} link)")
    chrome_options = Options()
    chrome_options.add_argument("--headless=new")
    chrome_options.add_argument("--window-size=1920,1080")
    driver = webdriver.Chrome(service=Service(ChromeDriverManager().install()), options=chrome_options) 
    results = []
    try:
        for url in urls:
            try:
                driver.get(url)
                time.sleep(0.5)
                try: WebDriverWait(driver, 6).until(EC.presence_of_element_located((By.CSS_SELECTOR, "h1")))
                except: pass

                soup = BeautifulSoup(driver.page_source, 'html.parser')
                title_el = soup.select_one('.master-banner__content h1') or soup.select_one('h1')
                title = temizle_metin(title_el.text) if title_el else "Başlık Yok"
                if title == "Başlık Yok": continue

                image = None
                img_div = soup.select_one('.master-banner__image')
                if img_div and 'style' in img_div.attrs:
                    m = re.search(r'url\([\'"]?(.*?)[\'"]?\)', img_div['style'])
                    if m: 
                        pot_img = m.group(1)
                        if "logo.svg" not in pot_img: image = urljoin(BASE_URL, pot_img)
                if not image:
                    all_imgs = soup.find_all('img')
                    for img in all_imgs:
                        src = img.get('src') or img.get('data-src')
                        if src and "logo" not in src and "icon" not in src and ".svg" not in src:
                            if "/content/" in src: image = urljoin(BASE_URL, src); break
                if not image: image = "https://www.paraf.com.tr/content/dam/parafcard/paraf-logos/paraf-logo-yeni.png"

                content_div = soup.select_one('.text--use-ulol .cmp-text')
                if not content_div:
                    candidates = soup.select('.text-area') + soup.select('.cmp-text')
                    for c in candidates:
                        if len(c.get_text(strip=True)) > 50: content_div = c; break
                
                conditions = []
                full_text = ""
                if content_div:
                    lis = content_div.select('li')
                    if lis: conditions = [temizle_metin(li.text) for li in lis]
                    else:
                        ps = content_div.select('p')
                        conditions = [temizle_metin(p.text) for p in ps if len(p.text)>15]
                    full_text = " ".join(conditions)

                vf, vu = extract_dates(full_text) 
                cat = get_category(full_text, title) 
                min_s, earn, disc, _, max_d = extract_financials_v25(full_text, title) # V25
                cards = extract_cards(title + " " + full_text)
                part_method = extract_participation(full_text)
                desc = conditions[0] if conditions else title
                if len(desc) > 300: desc = desc[:300] + "..."

                item = {
                    "id": 0, "title": title, "provider": IMPORT_SOURCE_NAME, "category": cat, "merchant": None,
                    "image": image, "images": [image] if image else [], "description": desc, "url": url,
                    "discount": disc, "earning": earn, "min_spend": min_s, "max_discount": max_d,
                    "created_at": datetime.now().strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "valid_from": vf, "valid_until": vu, "participation_method": part_method,
                    "conditions": conditions, "eligible_customers": cards, "source_url": BASE_URL
                }
                results.append(item)
                print(f"      + Çekildi: {title[:30]}... (Min: {min_s}, Max: {max_d})")
            except Exception as e: print(f"      ! Hata ({url}): {e}")
    finally:
        driver.quit()
    return results

# --- ANA AKIŞ ---
def main():
    print(f"🚀 {IMPORT_SOURCE_NAME} Scraper v25 (Final Döngüsel Düzeltme)...")
    chrome_options = Options()
    chrome_options.add_argument("--headless=new") 
    driver = webdriver.Chrome(service=Service(ChromeDriverManager().install()), options=chrome_options)
    campaign_urls = []
    try:
        driver.get(START_URL)
        WebDriverWait(driver, 10).until(EC.presence_of_element_located((By.CSS_SELECTOR, ".cmp-list--campaigns")))
        for i in range(30): 
            try:
                btn = WebDriverWait(driver, 10).until(EC.element_to_be_clickable((By.CSS_SELECTOR, ".button--more-campaign a")))
                ActionChains(driver).move_to_element(btn).perform()
                time.sleep(0.5) 
                driver.execute_script("arguments[0].click();", btn)
                time.sleep(4) 
                print(f"   -> 'Daha Fazla Göster' tıklandı (Deneme {i+1}).")
            except: print("   -> Tüm kampanyalar yüklendi."); break
        soup = BeautifulSoup(driver.page_source, 'html.parser')
        links = soup.select('.cmp-list--campaigns .cmp-teaser__title a')
        for link in links:
            href = link.get('href')
            if href and "/kampanyalar/" in href:
                full_url = urljoin(BASE_URL, href)
                if full_url not in campaign_urls: campaign_urls.append(full_url)
        print(f"\n✅ Toplam {len(campaign_urls)} kampanya linki bulundu.")
    finally: driver.quit()

    if not campaign_urls: return
    print(f"\n⚡ {len(campaign_urls)} kampanya {WORKER_COUNT} işçiye bölüştürülüyor...")
    chunk_size = math.ceil(len(campaign_urls) / WORKER_COUNT)
    chunks = [campaign_urls[i:i + chunk_size] for i in range(0, len(campaign_urls), chunk_size)]
    final_data = []
    with ThreadPoolExecutor(max_workers=WORKER_COUNT) as executor:
        futures = [executor.submit(worker_task, chunk, i+1) for i, chunk in enumerate(chunks)]
        for f in futures: final_data.extend(f.result())
    for i, item in enumerate(final_data, 1): item['id'] = i
    if final_data:
        with open(OUTPUT_FILE, 'w', encoding='utf-8') as f: json.dump(final_data, f, ensure_ascii=False, indent=4)
        print(f"\n🎉 İŞLEM BİTTİ! {len(final_data)} kampanya kaydedildi.")
    else: print("\n❌ Veri çekilemedi.")

if __name__ == "__main__":
    main()