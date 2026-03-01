import os
import sys
import ssl
import time
import json
import re
from urllib.parse import urljoin
from bs4 import BeautifulSoup
import argparse
import platform
import random

# Parse Arguments
parser = argparse.ArgumentParser()
parser.add_argument('--limit', type=int, default=1000, help='Campaign limit')
args, unknown = parser.parse_known_args()
CAMPAIGN_LIMIT = args.limit

BASE_URL = "https://www.maximum.com.tr"
CAMPAIGNS_URL = "https://www.maximum.com.tr/kampanyalar"
OUTPUT_FILE = "maximum_links.json"

# SSL FIX
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

def main():
    print(f"🚀 Maximum Link Collector (Python)...")
    
    driver = None
    try:
        # Chrome Options
        options = uc.ChromeOptions()
        options.add_argument("--no-first-run")
        options.add_argument("--password-store=basic")
        options.add_argument('--ignore-certificate-errors')
        options.add_argument("--window-position=-10000,0") 
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-dev-shm-usage")
        options.add_argument("--disable-gpu")
        options.add_argument("--disable-popup-blocking")
        options.add_argument("--disable-notifications")
        options.add_argument("--disable-blink-features=AutomationControlled")
        
        # Random User Agent
        ua_list = [
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36"
        ]
        options.add_argument(f"--user-agent={random.choice(ua_list)}")
        
        # OS Detection
        system_os = platform.system()
        use_sub = True
        
        if system_os == "Darwin":
            use_sub = False
            print("   🍏 MacOS detected: Subprocess mode OFF")
            options.add_argument("--disable-backgrounding-occluded-windows")
            options.add_argument("--disable-renderer-backgrounding")
            options.add_argument("--disable-extensions")
            options.add_argument("--disable-plugins")
        else:
            print(f"   🐧 {system_os} detected: Subprocess mode ON")

        driver = uc.Chrome(options=options, use_subprocess=use_sub)
        driver.set_page_load_timeout(120)
        
        print("   -> Connecting to site...")
        driver.get(CAMPAIGNS_URL)
        time.sleep(7)
        
        # Infinite scroll
        last_height = driver.execute_script("return document.body.scrollHeight")
        for _ in range(5):
            try:
                btn = driver.find_element(By.XPATH, "//button[contains(text(), 'Daha Fazla')]")
                driver.execute_script("arguments[0].scrollIntoView(true);", btn)
                time.sleep(1.5)
                driver.execute_script("arguments[0].click();", btn)
                time.sleep(3)
                
                new_height = driver.execute_script("return document.body.scrollHeight")
                if new_height == last_height: break
                last_height = new_height
            except:
                break
        print("      List loaded.")
        
        soup = BeautifulSoup(driver.page_source, 'html.parser')
        campaigns = []
        
        # Find all campaign cards
        for a in soup.find_all('a', href=True):
            if "/kampanyalar/" in a['href'] and "arsiv" not in a['href'] and len(a['href']) > 25:
                url = urljoin(BASE_URL, a['href'])
                
                # Try to find image within the same card
                img = None
                img_tag = a.find('img')
                if img_tag and img_tag.get('src'):
                    img_src = img_tag.get('src')
                    # Skip logos, favicons, menu icons
                    if not any(x in img_src.lower() for x in ['logo', 'favicon', 'menu', 'icon', 'altmenu']):
                        img = urljoin(BASE_URL, img_src)
                
                campaigns.append({
                    "url": url,
                    "image": img
                })
        
        # Remove duplicates by URL
        seen_urls = set()
        unique_campaigns = []
        for c in campaigns:
            if c["url"] not in seen_urls:
                seen_urls.add(c["url"])
                unique_campaigns.append(c)
        
        unique_campaigns = unique_campaigns[:CAMPAIGN_LIMIT]
        print(f"   -> Found {len(unique_campaigns)} campaigns.")

        # Output as JSON array with URL and image
        with open(OUTPUT_FILE, 'w', encoding='utf-8') as f:
            json.dump(unique_campaigns, f, ensure_ascii=False, indent=2)
            
        print(f"\n✅ DONE! {len(unique_campaigns)} campaigns saved to {OUTPUT_FILE}")

    except Exception as main_e:
        print(f"❌ Critical Error: {main_e}")
    finally:
        if driver: 
            try: driver.quit()
            except: pass

if __name__ == "__main__":
    main()
