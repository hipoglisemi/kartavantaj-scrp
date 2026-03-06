import os
import json
from dotenv import load_dotenv

# load environment variables for groq test
load_dotenv(".env")

from src.services.ai_parser import AIParser

# Sample raw text that previously returned empty conditions for Chippin
sample_text = """
Setur'dan 7.500 TL'ye varan Worldpuan
Setur'da Seçili Otellerde %50'ye varan Erken Rezervasyon İndirimi Chippinlilere! 

Kampanya Koşulları:
Kampanya 1-31 Mart 2026 tarihleri arasında geçerlidir.
Chippin uygulaması üzerinden yapılacak Setur harcamalarında geçerlidir.
Kazanılan puanlar 15 Nisan'da yüklenecektir.
Kişi başı en fazla 7.500 TL puan kazanılabilir.
İptal ve iade işlemlerinde puanlar geri alınır.
Ticari kartlar kampanyaya dahil değildir.
"""

print("Initializing AI Parser (Groq)...")
parser = AIParser(model_name="llama-3.3-70b-versatile")

print("Parsing test campaign...")
result = parser.parse_campaign_data(
    raw_text=sample_text,
    title="Setur'dan 7.500 TL'ye varan Worldpuan",
    bank_name="Chippin",
    card_name="Chippin"
)

print("\n--- TEST RESULTS ---")
print(json.dumps(result, indent=2, ensure_ascii=False))

if result.get("conditions") and len(result.get("conditions")) > 0:
    print("\n✅ SUCCESS: Conditions were heavily populated!")
else:
    print("\n❌ FAILED: Conditions are still empty or invalid.")
