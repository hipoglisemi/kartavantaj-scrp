
import requests
import time
from typing import List, Optional
from urllib.parse import urljoin
import sys
import os

current_dir = os.path.dirname(os.path.abspath(__file__))
project_root = os.path.dirname(os.path.dirname(current_dir))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from src.scrapers.akbank_base import AkbankBaseScraper

class AkbankWingsScraper(AkbankBaseScraper):
    """
    Scraper for Akbank Wings campaigns.
    Uses the Wings-specific JSON API for discovery.
    """
    
    WINGS_API_URL = "https://www.wingscard.com.tr/api/campaign/list"
    WINGS_BASE_URL = "https://www.wingscard.com.tr"
    
    def __init__(self):
        super().__init__(
            card_name="Wings",
            base_url=self.WINGS_BASE_URL,
            list_url=self.WINGS_API_URL,
            referer_url="https://www.wingscard.com.tr/kampanyalar"
        )

    def _fetch_campaign_list(self) -> List[str]:
        """Fetch all campaign URLs from the Wings JSON API."""
        print(f"📥 Fetching Wings campaign list from API...")
        campaign_urls = []
        
        try:
            # First request to get total page count
            response = self.session.get(self.WINGS_API_URL, params={'page': 1}, timeout=20)
            response.raise_for_status()
            data = response.json()
            
            # Wings API response structure check
            # data.get('data', {}).get('totalCount') or data.get('pageCount')
            data_obj = data.get('data', {})
            page_count = data.get('pageCount') or (data_obj.get('totalCount', 0) // 8 + 1)
            print(f"   Total pages to scan: {page_count}")
            
            for page in range(1, page_count + 1):
                print(f"   Fetching page {page}/{page_count}...")
                
                response = self.session.get(self.WINGS_API_URL, params={'page': page}, timeout=20)
                response.raise_for_status()
                json_response = response.json()
                
                current_data = json_response.get('data', {})
                campaigns = current_data.get('list', [])
                
                if not campaigns:
                    break

                for campaign in campaigns:
                    url_path = campaign.get('url')
                    if url_path:
                        full_url = urljoin(self.WINGS_BASE_URL, url_path)
                        if full_url not in campaign_urls:
                            campaign_urls.append(full_url)
                
                time.sleep(0.5)
                
        except Exception as e:
            print(f"❌ Error fetching Wings campaign API: {e}")
            
        print(f"✅ Found {len(campaign_urls)} campaigns for {self.card_name}")
        return campaign_urls

    def _process_campaign(self, url: str):
        """Override to use Wings-specific selectors."""
        from bs4 import BeautifulSoup
        from src.services.ai_parser import parse_api_campaign
        
        try:
            print(f"🔍 Processing: {url}")
            response = self.session.get(url, timeout=20)
            response.raise_for_status()
            
            soup = BeautifulSoup(response.text, 'html.parser')
            
            # --- Wings Specific Selectors ---
            # Title is in h1.banner-title according to wings.ts
            title_elm = soup.select_one('h1.banner-title')
            if not title_elm:
                title_elm = soup.select_one('h2.pageTitle') # Fallback
            
            title = title_elm.get_text(strip=True) if title_elm else "Kampanya"
            
            # Image is in .privileges-detail-image img
            img_elm = soup.select_one('.privileges-detail-image img')
            image_url = None
            if img_elm:
                image_url = urljoin(self.WINGS_BASE_URL, img_elm.get('src', ''))
            
            # Get background image if main one missing
            if not image_url:
                banner = soup.select_one('.privileges-detail-banner')
                if banner and 'style' in banner.attrs:
                    import re
                    match = re.search(r'url\(["\']?(.*?)["\']?\)', banner['style'])
                    if match:
                        image_url = urljoin(self.WINGS_BASE_URL, match.group(1))

            # Details text for AI
            details_container = soup.select_one('.privileges-detail-content') or soup.select_one('.cmsContent')
            details_text = details_container.get_text(separator=' ', strip=True) if details_container else ""
            
            if not details_text:
                details_text = title

            # AI Parsing
            ai_data = parse_api_campaign(
                title=title,
                short_description=title,
                content_html=details_text,
                bank_name="Akbank",
                scraper_sector=None
            )
            
            # Save to DB
            self._save_campaign(title, details_text, image_url, ai_data, url)
            
        except Exception as e:
            print(f"❌ Failed to process {url}: {e}")

if __name__ == "__main__":
    scraper = AkbankWingsScraper()
    scraper.run()
