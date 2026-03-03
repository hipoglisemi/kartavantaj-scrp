import sys
import os

current_dir = os.path.dirname(os.path.abspath(__file__))
project_root = os.path.dirname(os.path.dirname(current_dir))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from src.scrapers.akbank_base import AkbankBaseScraper

class AkbankAxessScraper(AkbankBaseScraper):
    """
    Scraper for Akbank Axess campaigns.
    """
    def __init__(self):
        super().__init__(
            card_name="Axess",
            base_url="https://www.axess.com.tr",
            list_url="https://www.axess.com.tr/ajax/kampanya-ajax.aspx",
            referer_url="https://www.axess.com.tr/kampanyalar",
            list_params={'checkBox': '[0]', 'searchWord': '""'}
        )

if __name__ == "__main__":
    scraper = AkbankAxessScraper()
    scraper.run()
