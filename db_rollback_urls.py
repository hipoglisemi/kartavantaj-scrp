import os
import sys
from datetime import datetime, timedelta

sys.path.append(os.path.dirname(os.path.abspath(__file__)))
from src.database import get_db_session
from src.models import Campaign, Bank, Card

def rollback_db_changes():
    with get_db_session() as db:
        # 1. Re-activate campaigns that were likely deactivated by url_health_check today
        today = datetime.now() - timedelta(hours=14)
        deactivated = db.query(Campaign).filter(
            Campaign.is_active == False,
            Campaign.updated_at >= today
        ).all()
        
        reactivated_count = 0
        for c in deactivated:
            c.is_active = True
            reactivated_count += 1
            
        print(f"Re-activated {reactivated_count} campaigns.")

        # 2. Revert YapıKredi tracking URLs
        yk_bank = db.query(Bank).filter_by(slug='yapi-kredi').first()
        if yk_bank:
            campaigns = db.query(Campaign).filter(
                Campaign.tracking_url.like('https://www.worldcard.com.tr%')
            ).all()
            
            url_revert_count = 0
            for c in campaigns:
                if not c.card or c.card.bank_id != yk_bank.id:
                    continue
                    
                old_url = c.tracking_url
                new_url = old_url
                if c.card.slug == 'play':
                    new_url = old_url.replace('worldcard.com.tr', 'yapikrediplay.com.tr')
                elif c.card.slug == 'crystal':
                    new_url = old_url.replace('worldcard.com.tr', 'crystalcard.com.tr')
                elif c.card.slug == 'adios':
                    new_url = old_url.replace('worldcard.com.tr', 'adioscard.com.tr')
                    
                if old_url != new_url:
                    c.tracking_url = new_url
                    url_revert_count += 1
            
            print(f"Reverted {url_revert_count} YapıKredi URLs back to their card domains.")

        db.commit()
        print("Database rollback completed.")

if __name__ == "__main__":
    rollback_db_changes()
