import os
import random
import time
import psycopg2
import google.generativeai as genai
from dotenv import load_dotenv
from slugify import slugify

# Load environments
load_dotenv()

# Setup Database Connection
DB_URL = os.getenv("DATABASE_URL")
if not DB_URL:
    raise ValueError("DATABASE_URL must be set in .env")

# Setup Gemini API (using the first key or main key)
GEMINI_KEY = os.getenv("GEMINI_API_KEY_1") or os.getenv("GEMINI_API_KEY")
if not GEMINI_KEY:
    raise ValueError("Gemini API key is missing in .env")

genai.configure(api_key=GEMINI_KEY)

# Use our fast & lightweight model
MODEL_NAME = "gemini-2.5-flash" # Note: 'gemini-2.5-flash-lite' can be used if accessible, falling back to flash for stability

# Topic ideas to randomly select from
TOPICS = [
    "Kredi Kartı ile Uçak Bileti Alırken Dikkat Edilmesi Gerekenler",
    "Mil Kartları ile Bedava Seyahat Etmenin Sırları",
    "Öğrenciler İçin En Avantajlı Kredi Kartları ve Kampanyaları",
    "Kredi Kartı Puanlarını Nakite Çevirme Yöntemleri",
    "Temassız Ödemelerde Güvenlik: Kredi Kartınızı Nasıl Korursunuz?",
    "Hangi Kredi Kartı Hangi Sektörde Daha Çok Kazandırıyor?",
    "En İyi Nakit İade (Cashback) Sağlayan Kredi Kartları İncelemesi",
    "Kredi Kartı Limit Artırımı Nasıl Yapılır, Nelere Dikkat Edilmeli?",
    "Taksitli Nakit Avans Çekerken Bilinmesi Gereken Püf Noktalar",
    "Yurt Dışı Alışverişlerinde Kredi Kartı Komisyonlarından Kaçınma Rehberi"
]

# High quality Unsplash Finance/Travel images to act as cover photos 
COVER_IMAGES = [
    "https://images.unsplash.com/photo-1554224155-6726b3ff858f?w=1000&q=80", # Finance
    "https://images.unsplash.com/photo-1563013544-824ae1b704d3?w=1000&q=80", # Credit Card
    "https://images.unsplash.com/photo-1553729459-efe14ef6055d?w=1000&q=80", # Money
    "https://images.unsplash.com/photo-1579621970563-ebec7560ff3e?w=1000&q=80", # Savings
    "https://images.unsplash.com/photo-1526304640581-d334cdbbf45e?w=1000&q=80", # Banking
    "https://images.unsplash.com/photo-1559526324-4b87b5e36e44?w=1000&q=80", # Shopping
    "https://images.unsplash.com/photo-1523240715632-99045506a591?w=1000&q=80", # Students
    "https://images.unsplash.com/photo-1434030216411-0b793f4b4173?w=1000&q=80", # Study
    "https://images.unsplash.com/photo-1516321318423-f06f85e504b3?w=1000&q=80", # Tech
    "https://images.unsplash.com/photo-1556742049-3fa374895692?w=1000&q=80"  # Business
]

def generate_seo_article(topic):
    print(f"🤖 Generating SEO Article with Gemini 2.5 Flash Lite for topic: '{topic}'")
    
    prompt = f"""
    Sen, "Kartavantaj" isimli lider bir kredi kartı ve kampanya platformunun Baş Editörüsün.
    Görevimiz: Google SEO kurallarına %100 uyumlu, yüksek kaliteli, tamamen Türkçe ve özgün bir blog makalesi yazmak.
    
    Makale Konusu: "{topic}"
    
    **KURALLAR:**
    1. Makale en az 800 - 1000 kelime uzunluğunda, derinlemesine bilgi veren ve kapsamlı bir rehber niteliğinde olmalıdır.
    2. Bir edebiyat öğretmeni titizliğiyle; kusursuz bir Türkçe, mükemmel imla kuralları ve son derece akıcı, profesyonel bir dil kullanılmalıdır.
    3. HTML formatında olmalıdır. Markdown KULLANMA. Sadece <p>, <h2>, <h3>, <ul>, <li>, <strong>, <em> etiketlerini kullan. Başlık için <h1> kullanma.
    4. Başlıklar ve paragraflar estetik bir düzen içinde, SEO uyumlu ve ilgi çekici olmalıdır.
    5. Yanıt olarak SADECE makalenin HTML kodunu ver.
    """
    
    model = genai.GenerativeModel('gemini-2.5-flash-lite')
    response = model.generate_content(prompt)
    
    html_content = response.text.strip()
    
    # Remove markdown codeblocks if AI messed up
    if html_content.startswith("```html"):
        html_content = html_content[7:]
    if html_content.endswith("```"):
        html_content = html_content[:-3]
        
    return html_content.strip()

def generate_meta_description(topic, html_content):
    prompt = f"""
    Aşağıdaki makale konusu için SEO uyumlu, Google arama sonuçlarında (SERP) gözükecek 150 karakteri aşmayan, tıklamaya teşvik eden bir Meta Açıklaması (Meta Description) yaz. 
    İçeriğe tıklatma (Call to Action) duygusu barındırsın. Yanıt olarak SADECE meta açıklamasını ver.
    Konu: {topic}
    """
    model = genai.GenerativeModel('gemini-2.5-flash-lite')
    response = model.generate_content(prompt)
    return response.text.strip()

def save_to_database(topic, html_content, meta_description, image_url):
    print(f"💾 Saving article to Superbase Postgres 'blogs' table...")
    base_slug = slugify(topic)
    slug = f"{base_slug}-{int(time.time())}" # Ensure absolute uniqueness
    
    conn = None
    try:
        conn = psycopg2.connect(DB_URL)
        cur = conn.cursor()
        
        insert_query = """
        INSERT INTO "blogs" 
        (title, slug, content_html, meta_description, image_url, category, is_published, created_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s, NOW())
        RETURNING id;
        """
        
        cur.execute(insert_query, (
            topic, 
            slug, 
            html_content, 
            meta_description, 
            image_url, 
            "Rehber", 
            True
        ))
        
        blog_id = cur.fetchone()[0]
        conn.commit()
        
        print(f"✅ Successfully inserted Blog Post! Database ID: {blog_id}")
        print(f"🌐 Expected URL: /blog/{slug}")
        
    except Exception as e:
        print(f"❌ Database error: {e}")
        if conn:
            conn.rollback()
    finally:
        if conn:
            cur.close()
            conn.close()

def main():
    print("🚀 Starting Kartavantaj SEO Auto-Blog Generator")
    # 1. Pick a random topic and image
    topic = random.choice(TOPICS)
    image_url = random.choice(COVER_IMAGES)
    
    # 2. Generate content
    html_content = generate_seo_article(topic)
    
    # 3. Generate meta description
    meta_description = generate_meta_description(topic, html_content)
    
    # 4. Save to DB
    save_to_database(topic, html_content, meta_description, image_url)
    print("✨ Process completed successfully.")

if __name__ == "__main__":
    main()
