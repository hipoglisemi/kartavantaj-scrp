import os
import sys
from datetime import datetime, timedelta

sys.path.append(os.path.dirname(os.path.abspath(__file__)))
from src.database import get_db_session
from src.models import Campaign, CampaignBrand, Brand, Sector

def reset_bad_classifications():
    with get_db_session() as db:
        # Check campaigns from the last 3 days
        cutoff = datetime.now() - timedelta(days=3)
        
        recent_campaigns = db.query(Campaign).filter(
            Campaign.created_at >= cutoff
        ).all()
        
        count_brands_cleared = 0
        count_sectors_cleared = 0
        
        for c in recent_campaigns:
            has_changes = False
            # Clear all brands linked to this campaign
            if c.brands:
                db.query(CampaignBrand).filter(CampaignBrand.campaign_id == c.id).delete()
                count_brands_cleared += 1
                has_changes = True
                
            # Clear sector (so autofixer picks it up again)
            if c.sector_id:
                c.sector_id = None
                count_sectors_cleared += 1
                has_changes = True
                
            if has_changes:
                c.is_active = True # ensure it's active so autofixer picks it up
                
        # Also clean up garbage brands like 'bereket', 'ramazan', bank names, etc that might have been created
        bad_brand_names = ['bereket', 'ramazan', 'kampanya', 'maxipuan', 'worldpuan', 'bonus', 'paraf', 'axess', 'vakıfbank', 'ziraat', 'yapı kredi', 'garanti', 'iş bankası', 'halkbank', 'puan', 'indirim']
        bad_brands = db.query(Brand).filter(Brand.name.in_(bad_brand_names)).all()
        for b in bad_brands:
            db.query(CampaignBrand).filter(CampaignBrand.brand_id == b.id).delete()
            db.delete(b)
            print(f"Deleted garbage brand: {b.name}")
            
        # Clean up any brand created in last 2 days that has no links
        cutoff_brands = datetime.now() - timedelta(days=2)
        orphan_brands = db.query(Brand).filter(Brand.created_at >= cutoff_brands).all()
        for b in orphan_brands:
            links = db.query(CampaignBrand).filter(CampaignBrand.brand_id == b.id).count()
            if links == 0:
                print(f"Deleted unused recent brand: {b.name}")
                db.delete(b)

        db.commit()
        print(f"Reset {count_brands_cleared} campaign brands and {count_sectors_cleared} sectors for re-classification via Gemini.")

if __name__ == "__main__":
    reset_bad_classifications()
