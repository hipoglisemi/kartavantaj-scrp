
import time
import json
import random
import os
import sys
import ssl
import undetected_chromedriver as uc

# MacOS SSL Fix
ssl._create_default_https_context = ssl._create_unverified_context

from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from bs4 import BeautifulSoup

# --- CONFIGURATION ---
BASE_URL = "https://www.paraf.com.tr"
START_URL = "https://www.paraf.com.tr/tr/kampanyalar.html"
OUTPUT_FILE = "paraf_kampanyalar_raw.json"
# Parse limit from args
CAMPAIGN_LIMIT = 1000
if len(sys.argv) > 1 and "--limit" in sys.argv:
    try:
        idx = sys.argv.index("--limit")
        CAMPAIGN_LIMIT = int(sys.argv[idx + 1])
    except: pass
print(f"   🎯 Limit: {CAMPAIGN_LIMIT}")

def get_random_user_agent():
    user_agents = [
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_14_6) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/14.1.2 Safari/605.1.15"
    ]
    return random.choice(user_agents)

def setup_driver():
    options = uc.ChromeOptions()
    options.add_argument(f"user-agent={get_random_user_agent()}")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--window-size=1920,1080")
    # options.add_argument("--headless=new") # Debug için kapalı tutuyoruz, gerekirse açılır

    driver = uc.Chrome(options=options)
    return driver

def scroll_and_click_more(driver):
    print("   🔄 Scroll ve 'Daha Fazla' butonu kontrol ediliyor...")
    
    # Initial load wait
    time.sleep(5) 
    
    click_count = 0
    max_clicks = 30
    if CAMPAIGN_LIMIT < 20: max_clicks = 1  # Optimize for testing
    
    while click_count < max_clicks:
        try:
            # Scroll to bottom to trigger events
            driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
            time.sleep(2)
            
            # Find visible "Daha Fazla" buttons
            buttons = driver.find_elements(By.CSS_SELECTOR, ".button--more-campaign a, a.btn-more, button")
            clicked = False
            
            for btn in buttons:
                if btn.is_displayed() and "DAHA FAZLA" in btn.text.strip().upper():
                    print(f"   👇 'Daha Fazla'ya tıklanıyor ({click_count+1})...")
                    driver.execute_script("arguments[0].click();", btn)
                    time.sleep(3) # Wait for content
                    clicked = True
                    click_count += 1
                    break
            
            if not clicked:
                print("   ✅ 'Daha Fazla' butonu kalmadı.")
                break
                
        except Exception as e:
            print(f"   ⚠️ Scroll Hatası: {e}")
            break

def scrape_list_page(driver):
    print(f"   🌐 Liste taranıyor: {START_URL}")
    driver.get(START_URL)
    scroll_and_click_more(driver)
    
    soup = BeautifulSoup(driver.page_source, 'html.parser')
    links = []
    
    # Paraf Reference Selector: .cmp-list--campaigns .cmp-teaser__title a
    items = soup.select('.cmp-list--campaigns .cmp-teaser__title a')
    
    # 2. Backup Selektörler
    if not items:
        print("   ⚠️ Primary selector failed. Trying backup...")
        items = soup.select('a[href*="/kampanyalar/"]')
        
    print(f"   🔍 Bulunan link sayısı: {len(items)}")
    
    for item in items:
        href = item.get('href')
        if href and "/kampanyalar/" in href and not href.endswith("kampanyalar.html"):
            full_url = href if href.startswith("http") else BASE_URL + href
            if full_url not in links:
                links.append(full_url)
                
    return links[:CAMPAIGN_LIMIT]

def robust_get(driver, url, max_retries=3):
    for attempt in range(max_retries):
        try:
            driver.get(url)
            # Wait for meaningful content
            WebDriverWait(driver, 20).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, "h1"))
            )
            return True
        except Exception as e:
            print(f"      ⚠️ Bağlantı hatası ({attempt+1}/{max_retries}): {str(e)[:50]}...")
            try:
                driver.delete_all_cookies()
            except: pass
            time.sleep(random.uniform(10, 20))
    return False

def scrape_detail(driver, url):
    if not robust_get(driver, url):
        print("      ❌ Sayfa yüklenemedi, atlanıyor.")
        return None

    soup = BeautifulSoup(driver.page_source, 'html.parser')
    
    # 1. Title
    title_el = soup.select_one('.master-banner__content h1') or soup.select_one('h1')
    title = title_el.text.strip() if title_el else "Başlıksız Kampanya"
    
    # 2. Image
    image = ""
    # Try style attribute first (common in Paraf)
    img_div = soup.select_one('.master-banner__image')
    if img_div and 'style' in img_div.attrs:
        import re
        m = re.search(r'url\([\'"]?(.*?)[\'"]?\)', img_div['style'])
        if m: image = m.group(1)
        
    # Backup image
    if not image:
        # Try finding valid content images
        all_imgs = soup.find_all('img')
        for img in all_imgs:
            src = img.get('src') or img.get('data-src') or ""
            if src:
                src_lower = src.lower()
                # Filter out junk
                if "logo" in src_lower or "icon" in src_lower or ".svg" in src_lower:
                    continue
                if "placeholder" in src_lower or "spacer" in src_lower:
                    continue
                    
                # Must be a content image
                if "/content/" in src or "campaigns" in src_lower:
                    image = src
                    break
        
    if image and not image.startswith('http'):
        image = BASE_URL + image
        
    # Final Fallback (if still empty)
    if not image or "logo" in image.lower():
         image = "https://www.paraf.com.tr/content/dam/parafcard/paraf-logos/paraf-logo-yeni.png"

    # 3. Description (Short) & Detail HTML (Long)
    # Paraf content is usually in .cmp-text
    content_div = soup.select_one('.text--use-ulol .cmp-text') or soup.select_one('.cmp-text')
    
    description = ""
    detail_html = ""
    
    if content_div:
        # P tags for short description
        ps = content_div.find_all('p')
        description = " ".join([p.text.strip() for p in ps[:2]]) # First 2 paragraphs as summary
        detail_html = str(content_div) # Keep full HTML for AI
    else:
        # Fallback to body text if no container found
        description = title
        detail_html = str(soup.body)

    return {
        "url": url,
        "title": title,
        "description": description,
        "detail_html": detail_html, # Critical for AI
        "image": image,
        "bank": "Halkbank",
        "card": "Paraf"
    }

def main():
    print("🚀 Paraf Python Scraper Başlatılıyor (Hybrid Mode)...")
    driver = setup_driver()
    
    try:
        links = scrape_list_page(driver)
        print(f"   🎯 Toplam {len(links)} kampanya işlenecek.")
        
        results = []
        for i, link in enumerate(links):
            print(f"   [{i+1}/{len(links)}] İşleniyor: {link}")
            data = scrape_detail(driver, link)
            if data:
                results.append(data)
                # Save continually
                with open(OUTPUT_FILE, 'w', encoding='utf-8') as f:
                    json.dump(results, f, ensure_ascii=False, indent=4)
            time.sleep(random.uniform(2, 5)) # Polite delay
            
        # Final Save
        with open(OUTPUT_FILE, 'w', encoding='utf-8') as f:
            json.dump(results, f, ensure_ascii=False, indent=4)
            
        print(f"\n✅ İşlem Tamamlandı! {len(results)} kampanya kaydedildi: {OUTPUT_FILE}")
        
    except Exception as e:
        print(f"\n❌ Kritik Hata: {e}")
    finally:
        driver.quit()

if __name__ == "__main__":
    main()
