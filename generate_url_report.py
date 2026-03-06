import os
import sys
import requests
from concurrent.futures import ThreadPoolExecutor, as_completed
from collections import defaultdict

sys.path.append(os.path.dirname(os.path.abspath(__file__)))
from src.database import get_db_session
from src.models import Campaign

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
    'Accept-Language': 'tr-TR,tr;q=0.9,en-US;q=0.8,en;q=0.7',
    'Connection': 'keep-alive'
}

def check_url(campaign_id, bank_name, url):
    if not url:
        return None
        
    try:
        response = requests.head(url, headers=HEADERS, timeout=5, allow_redirects=True)
        if response.status_code == 404:
             return {"id": campaign_id, "bank": bank_name, "url": url, "error": f"HTTP 404 Not Found"}
        elif response.status_code >= 400:
             return {"id": campaign_id, "bank": bank_name, "url": url, "error": f"HTTP {response.status_code}"}
        
        # Check if redirected to homepage (a common soft 404 pattern)
        final_url = response.url
        if final_url != url:
            domain = url.split('//')[1].split('/')[0]
            if final_url == f"https://{domain}/" or final_url == f"http://{domain}/" or final_url == f"https://{domain}/kampanyalar":
                return {"id": campaign_id, "bank": bank_name, "url": url, "error": f"Redirects to Homepage ({final_url})"}
                
        return None
    except requests.exceptions.Timeout:
        return {"id": campaign_id, "bank": bank_name, "url": url, "error": "Timeout (possible bot protection)"}
    except requests.exceptions.SSLError as e:
         return {"id": campaign_id, "bank": bank_name, "url": url, "error": f"SSL Error"}
    except requests.exceptions.ConnectionError as e:
         return {"id": campaign_id, "bank": bank_name, "url": url, "error": f"Connection Error"}
    except Exception as e:
        return {"id": campaign_id, "bank": bank_name, "url": url, "error": str(e)}

def generate_report():
    print("Fetching active campaigns from database...")
    with get_db_session() as db:
        campaigns = db.query(Campaign).filter(
            Campaign.is_active == True,
            Campaign.tracking_url != None,
            Campaign.tracking_url != ""
        ).all()
        
        tasks = []
        for c in campaigns:
            if c.card and c.card.bank:
                tasks.append((c.id, c.card.bank.name, c.tracking_url))
        
    print(f"Starting health check for {len(tasks)} URLs...")
    
    results = defaultdict(list)
    
    with ThreadPoolExecutor(max_workers=10) as executor:
        future_to_url = {executor.submit(check_url, task[0], task[1], task[2]): task for task in tasks}
        
        count = 0
        for future in as_completed(future_to_url):
            count += 1
            if count % 100 == 0:
                print(f"Processed {count}/{len(tasks)} URLs...")
                
            res = future.result()
            if res:
                results[res["bank"]].append(res)
                
    # Generate Output Report
    report_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "broken_urls_report.txt")
    with open(report_path, "w", encoding="utf-8") as f:
        f.write("KARTAVANTAJ BAZUKALI URL HEALTH REPORT\n")
        f.write("="*50 + "\n\n")
        
        if not results:
            f.write("Tebrikler! Sistemde kırık veya hatalı URL bulunamadı.\n")
        else:
            for bank, errors in sorted(results.items()):
                f.write(f"🏦 BANKA: {bank} ({len(errors)} Hatalı URL)\n")
                f.write("-" * 50 + "\n")
                
                # Group by error type for better readability
                error_types = defaultdict(list)
                for err in errors:
                    error_types[err["error"]].append(err)
                    
                for err_type, items in error_types.items():
                    f.write(f"\n  🔴 Hata Tipi: {err_type} ({len(items)} adet)\n")
                    for item in items:
                        f.write(f"    - ID: {item['id']}\n")
                        f.write(f"      URL: {item['url']}\n")
                f.write("\n" + "="*50 + "\n\n")
                
    print(f"\n✅ Tarama tamamlandı! Rapor '{report_path}' dosyasına kaydedildi.")
    
if __name__ == "__main__":
    generate_report()
