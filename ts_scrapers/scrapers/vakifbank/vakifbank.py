
import argparse
import json
import time
import random
import os
import ssl
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from webdriver_manager.chrome import ChromeDriverManager
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from bs4 import BeautifulSoup
from urllib.parse import urljoin

ssl._create_default_https_context = ssl._create_unverified_context

BASE_URL = "https://www.vakifkart.com.tr"
LIST_URL_TEMPLATE = "https://www.vakifkart.com.tr/kampanyalar/sayfa/{}"
OUTPUT_FILE = "vakifbank_kampanyalar_raw.json"

def get_driver():
    options = Options()
    options.add_argument("--headless=new")
    options.add_argument("--window-size=1920,1080")
    options.add_argument("--disable-popup-blocking")
    options.page_load_strategy = 'eager'
    
    prefs = {"profile.managed_default_content_settings.images": 2} 
    options.add_experimental_option("prefs", prefs)

    service = Service(ChromeDriverManager().install())
    driver = webdriver.Chrome(service=service, options=options)
    return driver

def robust_get(driver, url, retries=3):
    for i in range(retries):
        try:
            driver.get(url)
            return True
        except Exception as e:
            print(f"⚠️ Load error (Attempt {i+1}/{retries}): {e}")
            time.sleep(3)
    return False

def scrape_list_page(driver, limit=None):
    print("📋 Collecting campaign links...")
    links = []
    page = 1
    
    while True:
        if page == 1:
            url = "https://www.vakifkart.com.tr/kampanyalar"
        else:
            url = LIST_URL_TEMPLATE.format(page)
            
        print(f"   Getting page {page}: {url}")
        
        if not robust_get(driver, url):
            break
            
        try:
            # Wait for list
            WebDriverWait(driver, 10).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, "div.mainKampanyalarDesktop"))
            )
            
            items = driver.find_elements(By.CSS_SELECTOR, "div.mainKampanyalarDesktop:not(.eczk) .list a.item")
            if not items:
                print("   No more items found.")
                break
                
            new_links = 0
            for item in items:
                href = item.get_attribute('href')
                if href and href not in links:
                    links.append(href)
                    new_links += 1
            
            print(f"   -> Found {new_links} new campaigns. Total: {len(links)}")
            
            if new_links == 0:
                break

            if limit and len(links) >= limit:
                print(f"   🛑 Limit reached ({limit}). Stopping.")
                links = links[:limit]
                break
                
            page += 1
            time.sleep(1)
            
        except Exception as e:
            print(f"   ⚠️ Error page {page}: {e}")
            break
            
    return links

def scrape_detail(driver, url):
    if not robust_get(driver, url):
        return None
        
    try:
        # Title wait
        try:
            WebDriverWait(driver, 5).until(EC.presence_of_element_located((By.CSS_SELECTOR, "h1")))
        except: pass

        soup = BeautifulSoup(driver.page_source, 'html.parser')
        
        # 1. Title
        title_el = soup.select_one('.kampanyaDetay .title h1') or soup.find('h1')
        title = title_el.get_text(strip=True) if title_el else "Başlık Yok"
        
        # 2. Content
        content_div = soup.select_one('.kampanyaDetay .contentSide')
        detail_html = str(content_div) if content_div else ""
        
        # 3. Description
        description = title
        if content_div:
            desc_el = content_div.select_one('p') or content_div.select_one('li')
            if desc_el: description = desc_el.get_text(strip=True)

        # 4. Image
        img_el = soup.select_one('.kampanyaDetay .coverSide img')
        image = urljoin(BASE_URL, img_el['src']) if img_el else None
        
        return {
            "url": url,
            "title": title,
            "description": description,
            "detail_html": detail_html,
            "image": image,
            "bank": "Vakıfbank",
            "card": "Vakıfbank World"
        }
        
    except Exception as e:
        print(f"   ❌ Error detail: {e}")
        return None

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, help="Limit")
    args = parser.parse_args()
    
    driver = get_driver()
    all_data = []
    
    try:
        links = scrape_list_page(driver, limit=args.limit)
        
        print(f"\n⚡ Scraping {len(links)} details...")
        for i, link in enumerate(links):
            print(f"   [{i+1}/{len(links)}] {link}")
            d = scrape_detail(driver, link)
            if d:
                all_data.append(d)
                print(f"      ✅ {d['title'][:30]}...")
            time.sleep(0.5)
            
    finally:
        driver.quit()
        
    with open(OUTPUT_FILE, 'w', encoding='utf-8') as f:
        json.dump(all_data, f, ensure_ascii=False, indent=4)
    print(f"\nSaved {len(all_data)} to {OUTPUT_FILE}")

if __name__ == "__main__":
    main()
