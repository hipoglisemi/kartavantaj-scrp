import os
import sys

sys.path.append(os.path.dirname(os.path.abspath(__file__)))
from src.database import get_db_session
from src.models import Campaign

def fix_yapikredi_urls():
    with get_db_session() as db:
        campaigns = db.query(Campaign).filter(
            Campaign.tracking_url.like('%yapikrediplay.com.tr%') |
            Campaign.tracking_url.like('%crystalcard.com.tr%') |
            Campaign.tracking_url.like('%adioscard.com.tr%')
        ).all()
        
        count = 0
        for c in campaigns:
            old_url = c.tracking_url
            new_url = old_url.replace('yapikrediplay.com.tr', 'worldcard.com.tr') \
                             .replace('crystalcard.com.tr', 'worldcard.com.tr') \
                             .replace('adioscard.com.tr', 'worldcard.com.tr')
            
            c.tracking_url = new_url
            count += 1
            print(f"Update: {old_url} -> {new_url}")
            
        if count > 0:
            db.commit()
            print(f"Successfully updated {count} YapıKredi campaign URLs.")
        else:
            print("No YapıKredi campaign URLs needed fixing.")

if __name__ == "__main__":
    fix_yapikredi_urls()
