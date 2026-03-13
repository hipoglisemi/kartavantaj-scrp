


import sys
import os
# Path setup
project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from src.scrapers.akbank_base import AkbankBaseScraper  # type: ignore # pyre-ignore[21]

class AkbankBusinessScraper(AkbankBaseScraper):
    """
    Scraper for Akbank Business (Ticari) campaigns.
    """
    def __init__(self):
        AkbankBaseScraper.__init__(self,
            card_name="Axess Business",
            base_url="https://www.axess.com.tr",
            list_url="https://www.axess.com.tr/ajax/kampanya-ajax-ticari.aspx",
            referer_url="https://www.axess.com.tr/ticarikartlar/kampanya/8/450/kampanyalar",
            list_params={'checkBox': '[]', 'searchWord': '""'}
        )

if __name__ == "__main__":
    scraper = AkbankBusinessScraper()
    scraper.run()
