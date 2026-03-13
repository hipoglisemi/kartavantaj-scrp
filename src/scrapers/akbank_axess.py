# pyre-ignore-all-errors
# type: ignore

import sys
import os
# Path setup
project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

try:
    from src.scrapers.akbank_base import AkbankBaseScraper
except ImportError:
    from akbank_base import AkbankBaseScraper # type: ignore

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
