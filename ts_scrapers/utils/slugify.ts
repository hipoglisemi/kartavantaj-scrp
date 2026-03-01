/**
 * Türkçe karakterleri İngilizce karşılıklarına çevirir
 */
function turkishToEnglish(text: string): string {
    const charMap: Record<string, string> = {
        'ç': 'c', 'Ç': 'C',
        'ğ': 'g', 'Ğ': 'G',
        'ı': 'i', 'İ': 'I',
        'ö': 'o', 'Ö': 'O',
        'ş': 's', 'Ş': 'S',
        'ü': 'u', 'Ü': 'U'
    };

    return text.split('').map(char => charMap[char] || char).join('');
}

/**
 * Sektör slug'ı oluşturur (category → sector_slug)
 */
export function generateSectorSlug(category: string): string {
    if (!category) return 'diger';

    const normalized = category.toLowerCase().trim();

    // 🚨 MASTER SECTORS MAPPING (Policy Enforcement)
    // Matches the exact slugs in 'master_sectors' table
    if (normalized.includes('market') || normalized.includes('gıda')) return 'market-gida';
    if (normalized.includes('giyim') || normalized.includes('aksesuar')) return 'giyim-aksesuar';
    if (normalized.includes('mobilya') || normalized.includes('dekorasyon')) return 'mobilya-dekorasyon';
    if (normalized.includes('elektronik') || normalized.includes('teknoloji')) return 'elektronik';
    if (normalized.includes('restoran') || normalized.includes('kafe') || normalized.includes('cafe')) return 'restoran-kafe';
    if (normalized.includes('seyahat') || normalized.includes('konaklama') || normalized.includes('otel') || normalized.includes('turizm')) return 'turizm-konaklama';
    if (normalized.includes('akaryakıt') || normalized.includes('benzin') || normalized.includes('otogaz')) return 'akaryakit';
    if (normalized.includes('kozmetik') || normalized.includes('sağlık')) return 'kozmetik-saglik';
    if (normalized.includes('e-ticaret') || normalized.includes('internet')) return 'e-ticaret';
    if (normalized.includes('ulaşım') || normalized.includes('bilet') || normalized.includes('uçak')) return 'ulasim';
    if (normalized.includes('kuyum') || normalized.includes('optik') || normalized.includes('saat')) return 'kuyum-optik-saat';
    if (normalized.includes('kültür') || normalized.includes('sanat') || normalized.includes('sinema') || normalized.includes('tiyatro')) return 'kultur-sanat';
    if (normalized.includes('eğitim') || normalized.includes('okul') || normalized.includes('kırtasiye')) return 'egitim';
    if (normalized.includes('dijital') || normalized.includes('platform') || normalized.includes('oyun')) return 'dijital-platform';
    if (normalized.includes('sigorta') || normalized.includes('kasko')) return 'sigorta';
    if (normalized.includes('otomotiv') || normalized.includes('servis') || normalized.includes('lastik')) return 'otomotiv';
    if (normalized.includes('vergi') || normalized.includes('kamu') || normalized.includes('belediye')) return 'vergi-kamu';

    // Fallback just in case, but keep it cleaner
    return turkishToEnglish(category)
        .toLowerCase()
        .replace(/&/g, '') // Remove ampersand completely
        .replace(/\s+ve\s+/g, '-') // replace " ve " with dash
        .replace(/[^a-z0-9\s-]/g, '')
        .replace(/\s+/g, '-')
        .replace(/-+/g, '-')
        .trim();
}

/**
 * Kampanya slug'ı oluşturur (title → slug)
 * Format: "baslik-kelimeler-ID"
 */
export function generateCampaignSlug(title: string, id?: number): string {
    if (!title) return id ? `kampanya-${id}` : 'kampanya';

    // Türkçe karakterleri çevir
    let slug = turkishToEnglish(title);

    // Temizle ve formatla
    slug = slug
        .toLowerCase()
        .replace(/&/g, 've')
        .replace(/[^a-z0-9\s-]/g, '') // Sadece harf, rakam, boşluk ve tire
        .replace(/\s+/g, '-')         // Boşlukları tire yap
        .replace(/-+/g, '-')          // Çift tireleri tek tire yap
        .trim()
        .replace(/^-+|-+$/g, '');     // Baş ve sondaki tireleri kaldır

    // Çok uzunsa kısalt (max 60 karakter)
    if (slug.length > 60) {
        slug = slug.substring(0, 60).replace(/-[^-]*$/, '');
    }

    // ID varsa sona ekle
    return id ? `${slug}-${id}` : slug;
}

/**
 * Slug'dan ID çıkarır
 */
export function extractIdFromSlug(slug: string): number | null {
    const match = slug.match(/-(\d+)$/);
    return match ? parseInt(match[1], 10) : null;
}
