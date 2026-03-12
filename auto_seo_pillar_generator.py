"""
auto_seo_pillar_generator.py
----------------------------
Tamamen otonom SEO Pillar Page (Destansı Rehber) üretici.

Çalışma mantığı:
1. Veritabanındaki search_logs ve missing_searches tablolarını analiz eder.
2. Henüz blog/pillar page'i olmayan, en çok aranan anahtar kelimeleri tespit eder.
3. Bu kelime üzerine Vertex AI (Gemini) ile kapsamlı, SEO uyumlu bir Pillar Page yazar.
4. Oluşturulan sayfayı anında 'blogs' tablosuna 'Pillar' kategorisiyle yayınlar.
5. Tamamen otonom çalışır — sıfır manuel müdahale.
"""

import os
import time
import re
import psycopg2
from dotenv import load_dotenv
from slugify import slugify

load_dotenv()

# ─────────────────────────────────────────────
# YAPILANDIRMA
# ─────────────────────────────────────────────

DB_URL = os.getenv("DATABASE_URL")
if not DB_URL:
    raise ValueError("DATABASE_URL must be set in .env")

# Gemini / Vertex AI kurulumu
from src.utils.gemini_client import generate_with_rotation

_GEMINI_MODEL_NAME = os.getenv("GEMINI_MODEL", "gemini-2.0-flash-lite")
MODEL_NAME = _GEMINI_MODEL_NAME

# Minimum kaç kez aratılmış olması gerektiği
MIN_SEARCH_COUNT = 3

# Bunların bloglarda geçip geçmediğini filtrelemek için çıkarılacak stop-words
SEARCH_STOP_WORDS = {
    "a", "ve", "ile", "için", "ya", "gibi", "mı", "mi", "mu", "mü",
    "bu", "şu", "o", "bir", "de", "da", "ki", "ne", "en", "ben",
    "sen", "biz", "siz", "onlar", "nasıl", "neden", "hangi", "ne zaman"
}

# Sabit Unsplash kapak görselleri (finance temalı)
COVER_IMAGES = [
    "https://images.unsplash.com/photo-1554224155-6726b3ff858f?w=1000&q=80",
    "https://images.unsplash.com/photo-1563013544-824ae1b704d3?w=1000&q=80",
    "https://images.unsplash.com/photo-1553729459-efe14ef6055d?w=1000&q=80",
    "https://images.unsplash.com/photo-1579621970563-ebec7560ff3e?w=1000&q=80",
    "https://images.unsplash.com/photo-1526304640581-d334cdbbf45e?w=1000&q=80",
    "https://images.unsplash.com/photo-1559526324-4b87b5e36e44?w=1000&q=80",
]


# ─────────────────────────────────────────────
# ADIM 1: VERİTABANI ANALİZİ
# ─────────────────────────────────────────────

def get_trending_keywords() -> list[tuple[str, int]]:
    """
    Son 60 gündeki search_logs ve missing_searches tablolarını harmanlayarak
    en çok aranan anahtar kelimeleri döndürür.
    Dönen liste: [(keyword, count), ...] — count'a göre azalan şekilde sıralı.
    """
    conn = None
    keywords: dict[str, int] = {}
    try:
        conn = psycopg2.connect(DB_URL)
        cur = conn.cursor()

        # search_logs: son 60 gün
        cur.execute("""
            SELECT LOWER(TRIM(query)), COUNT(*) as cnt
            FROM search_logs
            WHERE searched_at >= NOW() - INTERVAL '60 days'
              AND query IS NOT NULL
              AND LENGTH(TRIM(query)) > 3
            GROUP BY LOWER(TRIM(query))
            HAVING COUNT(*) >= %s
            ORDER BY cnt DESC
            LIMIT 100;
        """, (MIN_SEARCH_COUNT,))
        for row in cur.fetchall():
            query, cnt = row
            if _is_valid_keyword(query):
                keywords[query] = keywords.get(query, 0) + cnt

        # missing_searches: organik trafik arama eksiklik tablosu
        cur.execute("""
            SELECT LOWER(TRIM(query)), search_count
            FROM missing_searches
            WHERE is_resolved = FALSE
              AND LENGTH(TRIM(query)) > 3
            ORDER BY search_count DESC
            LIMIT 50;
        """)
        for row in cur.fetchall():
            query, cnt = row
            if _is_valid_keyword(query):
                keywords[query] = keywords.get(query, 0) + cnt

    except Exception as e:
        print(f"[HATA] Arama logları okunurken hata: {e}")
    finally:
        if conn:
            conn.close()

    # Count'a göre azalan şekilde sırala
    sorted_kws = sorted(keywords.items(), key=lambda x: x[1], reverse=True)
    print(f"[ANALİZ] {len(sorted_kws)} trend anahtar kelime tespit edildi.")
    return sorted_kws


def _is_valid_keyword(kw: str) -> bool:
    """Çok kısa, sadece sayı, stop-word olan veya URL olan kelimeleri filtreler."""
    kw = kw.strip()
    if len(kw) < 6:
        return False
    if re.match(r"^\d+$", kw):
        return False
    words = kw.split()
    # Sadece stop-word içeren sorguları at
    if all(w in SEARCH_STOP_WORDS for w in words):
        return False
    # URL veya teknik ifadeler
    if any(c in kw for c in ["http", "www.", ".com", ".tr", "/"]):
        return False
    return True


def get_existing_blog_slugs() -> set[str]:
    """Mevcut blog slug'larını ve başlıklarını çeker."""
    conn = None
    slugs: set[str] = set()
    titles: set[str] = set()
    try:
        conn = psycopg2.connect(DB_URL)
        cur = conn.cursor()
        cur.execute('SELECT slug, LOWER(TRIM(title)) FROM blogs')
        for slug, title in cur.fetchall():
            slugs.add(slug)
            titles.add(title)
    except Exception as e:
        print(f"[HATA] Mevcut bloglar okunurken hata: {e}")
    finally:
        if conn:
            conn.close()
    return slugs, titles


def pick_best_missing_topic(trending: list[tuple[str, int]], existing_titles: set[str]) -> str | None:
    """
    Trending listesinden henüz Pillar Page'i olmayan en iyi konuyu seçer.
    Eğer zaten yeterince 'kredi kartı' veya temel terimler varsa onlara benzer konuları atlar.
    """
    for keyword, count in trending:
        # Bu konu için benzer bir blog başlığı zaten var mı?
        already_exists = any(keyword in t for t in existing_titles)
        if not already_exists:
            print(f"[SEÇİM] Konu: '{keyword}' (aranma: {count}x) — Henüz sayfası yok, seçildi!")
            return keyword
    return None


# ─────────────────────────────────────────────
# ADIM 2: VERTEX AI İLE İÇERİK ÜRETİMİ
# ─────────────────────────────────────────────

def generate_pillar_content(keyword: str) -> str:
    """Seçilen anahtar kelime üzerine kapsamlı bir Pillar Page HTML içeriği üretir."""
    print(f"[AI] '{keyword}' için Pillar Page içeriği üretiliyor...")
    prompt = f"""
    Sen "Kartavantaj" isimli Türkiye'nin lider kredi kartı ve banka kampanyaları platformunun Baş Editörüsün.
    
    Görev: Aşağıdaki anahtar kelime üzerine Google'ın arama sonuçlarında üst sıraya taşıyacak, 
    çok kapsamlı, "Destansı Rehber (Pillar Page)" niteliğinde bir sayfa yaz.
    
    Anahtar Kelime / Konu: "{keyword}"
    
    KURALLAR:
    1. En az 1000-1500 kelime uzunluğunda olmalı. Kısa tutma — bu bir destansı rehberdir.
    2. Google'ın E-E-A-T (Deneyim, Uzmanlık, Otorite, Güven) standartlarına tam uygun ama doğal bir dil kullan.
    3. Tamamen HTML formatında yaz: <h2>, <h3>, <p>, <ul>, <li>, <strong>, <em> etiketlerini kullan. 
       ASLA Markdown veya <h1> kullanma.
    4. İçeriğin içinde doğal bir şekilde "kredi kartı kampanyaları", "kredi kartı", "kampanyalar" kelimelerini geç.
    5. Sonuç paragrafında kullanıcıyı aksiyon almaya yönlendir ("En güncel kampanyaları keşfetmek için tıklayın" gibi).
    6. Yanıt olarak SADECE HTML kodunu ver. Başka hiçbir şey ekleme.
    """
    html = generate_with_rotation(
        prompt=prompt,
        model=MODEL_NAME
    )
    # Markdown blok kalıntısı temizle
    if html.startswith("```html"):
        html = html[7:]
    if html.endswith("```"):
        html = html[:-3]
    return html.strip()


def generate_meta_description(keyword: str) -> str:
    """SEO uyumlu meta açıklaması üretir."""
    prompt = f"""
    Aşağıdaki Pillar Page konusu için Google arama sonuçlarında tıklamayı artıracak,
    maksimum 155 karakterlik bir Meta Açıklaması (Meta Description) yaz.
    Konu: "{keyword}"
    Yanıt olarak SADECE meta açıklamasını ver.
    """
    text = generate_with_rotation(prompt=prompt, model=MODEL_NAME)
    return text[:160]


def generate_seo_title(keyword: str) -> str:
    """Kullanıcı tıklamayı artıracak SEO başlığı üretir."""
    prompt = f"""
    Aşağıdaki konu için Google'da üst sıraya çıkacak, merak uyandıran, Türkçe bir H1 başlık yaz.
    Başlığa yıl (2026), "Rehber", "En İyi" veya "Kapsamlı" gibi CTR artırıcı kelimeler ekle.
    Konu: "{keyword}"
    Yanıt olarak SADECE başlık metnini ver. Tırnak işareti veya etiket kullanma.
    """
    return generate_with_rotation(prompt=prompt, model=MODEL_NAME)


# ─────────────────────────────────────────────
# ADIM 3: VERİTABANINA KAYDET
# ─────────────────────────────────────────────

def save_pillar_page(title: str, slug: str, html: str, meta: str, image_url: str):
    """Üretilen Pillar Page'i 'blogs' tablosuna 'Pillar' kategorisiyle kaydeder."""
    conn = None
    try:
        conn = psycopg2.connect(DB_URL)
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO blogs (title, slug, content_html, meta_description, image_url, category, is_published, created_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, NOW())
            RETURNING id;
        """, (title, slug, html, meta, image_url, "Pillar", True))
        blog_id = cur.fetchone()[0]
        conn.commit()
        print(f"[BAŞARILI] Pillar Page kaydedildi! ID: {blog_id}")
        print(f"[URL] /blog/{slug}")
    except Exception as e:
        print(f"[HATA] Kayıt hatası: {e}")
        if conn:
            conn.rollback()
    finally:
        if conn:
            conn.close()


# ─────────────────────────────────────────────
# ANA PROGRAM
# ─────────────────────────────────────────────

def main():
    import random
    print("=" * 60)
    print("🤖 Kartavantaj — Otonom SEO Pillar Page Üretici")
    print("=" * 60)

    # 1. Trend anahtar kelimeleri al
    trending = get_trending_keywords()
    if not trending:
        print("[BİLGİ] Henüz yeterli arama logu yok veya trend kelime bulunamadı.")
        return

    # 2. Mevcut blogları al
    existing_slugs, existing_titles = get_existing_blog_slugs()
    print(f"[VT] Mevcut {len(existing_slugs)} blog/pillar sayfası var.")

    # 3. En iyi eksik konuyu seç
    keyword = pick_best_missing_topic(trending, existing_titles)
    if not keyword:
        print("[BİLGİ] Tüm trend kelimeler için zaten sayfa mevcut. Bugün ekleme yapılmayacak.")
        return

    # 4. AI ile başlık, içerik ve meta üret
    seo_title = generate_seo_title(keyword)
    html_content = generate_pillar_content(keyword)
    meta_desc = generate_meta_description(keyword)

    # 5. Benzersiz slug yap
    base_slug = slugify(seo_title or keyword)
    slug = f"{base_slug}-rehber-{int(time.time())}"

    # 6. Kapak görseli seç
    image_url = random.choice(COVER_IMAGES)

    print(f"\n[ÖZEt]")
    print(f"  Başlık : {seo_title}")
    print(f"  Slug   : {slug}")
    print(f"  Meta   : {meta_desc[:80]}...")
    print(f"  HTML   : {len(html_content)} karakter üretildi.")

    # 7. Kaydet
    save_pillar_page(seo_title, slug, html_content, meta_desc, image_url)

    print("\n✅ Otonom SEO Pillar Page üretim döngüsü tamamlandı!")


if __name__ == "__main__":
    main()
