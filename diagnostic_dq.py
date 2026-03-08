print("Script started...")
try:
    from src.database import SessionLocal
    from src.models import Campaign, Sector
    from sqlalchemy.orm import joinedload
    print("Imports successful")
    
    db = SessionLocal()
    print("Session created")
    
    q = db.query(Campaign).filter(Campaign.is_active == True, Campaign.auto_corrected == False)
    total = q.count()
    print(f"Total found: {total}")
    
    defective_candidates = q.options(joinedload(Campaign.sector), joinedload(Campaign.brands)).all()
    print(f"Candidates loaded: {len(defective_candidates)}")

    missing_desc = 0
    missing_reward = 0
    sector_diger = 0
    missing_brands = 0
    with_url = 0
    no_url = 0
    defective_count = 0
    
    for c in defective_candidates:
        is_defective = False
        if not c.description or len(c.description.strip()) < 15:
            is_defective = True
            missing_desc += 1
        if not c.reward_text or c.reward_text.strip() == "":
            is_defective = True
            missing_reward += 1
        if c.sector and c.sector.slug == "diger":
            is_defective = True
            sector_diger += 1
        if not c.brands:
            is_defective = True
            missing_brands += 1
        
        if is_defective:
            defective_count += 1
            if c.tracking_url:
                with_url += 1
            else:
                no_url += 1
                
    print(f"Results summary:")
    print(f"  Defective: {defective_count}")
    print(f"  With URL: {with_url}")
    print(f"  No URL: {no_url}")
    print(f"  Missing Desc: {missing_desc}")
    print(f"  Missing Reward: {missing_reward}")
    print(f"  Sector Diger: {sector_diger}")
    print(f"  Missing Brands: {missing_brands}")

except Exception as e:
    print(f"An error occurred: {e}")
    import traceback
    traceback.print_exc()
finally:
    if 'db' in locals():
        db.close()
