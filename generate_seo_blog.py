import os
import random
import time
import psycopg2
from dotenv import load_dotenv
from slugify import slugify

# Load environments
load_dotenv()

# Setup Database Connection
DB_URL = os.getenv("DATABASE_URL")
if not DB_URL:
    raise ValueError("DATABASE_URL must be set in .env")

# Setup Gemini API (using Vertex AI or legacy fallback)
from src.utils.gemini_client import generate_with_rotation

_GEMINI_MODEL_NAME = os.getenv("GEMINI_MODEL", "gemini-2.0-flash-lite")
MODEL_NAME = _GEMINI_MODEL_NAME

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
    
    html_content = generate_with_rotation(
        prompt=prompt,
        model=MODEL_NAME
    )
    
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
    return generate_with_rotation(
        prompt=prompt,
        model=MODEL_NAME
    )

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

def get_existing_titles_and_slugs():
    """Fetch existing titles and slugs from the database to avoid duplicates."""
    print("🔍 Checking database for existing blog posts...")
    conn = None
    titles = set()
    slugs = set()
    try:
        conn = psycopg2.connect(DB_URL)
        cur = conn.cursor()
        cur.execute('SELECT title, slug FROM "blogs"')
        rows = cur.fetchall()
        for row in rows:
            titles.add(row[0].lower().strip())
            slugs.add(row[1])
    except Exception as e:
        print(f"⚠️ Could not fetch existing blogs: {e}")
    finally:
        if conn:
            conn.close()
    return titles, slugs

def main():
    print("🚀 Starting Kartavantaj SEO Auto-Blog Generator")
    
    # 1. Fetch existing data to avoid duplicates
    existing_titles, existing_slugs = get_existing_titles_and_slugs()
    
    # 2. Filter topics that haven't been written yet
    available_topics = [t for t in TOPICS if t.lower().strip() not in existing_titles]
    
    if not available_topics:
        print("📭 No new topics to write about! All topics in TOPICS list already exist in database.")
        return

    # 3. Pick a random topic (from available ones) and image
    topic = random.choice(available_topics)
    image_url = random.choice(COVER_IMAGES)
    
    print(f"📝 Selected New Topic: '{topic}'")
    
    # Check if a slug collision might happen (though save_to_database adds a timestamp, stay safe)
    potential_slug = slugify(topic)
    if any(s.startswith(potential_slug) for s in existing_slugs):
        print(f"💡 Note: A similar slug already exists for '{topic}', but we will proceed with a unique timestamped slug.")

    # 4. Generate content
    html_content = generate_seo_article(topic)
    
    # 5. Generate meta description
    meta_description = generate_meta_description(topic, html_content)
    
    # 6. Save to DB
    save_to_database(topic, html_content, meta_description, image_url)
    print("✨ Process completed successfully.")

if __name__ == "__main__":
    main()
