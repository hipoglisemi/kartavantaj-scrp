import os
import sys
import ssl
import time
import json
import re
import random
from datetime import datetime
from urllib.parse import urljoin
from bs4 import BeautifulSoup

# --- AYARLAR ---
BASE_URL = "https://www.maximum.com.tr"
CAMPAIGNS_URL = "https://www.maximum.com.tr/kampanyalar"
OUTPUT_FILE = "maximum_kampanyalar_hibrit.json"
IMPORT_SOURCE_NAME = "Maximum Kart"
CAMPAIGN_LIMIT = 1000 

# --- SSL FIX ---
try:
    _create_unverified_https_context = ssl._create_unverified_context
except AttributeError:
    pass
else:
    ssl._create_default_https_context = _create_unverified_https_context

if sys.version_info >= (3, 12):
    try:
        import setuptools
        from setuptools import _distutils
        sys.modules["distutils"] = _distutils
    except ImportError:
        pass

import undetected_chromedriver as uc
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

# --- YARDIMCI FONKSİYONLAR ---
def tr_lower(text):
    return text.replace('I', 'ı').replace('İ', 'i').lower() if text else ""

def temizle_metin(text):
    if not text: return ""
    text = text.replace('\n', ' ').replace('\r', '')
    text = re.sub(r'\s+', ' ', text).strip()
    return text

def format_rakam(rakam_int):
    try: return f"{int(rakam_int):,}".replace(",", ".")
    except: return None

def format_tarih_iso(tarih_str, is_end=False):
    if not tarih_str: return None
    ts = tr_lower(tarih_str)
    aylar = {'ocak':'01','şubat':'02','mart':'03','nisan':'04','mayıs':'05','haziran':'06',
             'temmuz':'07','ağustos':'08','eylül':'09','ekim':'10','kasım':'11','aralık':'12'}
    try:
        m_dot = re.search(r'(\d{1,2})\.(\d{1,2})\.(\d{4})\s*-\s*(\d{1,2})\.(\d{1,2})\.(\d{4})', ts)
        if m_dot:
            g1, a1, y1, g2, a2, y2 = m_dot.groups()
            if is_end: return f"{y2}-{a2.zfill(2)}-{g2.zfill(2)}T23:59:59Z"
            else: return f"{y1}-{a1.zfill(2)}-{g1.zfill(2)}T00:00:00Z"
        m = re.search(r'(\d{1,2})\s*([a-zğüşıöç]+)?\s*-\s*(\d{1,2})\s*([a-zğüşıöç]+)\s*(\d{4})', ts)
        if m:
            g1, a1, g2, a2, yil = m.groups()
            if not a1: a1 = a2
            if is_end: return f"{yil}-{aylar.get(a2,'12')}-{str(g2).zfill(2)}T23:59:59Z"
            else: return f"{yil}-{aylar.get(a1,'01')}-{str(g1).zfill(2)}T00:00:00Z"
    except: return None

def get_category(title, text):
    t = tr_lower(title + " " + text)
    if any(x in t for x in ["market", "bakkal", "süpermarket", "migros"]): return "Market"
    if any(x in t for x in ["restoran", "kafe", "yemek", "burger"]): return "Restoran & Kafe"
    if any(x in t for x in ["akaryakıt", "benzin", "otogaz", "opet", "shell"]): return "Yakıt"
    if any(x in t for x in ["giyim", "moda", "ayakkabı"]): return "Giyim & Moda"
    if any(x in t for x in ["elektronik", "teknoloji", "telefon"]): return "Elektronik"
    if any(x in t for x in ["seyahat", "otel", "uçak", "tatil"]): return "Seyahat"
    if any(x in t for x in ["e-ticaret", "online", "internet", "trendyol"]): return "Online Alışveriş"
    return "Diğer"

def extract_merchant(title):
    try:
        match = re.search(r"(.+?)['’](?:ta|te|tan|ten|da|de|dan|den)\s", title, re.IGNORECASE)
        if match:
            merchant = match.group(1).strip()
            if len(merchant.split()) < 5: return merchant
    except: pass
    return None

# --- KART FİLTRESİ (EN GÜNCEL HALİ) ---
def extract_cards_precise(text):
    include_section = re.search(
        r'(?:Kampanyaya|Kampanya)\s+(?:dâhil|dahil)\s+(?:olan|edilen)\s+(?:kartlar|işlemler|kartlar ve işlemler)\s*:?\s*(.*?)(?:Kampanyaya\s+(?:dâhil|dahil)\s+(?:olmayan)|$)',
        text, re.IGNORECASE | re.DOTALL
    )
    target_text = include_section.group(1) if include_section else text
    t_low = target_text.replace('İ', 'i').lower()

    card_patterns = [
        ("Maximiles Black", r"maximiles\s+black"), ("Maximiles", r"maximiles(?!.*\sblack)"),
        ("Privia Black", r"privia\s+black"), ("Privia", r"privia(?!.*\sblack)"),
        ("MercedesCard", r"mercedes\s*card|mercedes"),
        ("İş'te Üniversiteli", r"iş['’\s]?te\s+üniversiteli"),
        ("Maximum Genç", r"maximum\s+genç|genç\s+kart"),
        ("Maximum Pati Kart", r"pati\s+kart"), ("Maximum TEMA Kart", r"tema\s+kart"),
        ("Maximum Gold", r"maximum\s+gold"), ("Maximum Platinum", r"maximum\s+platinum"),
        ("Maximum Premier", r"maximum\s+premier"), ("Bankamatik Kartı", r"bankamatik"),
        ("MaxiPara", r"maxipara"), ("Ticari Kart", r"ticari|vadematik|şirket\s+kredi"),
        ("Sanal Kart", r"sanal\s+kart"), ("TROY Logolu Kart", r"troy"),
        ("Maximum Kart", r"maximum\s+kart|maximum\s+özellikli")
    ]
    found_cards = []
    for name, pattern in card_patterns:
        if re.search(pattern, t_low):
            if name == "Maximiles" and "Maximiles Black" in found_cards: continue
            if name == "Privia" and "Privia Black" in found_cards: continue
            found_cards.append(name)
    if not found_cards:
        if "bireysel" in t_low and "kredi kartı" in t_low: found_cards.append("Maximum Kart")
    return sorted(list(set(found_cards)))

# --- FİNANSAL MOTOR V8 (HATASIZ) ---
def extract_financials_v8(text, title):
    text_clean = re.sub(r'(?<=\d)\.(?=\d)', '', text)
    t_low = text_clean.replace('İ', 'i').lower()
    title_low = title.replace('İ', 'i').lower()
    min_s = 0; max_d = 0; earn = None; disc = None
    
    # 1. Taksit (Başlık Öncelikli)
    title_taksit = re.search(r'(\d+)\s*(?:aya varan)?\s*taksit', title_low)
    if title_taksit and int(title_taksit.group(1)) < 24:
        disc = f"{title_taksit.group(1)} Taksit"
    elif "taksit" in t_low:
        pesin_m = re.findall(r'peşin fiyatına\s*(\d+)\s*taksit', t_low)
        if pesin_m: disc = f"{max(map(int, pesin_m))} Taksit"
        else:
            taksit_m = re.findall(r'(\d+)\s*(?:aya varan|ay)?\s*taksit', t_low)
            valid_t = [int(t) for t in taksit_m if 2 <= int(t) <= 18]
            if valid_t: disc = f"{max(valid_t)} Taksit"
    
    if disc:
        # Aralık Kontrolü (5.000 - 500.000 -> 5.000)
        range_match = re.search(r'(\d+)\s*(?:-|ile)\s*(\d+)\s*tl.*?taksit', t_low)
        if range_match: min_s = int(range_match.group(1))
        else:
            s_match = re.search(r'(\d+)\s*tl.*?taksit', t_low)
            if s_match: min_s = int(s_match.group(1))

    # 2. Fiyat Avantajı (S Sport Fix)
    price_match = re.search(r'(\d+)\s*tl\s*yerine\s*(\d+)\s*tl', t_low)
    if price_match:
        old = int(price_match.group(1)); new = int(price_match.group(2))
        if old - new > 0:
            max_d = old - new; earn = f"{format_rakam(max_d)} TL İndirim (Fiyat Avantajı)"; min_s = new
            return min_s, earn, disc, max_d

    # 3. Yüzde (Çoklu/Tekli)
    if not earn:
        perc_match = re.search(r'%(\d+)', t_low)
        if perc_match:
            rate = int(perc_match.group(1))
            cap_match = re.search(r'(?:en fazla|maksimum|max)\s*(\d+)\s*tl', t_low)
            if cap_match:
                cap = int(cap_match.group(1)); max_d = cap; min_s = int(cap * 100 / rate)
                earn = f"{format_rakam(cap)} TL İndirim"
            else:
                earn = f"%{rate} İndirim"
                entry = re.search(r'(\d+)\s*tl.*?alışveriş', t_low)
                if entry: min_s = int(entry.group(1))

    # 4. Puan (Maksimum Kazanç)
    tier_pattern = r'(\d+)\s*tl.*?(\d+)\s*tl\s*(?:maxipuan|puan|indirim)'
    tiers = re.findall(tier_pattern, t_low)
    best_earn = 0; best_spend = 0
    for s_str, e_str in tiers:
        s = int(s_str); e = int(e_str)
        if s > e and e > best_earn: best_earn = e; best_spend = s
            
    if best_earn > 0 and (max_d == 0 or best_earn > max_d):
        max_d = best_earn; min_s = best_spend
        suffix = "İndirim" if "indirim" in title_low else "MaxiPuan"
        earn = f"{format_rakam(best_earn)} TL {suffix}"

    # 5. Döngüsel
    unit_match = re.search(r'her\s*(\d+)\s*tl', t_low)
    total_match = re.search(r'toplam(?:da)?\s*(\d+)\s*tl', t_low)
    if unit_match and total_match:
        u_spend = int(unit_match.group(1)); total_cap = int(total_match.group(1))
        u_earn_m = re.search(r'(\d+)\s*tl\s*(?:maxipuan|puan)', t_low)
        u_earn = int(u_earn_m.group(1)) if u_earn_m else 0
        if u_earn > 0 and u_earn < total_cap:
             count = total_cap / u_earn
             calc_spend = int(count * u_spend)
             if total_cap >= max_d:
                 max_d = total_cap; min_s = calc_spend
                 suffix = "İndirim" if "indirim" in title_low else "MaxiPuan"
                 earn = f"{format_rakam(total_cap)} TL {suffix}"

    return min_s, earn, disc, max_d

def extract_participation(text):
    methods = []
    t_low = tr_lower(text)
    if "işcep" in t_low or "maximum mobil" in t_low: methods.append("Maximum Mobil / İşCep")
    sms_match = re.search(r'([a-z0-9]+)\s*yazıp\s*(\d{4})', t_low)
    if sms_match: methods.append(f"SMS ({sms_match.group(1).upper()} -> {sms_match.group(2)})")
    if "otomatik" in t_low and not methods: return "Otomatik Katılım"
    return ", ".join(list(set(methods))) if methods else "Detayları İnceleyin"

# --- ANA AKIŞ ---
def main():
    print(f"🚀 Maximum Kart - HIBRIT MOD (Görsel v7 + Logic v8)...")
    
    driver = None
    try:
        options = uc.ChromeOptions()
        options.add_argument("--no-first-run")
        options.add_argument("--password-store=basic")
        options.add_argument('--ignore-certificate-errors')
        options.add_argument("--window-position=-10000,0") 
        options.add_argument("--no-sandbox")
        
        driver = uc.Chrome(options=options, use_subprocess=True)
        driver.set_page_load_timeout(60)
        
        driver.get(CAMPAIGNS_URL)
        print("   -> Liste yükleniyor...")
        time.sleep(5)
        
        # Sonsuz Scroll
        while True:
            try:
                btn = driver.find_element(By.XPATH, "//button[contains(text(), 'Daha Fazla')]")
                driver.execute_script("arguments[0].scrollIntoView(true);", btn)
                time.sleep(1)
                driver.execute_script("arguments[0].click();", btn)
                time.sleep(2)
            except:
                print("      Tüm liste yüklendi.")
                break
        
        soup = BeautifulSoup(driver.page_source, 'html.parser')
        all_links = []
        for a in soup.find_all('a', href=True):
            if "/kampanyalar/" in a['href'] and "arsiv" not in a['href'] and len(a['href']) > 25:
                all_links.append(urljoin(BASE_URL, a['href']))
        
        unique_links = list(set(all_links))
        print(f"   -> Toplam {len(unique_links)} kampanya bulundu. İşleniyor...")

        final_data = []
        count = 0
        
        for i, url in enumerate(unique_links, 1):
            if count >= CAMPAIGN_LIMIT: break
            
            try:
                time.sleep(1.5)
                driver.get(url)
                
                # 🔥 GÖRSEL İÇİN V7 TAKTİĞİ: SCROLL
                driver.execute_script("window.scrollTo(0, 600);")
                time.sleep(0.5)
                
                try: WebDriverWait(driver, 5).until(EC.presence_of_element_located((By.CSS_SELECTOR, "span[id$='CampaignDescription']")))
                except: pass

                d_soup = BeautifulSoup(driver.page_source, 'html.parser')
                title_el = d_soup.select_one('h1.gradient-title-text') or d_soup.find('h1')
                title = temizle_metin(title_el.text) if title_el else "Başlık Yok"
                
                if "geçmiş" in title.lower() or len(title) < 10: continue

                date_el = d_soup.select_one("span[id$='KampanyaTarihleri']")
                date_text = temizle_metin(date_el.text) if date_el else ""
                vu = format_tarih_iso(date_text, True)
                if vu and datetime.strptime(vu, "%Y-%m-%dT%H:%M:%SZ") < datetime.now(): continue

                desc_el = d_soup.select_one("span[id$='CampaignDescription']")
                conditions = []
                full_text = ""
                if desc_el:
                    for br in desc_el.find_all("br"): br.replace_with("\n")
                    for p in desc_el.find_all("p"): p.insert(0, "\n")
                    raw_text = desc_el.get_text()
                    conditions = [temizle_metin(line) for line in raw_text.split('\n') if len(temizle_metin(line)) > 15]
                    full_text = " ".join(conditions)
                else:
                    full_text = temizle_metin(d_soup.get_text())
                    conditions = [t for t in full_text.split('\n') if len(t)>20]

                # 🔥 GÖRSEL İÇİN V7 TAKTİĞİ: ID SELECTOR
                image = None
                img_el = d_soup.select_one("img[id$='CampaignImage']")
                if img_el: image = urljoin(BASE_URL, img_el['src'])

                cat = get_category(title, full_text)
                merchant = extract_merchant(title)
                min_s, earn, disc, max_d = extract_financials_v8(full_text, title)
                cards = extract_cards_precise(full_text)
                vf = format_tarih_iso(date_text, False)
                part_method = extract_participation(full_text)
                
                count += 1
                print(f"      [{count}] {title[:35]}... (M:{min_s} E:{earn} Img:{'✅' if image else '❌'})")

                item = {
                    "id": count,
                    "title": title,
                    "provider": IMPORT_SOURCE_NAME,
                    "category": cat,
                    "merchant": merchant,
                    "image": image,
                    "images": [image] if image else [],
                    "description": conditions[0] if conditions else title,
                    "url": url,
                    "discount": disc,
                    "earning": earn,
                    "min_spend": min_s,
                    "max_discount": max_d,
                    "created_at": datetime.now().strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "valid_from": vf,
                    "valid_until": vu,
                    "participation_method": part_method,
                    "conditions": conditions,
                    "eligible_customers": cards,
                    "source_url": BASE_URL
                }
                final_data.append(item)

            except Exception as e:
                print(f"      ⚠️ Hata: {e}")
                continue
        
        with open(OUTPUT_FILE, 'w', encoding='utf-8') as f:
            json.dump(final_data, f, ensure_ascii=False, indent=4)
            
        print(f"\n✅ İŞLEM TAMAMLANDI! {len(final_data)} kampanya kaydedildi.")

    except Exception as main_e:
        print(f"❌ Kritik Hata: {main_e}")
    finally:
        if driver: 
            try: driver.quit()
            except: pass

if __name__ == "__main__":
    main()
