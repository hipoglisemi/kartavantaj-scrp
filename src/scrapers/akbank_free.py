# pyre-ignore-all-errors
# type: ignore

import sys
import os
# Path setup
project_root = "/Users/hipoglisemi/Desktop/kartavantaj-scraper"
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from src.scrapers.akbank_base import AkbankBaseScraper

class AkbankFreeScraper(AkbankBaseScraper):
    """
    Scraper for Akbank Free Card campaigns.
    """
    def __init__(self):
        AkbankBaseScraper.__init__(
            self,
            card_name="Axess Free",
            base_url="https://www.kartfree.com",
            list_url="https://www.kartfree.com/ajax/kampanya-ajax-free.aspx",
            referer_url="https://www.kartfree.com/kampanyalar",
            list_params={'checkBox': '[]', 'searchWord': '""'}
        )

if __name__ == "__main__":
    scraper = AkbankFreeScraper()
    scraper.run()
