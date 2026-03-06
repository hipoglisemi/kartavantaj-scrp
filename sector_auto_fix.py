#!/usr/bin/env python3
"""
Sector Auto-Fixer
Intelligently re-categorizes campaigns in the 'Diğer' (Other) sector using Gemini AI.
"""

import json
import re
from typing import Dict, Optional
import time

from src.services.database import get_db_connection
from src.services.ai_parser import AIParser
import os
import sys
from dotenv import load_dotenv

# Add project root to path
sys.path.insert(0, str(os.path.abspath(os.path.dirname(__file__))))

from src.database import get_db_session
from src.models import Campaign, Sector

load_dotenv()

# Configure Gemini
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
if not GEMINI_API_KEY:
    print("❌ GEMINI_API_KEY not found in .env")
    sys.exit(1)

genai.configure(api_key=GEMINI_API_KEY)
model = genai.GenerativeModel("gemini-2.5-flash-lite")

VALID_SECTORS = [
    "Market & Gıda", "Akaryakıt", "Giyim & Aksesuar", "Restoran & Kafe", 
    "Elektronik", "Mobilya & Dekorasyon", "Kozmetik & Sağlık", "E-Ticaret", 
    "Ulaşım", "Dijital Platform", "Kültür & Sanat", "Eğitim", "Sigorta", 
    "Otomotiv", "Vergi & Kamu", "Turizm & Konaklama", "Kuyum, Optik ve Saat"
]

def get_best_sector(title, description):
    prompt = f"""
    Aşağıdaki kredi kartı kampanyasını analiz et ve sadece listedeki sektör isimlerinden birini seç.
    
    KAMPANYA:
    Başlık: {title}
    Açıklama: {description}
    
    YALNIZCA BU LİSTEDEN SEÇ (Parantezleri Yazma):
    - Market & Gıda
    - Akaryakıt
    - Giyim & Aksesuar
    - Restoran & Kafe
    - Elektronik
    - Mobilya & Dekorasyon
    - Kozmetik & Sağlık
    - E-Ticaret
    - Ulaşım
    - Dijital Platform
    - Kültür & Sanat
    - Eğitim
    - Sigorta
    - Otomotiv
    - Vergi & Kamu
    - Turizm & Konaklama
    - Kuyum, Optik ve Saat
    
    Eğer hiçbirine uymuyorsa "Diğer" cevabını ver.
    Cevabın sadece sektör adı olsun.
    """
    
    try:
        response = model.generate_content(prompt)
        result = response.text.strip()
        # Clean up any potential markdown or extra text
        for s in VALID_SECTORS + ["Diğer"]:
            if s.lower() in result.lower():
                return s
        return "Diğer"
    except Exception as e:
        print(f"   ⚠️ AI Error: {e}")
        return "Diğer"

def run_sector_fix():
    print("🚀 Starting Sector Auto-Fixer...")
    
    session = get_db_session()
    
    try:
        # Get 'Other' sector
        diger_sector = session.query(Sector).filter(Sector.slug == "diger").first()
        if not diger_sector:
            print("❌ 'diger' sector not found in database.")
            return

        # Fetch all sectors for mapping
        all_sectors = session.query(Sector).all()
        sector_map = {s.name: s.id for s in all_sectors}

        # Find campaigns in 'Other'
        campaigns = session.query(Campaign).filter(
            Campaign.sector_id == diger_sector.id,
            Campaign.is_active == True
        ).all()

        print(f"🔍 Found {len(campaigns)} active campaigns in 'Diğer' sector.")

        fixed_count = 0
        for i, camp in enumerate(campaigns, 1):
            print(f"[{i}/{len(campaigns)}] Analiz ediliyor: {camp.title[:50]}...")
            
            best_sector_name = get_best_sector(camp.title, camp.description or "")
            
            if best_sector_name != "Diğer" and best_sector_name in sector_map:
                new_id = sector_map[best_sector_name]
                print(f"   ✨ Yeni Sektör: {best_sector_name} (ID: {new_id})")
                camp.sector_id = new_id
                session.commit()
                fixed_count += 1
            else:
                print(f"   ℹ️ Değişiklik Yok: Diğer olarak kaldı.")
            
            # Rate limiting
            time.sleep(1)

        print(f"\n🏁 İşlem tamamlandı. {fixed_count} kampanya yeni sektöre taşındı.")

    except Exception as e:
        print(f"❌ Error: {e}")
        session.rollback()
    finally:
        session.close()

if __name__ == "__main__":
    run_sector_fix()
