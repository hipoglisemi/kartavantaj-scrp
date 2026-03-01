// src/utils/dataExtractor.ts
import * as cheerio from 'cheerio';
import { getSectorsWithKeywords } from './sectorCache';

interface ExtractedData {
    valid_from?: string | null;
    valid_until?: string | null;
    min_spend?: number;
    min_spend_currency?: string;
    earning?: string | null;
    earning_currency?: string;
    discount?: string | null;
    max_discount?: number | null;
    max_discount_currency?: string;
    discount_percentage?: number | null;
    brand?: string | null;
    sector_slug?: string | null;
    category?: string | null;
    description?: string | null;
    eligible_cards?: string[]; // Replacing valid_cards
    participation_method?: string | null;
    spend_channel?: 'IN_STORE_POS' | 'ONLINE' | 'IN_APP' | 'MERCHANT_SPECIFIC' | 'MEMBER_MERCHANT' | 'UNKNOWN' | null;
    spend_channel_detail?: string | null;
    date_flags?: string[];
    math_flags?: string[];
    required_spend_for_max_benefit?: number | null;
    required_spend_currency?: string;
    has_mixed_currency?: boolean;
    ai_suggested_valid_until?: string | null;
    ai_suggested_math?: {
        min_spend?: number;
        earning?: string;
        max_discount?: number;
        discount_percentage?: number;
    } | null;
    is_bank_campaign?: boolean;
    sector_confidence?: 'high' | 'medium' | 'low';
    classification_method?: string;
    needs_manual_sector?: boolean;
    math_method?: string;
    needs_manual_math?: boolean;
    ai_marketing_text?: string;
    perk_text?: string | null;
    coupon_code?: string | null;
    reward_type?: string | null;
    needs_manual_reward?: boolean;
    conditions?: string[] | null;
}

// ... existing code ...

const MONTHS: Record<string, string> = {
    'ocak': '01', 'şubat': '02', 'mart': '03', 'nisan': '04', 'mayıs': '05', 'haziran': '06',
    'temmuz': '07', 'ağustos': '08', 'eylül': '09', 'ekim': '10', 'kasım': '11', 'aralık': '12'
};

const CURRENCY_SYMBOLS: Record<string, string> = {
    '₺': 'TRY', 'tl': 'TRY',
    '$': 'USD', 'usd': 'USD', 'dollar': 'USD', 'dolar': 'USD',
    '€': 'EUR', 'eur': 'EUR', 'avro': 'EUR',
    '£': 'GBP', 'gbp': 'GBP', 'sterlin': 'GBP'
};

function detectCurrency(text: string): string {
    const lower = text.toLowerCase();
    for (const [sym, code] of Object.entries(CURRENCY_SYMBOLS)) {
        if (lower.includes(sym)) return code;
    }
    return 'TRY';
}

/**
 * Normalizes Turkish text by stripping common suffixes (Phase 7.5)
 * Small, safe, and doesn't use fuzzy matching.
 */
export function normalizeTurkishText(text: string): string {
    if (!text) return '';
    // Basic cleaning and lowercasing
    // Replace Turkish İ with i and I with ı to handle case correctly
    let normalized = text
        .replace(/İ/g, 'i').replace(/I/g, 'ı')
        .toLowerCase()
        .replace(/['’]/g, ' ')
        .replace(/[.,:;!?]/g, ' ')
        .replace(/\s+/g, ' ')
        .trim();

    const tokens = normalized.split(' ');
    const cleanedTokens = tokens.map(token => {
        // Only strip if word is long enough to avoid stripping roots
        if (token.length <= 4) return token;

        // Controlled suffixes: -de/da, -den/dan, -in/ın/un/ün, -ler/lar, -li/lı/lu/lü
        return token
            .replace(/(?:lar|ler|dan|den|tan|ten|da|de|ta|te|ın|in|un|ün)$/i, '')
            .replace(/(?:li|lı|lu|lü)$/i, '');
    });

    return cleanedTokens.join(' ');
}

const SECTORS = [
    { slug: 'market-gida', name: 'Market & Gıda', keywords: ['migros', 'carrefoursa', 'şok market', 'a101', 'bim', 'getir', 'yemeksepeti market', 'kasap', 'şarküteri', 'fırın'] },
    { slug: 'akaryakit', name: 'Akaryakıt', keywords: ['shell', 'opet', 'bp', 'petrol ofisi', 'totalenergies', 'akaryakıt', 'benzin', 'motorin', 'lpg', 'istasyon'] },
    { slug: 'giyim-aksesuar', name: 'Giyim & Aksesuar', keywords: ['boyner', 'zara', 'h&m', 'mango', 'lcw', 'koton', 'giyim', 'ayakkabı', 'çanta', 'moda', 'aksesuar', 'takı', 'saat'] },
    { slug: 'restoran-kafe', name: 'Restoran & Kafe', keywords: ['restoran', 'yemeksepeti', 'getir yemek', 'starbucks', 'kahve', 'cafe', 'kafe', 'burger king', 'mcdonalds', 'fast food'] },
    { slug: 'elektronik', name: 'Elektronik', keywords: ['teknosa', 'vatan bilgisayar', 'media markt', 'apple', 'samsung', 'elektronik', 'beyaz eşya', 'telefon', 'bilgisayar', 'tablet', 'laptop', 'televizyon', 'klima', 'beyaz eşya'] },
    { slug: 'mobilya-dekorasyon', name: 'Mobilya & Dekorasyon', keywords: ['ikea', 'koçtaş', 'bauhaus', 'mobilya', 'dekorasyon', 'ev tekstili', 'yatak', 'mutfak', 'halı', 'iklimlendirme'] },
    { slug: 'kozmetik-saglik', name: 'Kozmetik & Sağlık', keywords: ['gratis', 'watsons', 'rossmann', 'sephora', 'kozmetik', 'kişisel bakım', 'eczane', 'sağlık', 'hastane', 'doktor', 'parfüm'] },
    { slug: 'e-ticaret', name: 'E-Ticaret', keywords: ['trendyol', 'hepsiburada', 'amazon', 'n11', 'pazarama', 'çiçeksepeti', 'e-ticaret', 'online alışveriş'] },
    { slug: 'ulasim', name: 'Ulaşım', keywords: ['thy', 'pegasus', 'türk hava yolları', 'havayolu', 'otobüs', 'ulaşım', 'araç kiralama', 'rent a car', 'martı', 'bitaksi', 'uber'] },
    { slug: 'dijital-platform', name: 'Dijital Platform', keywords: ['netflix', 'spotify', 'youtube premium', 'exxen', 'disney+', 'steam', 'playstation', 'xbox', 'dijital platform', 'oyun'] },
    { slug: 'kultur-sanat', name: 'Kültür & Sanat', keywords: ['sinema', 'tiyatro', 'konser', 'biletix', 'itunes', 'kitap', 'etkinlik', 'müze', 'sanat'] },
    { slug: 'egitim', name: 'Eğitim', keywords: ['okul', 'üniversite', 'kırtasiye', 'kurs', 'eğitim', 'öğrenim'] },
    { slug: 'sigorta', name: 'Sigorta', keywords: ['sigorta', 'kasko', 'poliçe', 'emeklilik'] },
    { slug: 'otomotiv', name: 'Otomotiv', keywords: ['otomotiv', 'servis', 'bakım', 'yedek parça', 'lastik', 'oto'] },
    { slug: 'vergi-kamu', name: 'Vergi & Kamu', keywords: ['vergi', 'mtv', 'belediye', 'e-devlet', 'kamu', 'fatura'] },
    { slug: 'turizm-konaklama', name: 'Turizm & Konaklama', keywords: ['otel', 'tatil', 'konaklama', 'turizm', 'acente', 'jolly tur', 'etstur', 'setur', 'yurt dışı', 'seyahat'] }
];

/**
 * Main extractor function (Phase 7.5)
 */
export async function extractDirectly(
    html: string,
    title: string,
    masterBrands: Array<{ name: string, sector_id?: number }> = []
): Promise<ExtractedData> {
    const $ = cheerio.load(html);

    // Isolated content
    const contentSelectors = ['.cmsContent', '.campaingDetail', 'main', 'article'];
    let targetHtml = '';
    for (const sel of contentSelectors) {
        const found = $(sel);
        if (found.length > 0) {
            targetHtml = found.html() || '';
            break;
        }
    }
    if (!targetHtml) targetHtml = $.html();

    const $$ = cheerio.load(targetHtml);
    $$('script, style, iframe, nav, footer, header, .footer, .header, .sidebar, #header, #footer').remove();

    const possibleCleanSelectors = ['.campaign-text', '.content-body', '.description', 'h2, p, li'];
    let cleanTextMatches: string[] = [];
    $$(possibleCleanSelectors.join(',')).each((_, el) => {
        cleanTextMatches.push($$(el).text());
    });

    const cleanText = cleanTextMatches.join(' ').replace(/\s+/g, ' ').trim() || $$.text().replace(/\s+/g, ' ').trim();
    const normalizedText = normalizeTurkishText(cleanText);

    // 1. Bank Campaign Detection (Point 1 - AI-Free)
    const { isBankCampaign } = await import('./bankCampaignDetector');
    const isBank = isBankCampaign(title, cleanText);
    if (isBank) {
        console.log(`   🏦 Bank Service Detected (Point 1), skipping AI classification.`);
        return {
            valid_from: null,
            valid_until: null,
            min_spend: 0,
            earning: null,
            discount: null,
            sector_slug: 'diger',
            category: 'Diğer',
            brand: null,
            description: cleanText,
            is_bank_campaign: true,
            classification_method: 'bank_detector',
            sector_confidence: 'high'
        };
    }

    // 2. Date & Math Extraction (Deterministic - Phase 7.5 & 8)
    const dates = parseDates(cleanText);
    const math = extractMathDetails(title, cleanText);
    const eligible_cards = extractValidCards(cleanText);
    const participation_method_deterministic = extractJoinMethod(cleanText);
    let participation_method: string | null = participation_method_deterministic;
    let ai_marketing_text = '';
    let conditions: string[] = [];

    // 3. Date & Math Referee Check (DEPRECATED - Moved to Full Surgical Pipeline)
    let ai_suggested_valid_until: string | null = null;
    let ai_suggested_math: any = null;
    let math_method = 'deterministic';
    let needs_manual_math = false;

    // 5. Classification (Deterministic ONLY)
    const localBrands = [...masterBrands];
    const dynamicSectors = await getSectorsWithKeywords();
    const classification = extractClassification(title, cleanText, localBrands, dynamicSectors);

    let sector_slug = classification.sector_slug;
    let confidence = classification.confidence;
    let classification_method = classification.method;
    let needs_manual_sector = confidence < 0.7; // Flag if deterministic is weak

    if (sector_slug === 'diger') needs_manual_sector = true;

    // 6. Snippet AI Labeler (Minimal Token) - Triggered on Uncertainty
    const { channel: spend_channel, detail: spend_channel_detail } = extractSpendChannel(cleanText, classification.brand);

    let needs_manual_reward = false;
    const perkSignalRegex = /ücretsiz|bedava|kupon|promosyon\s*kodu|kod:|voucher|otopark|vale|geçiş|hgs|ogs|fast|bilet|hediye|çekiliş\s*katılım\s*kodu/i;

    // Trigger on missing essential operation/perk info
    const isUncertainReward = math.reward_type === 'unknown' ||
        (perkSignalRegex.test(cleanText) && !math.perk_text) ||
        !participation_method ||
        (spend_channel === 'UNKNOWN');

    if (isUncertainReward) {
        needs_manual_reward = true;
    }

    // 7. Final Marketing Enhancement (Sync with V7)
    if (!ai_marketing_text && (math.earning || math.discount)) {
        ai_marketing_text = `${title}. ${math.earning ? math.earning + ' kazanma fırsatı!' : ''} ${math.discount ? math.discount + ' imkanıyla.' : ''}`.trim();
    }

    return {
        valid_from: dates.valid_from,
        valid_until: dates.valid_until,
        date_flags: dates.date_flags,
        min_spend: math.min_spend,
        min_spend_currency: math.min_spend_currency,
        earning: math.earning,
        earning_currency: math.earning_currency,
        discount: math.discount,
        max_discount: math.max_discount,
        max_discount_currency: math.max_discount_currency,
        discount_percentage: math.discount_percentage,
        math_flags: math.math_flags,
        required_spend_for_max_benefit: math.required_spend_for_max_benefit,
        required_spend_currency: math.required_spend_currency,
        has_mixed_currency: math.has_mixed_currency,
        ai_suggested_valid_until,
        ai_suggested_math,
        brand: classification.brand,
        sector_slug,
        category: sector_slug === 'diger' ? 'Diğer' : (dynamicSectors.find(s => s.slug === sector_slug)?.name || classification.category),
        sector_confidence: confidence >= 0.7 ? 'high' : (confidence >= 0.3 ? 'medium' : 'low'),
        classification_method,
        needs_manual_sector,
        math_method,
        needs_manual_math,
        description: cleanText,
        eligible_cards: (math as any).eligible_cards || eligible_cards,
        participation_method: (math as any).participation_method || participation_method,
        spend_channel: (math as any).spend_channel || spend_channel,
        spend_channel_detail,
        reward_type: math.reward_type,
        needs_manual_reward,
        conditions: conditions.length > 0 ? conditions : null,
        ai_marketing_text: ai_marketing_text
    };
}

/**
 * Converts Turkish date strings (e.g., "31 Aralık 2025", "31.12.2025" or "31 Aralık") to ISO (2025-12-31)
 */
function parseTurkishDate(dateStr: string, defaultYear?: number): string | null {
    if (!dateStr) return null;
    const cleanStr = dateStr.trim().replace(/[.,/]/g, ' ').replace(/\s+/g, ' ');
    const parts = cleanStr.toLowerCase().split(' ');

    let day = '', month = '', year = '';
    const now = new Date();
    const currentYear = defaultYear || now.getFullYear();
    const currentMonth = now.getMonth() + 1;

    if (parts.length === 3) {
        day = parts[0].padStart(2, '0');
        const monthPart = parts[1];
        year = parts[2];
        if (/^\d{1,2}$/.test(monthPart)) {
            month = monthPart.padStart(2, '0');
        } else {
            month = MONTHS[monthPart] || '';
        }
    } else if (parts.length === 2) {
        day = parts[0].padStart(2, '0');
        month = MONTHS[parts[1]] || (/^\d{1,2}$/.test(parts[1]) ? parts[1].padStart(2, '0') : '');
        if (!month) return null;

        const monthNum = parseInt(month);
        if (monthNum < currentMonth - 1) {
            year = (currentYear + 1).toString();
        } else {
            year = currentYear.toString();
        }
    } else {
        return null;
    }

    if (!month || !day || !year) return null;
    if (day.length > 2 || year.length !== 4) return null; // Basic validation

    return `${year}-${month}-${day}`;
}

/**
 * Robust Date Parsing (Phase 7.5 Specifications)
 */
export function parseDates(text: string, today: Date = new Date()): {
    valid_from: string | null,
    valid_until: string | null,
    date_flags: string[]
} {
    let valid_from: string | null = null;
    let valid_until: string | null = null;
    let date_flags: string[] = [];

    // Filter out point usage context before parsing
    const normalized = text.replace(/\s+/g, ' ');

    // 1. DD.MM.YYYY - DD.MM.YYYY
    const fullNumericRange = /(\d{1,2})[./](\d{1,2})[./](\d{4})\s*[-–]\s*(\d{1,2})[./](\d{1,2})[./](\d{4})/g;
    let m = fullNumericRange.exec(normalized);
    if (m) {
        valid_from = `${m[3]}-${m[2].padStart(2, '0')}-${m[1].padStart(2, '0')}`;
        valid_until = `${m[6]}-${m[5].padStart(2, '0')}-${m[4].padStart(2, '0')}`;
        return { valid_from, valid_until, date_flags };
    }

    // 2. DD.MM - DD.MM (Yearless)
    const shortNumericRange = /(\d{1,2})[./](\d{1,2})\s*[-–]\s*(\d{1,2})[./](\d{1,2})/g;
    m = shortNumericRange.exec(normalized);
    if (m) {
        const fromDateObj = parseTurkishDate(`${m[1]}.${m[2]}`, today.getFullYear());
        const untilDateObj = parseTurkishDate(`${m[3]}.${m[4]}`, today.getFullYear());
        if (fromDateObj && untilDateObj) {
            valid_from = fromDateObj;
            valid_until = untilDateObj;
            if (valid_from > valid_until) {
                valid_until = parseTurkishDate(`${m[3]}.${m[4]}`, today.getFullYear() + 1);
            }
            date_flags.push('year_inferred');
            return { valid_from, valid_until, date_flags };
        }
    }

    // 3. 1-31 Ocak (Phase 7 logic but more robust) - FIXED for audit
    const textRangeRegex = /(\d{1,2})\s*[-–]\s*(\d{1,2})\s+(Ocak|Şubat|Mart|Nisan|Mayıs|Haziran|Temmuz|Ağustos|Eylül|Ekim|Kasım|Aralık)(?:\s+(\d{4}))?/gi;
    m = textRangeRegex.exec(normalized);
    if (m) {
        const startDay = parseInt(m[1]);
        const endDay = parseInt(m[2]);
        const monthName = m[3];
        const explicitYear = m[4] ? parseInt(m[4]) : null;

        // Determine year with improved logic
        const monthNum = parseInt(MONTHS[monthName.toLowerCase()]);
        const currentYear = today.getFullYear();
        const currentMonth = today.getMonth() + 1; // 0-indexed to 1-indexed

        let year: number;
        if (explicitYear) {
            year = explicitYear;
        } else {
            // Use current year if month is current or future, next year if past
            if (monthNum >= currentMonth) {
                year = currentYear;
            } else {
                year = currentYear + 1;
            }
            date_flags.push('year_inferred');
        }

        // Check for invalid range (e.g., "31-31 Aralık")
        if (startDay === endDay) {
            // Treat as single date, not a range
            const singleDateStr = `${endDay} ${monthName} ${year}`.trim();
            valid_until = parseTurkishDate(singleDateStr, year);
            valid_from = null;
            date_flags.push('single_date_from_invalid_range');
            return { valid_from, valid_until, date_flags };
        }

        // Valid range: construct from and until dates
        const monthStr = MONTHS[monthName.toLowerCase()];
        valid_from = `${year}-${monthStr}-${startDay.toString().padStart(2, '0')}`;
        valid_until = `${year}-${monthStr}-${endDay.toString().padStart(2, '0')}`;

        return { valid_from, valid_until, date_flags };
    }

    // 4. 1 Ocak’tan 31 Ocak’a kadar (Refined)
    const longTextRangeRegex = /(\d{1,2})\s+(Ocak|Şubat|Mart|Nisan|Mayıs|Haziran|Temmuz|Ağustos|Eylül|Ekim|Kasım|Aralık)\s*(?:'dan|'den|'tan|'ten|’dan|’den|’tan|’ten)?\s*(\d{1,2})\s+(Ocak|Şubat|Mart|Nisan|Mayıs|Haziran|Temmuz|Ağustos|Eylül|Ekim|Kasım|Aralık)\s*(?:'a|'e|’a|’e)?\s+kadar/gi;
    m = longTextRangeRegex.exec(normalized);
    if (!m) {
        const crossMonthRegex = /(\d{1,2})\s+(Ocak|Şubat|Mart|Nisan|Mayıs|Haziran|Temmuz|Ağustos|Eylül|Ekim|Kasım|Aralık)\s*[-–]\s*(\d{1,2})\s+(Ocak|Şubat|Mart|Nisan|Mayıs|Haziran|Temmuz|Ağustos|Eylül|Ekim|Kasım|Aralık)/gi;
        m = crossMonthRegex.exec(normalized);
    }
    if (m) {
        const fromStr = `${m[1]} ${m[2]}`;
        const untilStr = `${m[3]} ${m[4]}`;
        valid_from = parseTurkishDate(fromStr, today.getFullYear());
        valid_until = parseTurkishDate(untilStr, today.getFullYear());
        if (valid_from && valid_until) {
            if (valid_from > valid_until) {
                valid_until = parseTurkishDate(untilStr, today.getFullYear() + 1);
            }
            date_flags.push('year_inferred');
            return { valid_from, valid_until, date_flags };
        }
    }

    // Fallback: Individual dates with "until" signals
    const untilSignals = ["’a kadar", "a kadar", "e kadar", "son gün", "son tarih", "tarihine kadar"];
    const textDatePat = /(\d{1,2})\s+(Ocak|Şubat|Mart|Nisan|Mayıs|Haziran|Temmuz|Ağustos|Eylül|Ekim|Kasım|Aralık)(?:\s+(\d{4}))?/gi;
    const numericDatePat = /(\d{1,2})[./](\d{1,2})[./](\d{4})/g;

    let bestUntilMatch: string | null = null;
    let numericM;
    while ((numericM = numericDatePat.exec(normalized)) !== null) {
        const snippet = normalized.substring(Math.max(0, numericM.index - 20), numericM.index + 50).toLowerCase();
        if (untilSignals.some(s => snippet.includes(s))) {
            bestUntilMatch = `${numericM[3]}-${numericM[2].padStart(2, '0')}-${numericM[1].padStart(2, '0')}`;
        }
    }
    if (!bestUntilMatch) {
        let textM;
        while ((textM = textDatePat.exec(normalized)) !== null) {
            const snippet = normalized.substring(Math.max(0, textM.index - 20), textM.index + 50).toLowerCase();
            if (untilSignals.some(s => snippet.includes(s))) {
                const parsed = parseTurkishDate(textM[0], textM[3] ? parseInt(textM[3]) : today.getFullYear());
                if (parsed) {
                    bestUntilMatch = parsed;
                    if (!textM[3]) date_flags.push('year_inferred');
                }
            }
        }
    }

    // Last resort: Month-end heuristic
    if (!bestUntilMatch) {
        const endOfMonthMatch = normalized.match(/(Ocak|Şubat|Mart|Nisan|Mayıs|Haziran|Temmuz|Ağustos|Eylül|Ekim|Kasım|Aralık)\s+sonuna\s+kadar/i);
        if (endOfMonthMatch) {
            const monthNum = MONTHS[endOfMonthMatch[1].toLowerCase()];
            if (monthNum) {
                let year = today.getFullYear();
                if (parseInt(monthNum) < (today.getMonth() + 1) - 1) year++;
                const lastDay = new Date(year, parseInt(monthNum), 0).getDate();
                bestUntilMatch = `${year}-${monthNum.padStart(2, '0')}-${lastDay.toString().padStart(2, '0')}`;
                date_flags.push('year_inferred');
            }
        }
    }
    return { valid_from, valid_until: bestUntilMatch, date_flags };
}

export function extractMathDetails(title: string, content: string): {
    min_spend: number;
    min_spend_currency: string;
    earning: string | null;
    earning_currency: string;
    discount: string | null;
    max_discount: number | null;
    max_discount_currency: string;
    discount_percentage: number | null;
    math_flags: string[];
    required_spend_for_max_benefit: number | null;
    required_spend_currency: string;
    has_mixed_currency: boolean;
    perk_text: string | null;
    coupon_code: string | null;
    reward_type: string | null;
} {
    const combinedText = (title + ' ' + content).replace(/\./g, '').replace(/\s+/g, ' ');
    const lowerText = combinedText.toLowerCase();

    let min_spend = 0;
    let min_spend_currency = 'TRY';
    let earning: string | null = null;
    let earning_currency = 'TRY';
    let discount: string | null = null;
    let max_discount: number | null = null;
    let max_discount_currency = 'TRY';
    let discount_percentage: number | null = null;
    let math_flags: string[] = [];
    let required_spend_for_max_benefit: number | null = null;
    let has_mixed_currency = false;
    let perk_text: string | null = null;
    let coupon_code: string | null = null;
    let reward_type: string | null = null;

    // 1. Currency Detection & Mixed Flag
    const currencies = new Set<string>();
    const tryMatch = combinedText.match(/[₺]|TL/g);
    const usdMatch = combinedText.match(/[$]|USD|Dollar/gi);
    const eurMatch = combinedText.match(/[€]|EUR|Avro/gi);
    const gbpMatch = combinedText.match(/[£]|GBP|Sterlin/gi);

    if (tryMatch) currencies.add('TRY');
    if (usdMatch) currencies.add('USD');
    if (eurMatch) currencies.add('EUR');
    if (gbpMatch) currencies.add('GBP');

    if (currencies.size > 1) {
        has_mixed_currency = true;
        math_flags.push('mixed_currency');
    }

    // 2. Perk & Coupon Detection (Deterministic)
    const perkRegex = /ücretsiz|bedava|kupon|promosyon\s*kodu|kod:|voucher|otopark|vale|geçiş|hgs|ogs|fast|bilet|hediye|çekiliş\s*katılım\s*kodu/i;
    const couponRegex = /(?:kupon|kod|code|davet\s*kodu)\s*[:\-]?\s*([A-Z0-9]{4,15})/i;

    if (perkRegex.test(combinedText)) {
        reward_type = 'perk';
        // Extract a snippet for perk_text if it's the dominant signal
        const perkMatch = combinedText.match(/(?:ücretsiz|bedava|hediye)\s+[a-zçğıöşü\s]{3,20}/i);
        if (perkMatch) perk_text = perkMatch[0].trim();
    }

    const cMatch = combinedText.match(couponRegex);
    if (cMatch) {
        coupon_code = cMatch[1].toUpperCase();
        if (!reward_type) reward_type = 'perk';
    }

    // 3. Strong Explicit Patterns (Priority 1)
    const explicitMinSpendRegex = /(\d+[\d.,]*)\s*(tl|usd|eur|gbp|[$€£₺])\s*(?:ve\s+)?(?:üzeri|tutarında|harcamaya|yüklemeye|ödemeye|siparişinize|veya\s+üzeri|alışverişte|harcamanızda)/gi;
    const explicitMinMatches = [...combinedText.matchAll(explicitMinSpendRegex)];
    if (explicitMinMatches.length > 0) {
        min_spend = parseInt(explicitMinMatches[0][1].replace(/[.,]/g, ''));
        min_spend_currency = detectCurrency(explicitMinMatches[0][2]);
    } else {
        // Fallback to basic TL regex if no currency-aware match
        const basicMinRegex = /(\d+[\d.,]*)\s*tl\s*(?:ve\s+)?(?:üzeri|harcamaya|alışverişte)/gi;
        const basicMinMatch = combinedText.match(basicMinRegex);
        if (basicMinMatch) {
            min_spend = parseInt(basicMinMatch[0].match(/\d+/)![0]);
            min_spend_currency = 'TRY';
        }
    }

    const explicitCapRegex = /(?:max|maximum|toplam|toplamda|en fazla|varan|kadar)\s*(\d+[\d.,]*)\s*(tl|usd|eur|gbp|[$€£₺])/gi;
    const explicitCapMatches = [...combinedText.matchAll(explicitCapRegex)];
    if (explicitCapMatches.length > 0) {
        max_discount = parseInt(explicitCapMatches[0][1].replace(/[.,]/g, ''));
        max_discount_currency = detectCurrency(explicitCapMatches[0][2]);
    }

    const explicitPctRegex = /(?:%|yüzde)\s*(\d+)|(\d+)\s*%/gi;
    const explicitPctMatch = explicitPctRegex.exec(lowerText);
    if (explicitPctMatch) {
        discount_percentage = parseInt(explicitPctMatch[1] || explicitPctMatch[2]);
    }

    // 4. Proximity-Based Candidates (Minimal Token Reuse)
    const spendKeywords = ['harcama', 'alışveriş', 'tutarında', 'tek seferde', 'üzeri', 'peşin', 'yükleme', 'sipariş', 'ödeme'];
    const rewardKeywords = ['puan', 'chip-para', 'bonus', 'indirim', 'maxipuan', 'parafpara', 'bankkart lira', 'nakit', 'iade', 'taksit', 'kazan', 'hediye'];

    const numRegex = /(\d+[\d.,]*)(?:\s*(tl|%|kat|taksit|usd|eur|gbp|[$€£₺]))?/gi;
    const candidates: any[] = [];
    let m;
    while ((m = numRegex.exec(combinedText)) !== null) {
        const val = parseInt(m[1].replace(/[.,]/g, ''));
        const unit = (m[2] || 'tl').toLowerCase();
        let type = 'unknown';

        const snippetBefore = lowerText.substring(Math.max(0, m.index - 25), m.index);
        const snippetAfter = lowerText.substring(m.index, Math.min(lowerText.length, m.index + 40));

        if (spendKeywords.some(kw => snippetBefore.includes(kw) || snippetAfter.includes(kw))) type = 'spend';
        else if (rewardKeywords.some(kw => snippetBefore.includes(kw) || snippetAfter.includes(kw))) type = 'reward';

        candidates.push({ val, unit: detectCurrency(unit), type, index: m.index });
    }

    if (min_spend === 0) {
        const spendCand = candidates.filter(c => c.type === 'spend' && c.unit !== '%').sort((a, b) => b.val - a.val);
        if (spendCand.length > 0) {
            min_spend = spendCand[0].val;
            min_spend_currency = spendCand[0].unit === 'tl' ? 'TRY' : spendCand[0].unit;
        }
    }

    // 5. Extract earning / reward text
    const relaxedRewardRegex = /(\d+[\d.,]*)\s*(?:tl|%|usd|eur|gbp|[$€£₺]|['’](?:ye|ya|e|a)|lik|lık|lük|luk)?\s*(?:(?:ye|ya|e|a|lik|lık|lük|luk|ye varan|ya varan|ye kadar|ya kadar|['’](?:ye|ya|e|a)\s+(?:varan|kadar))\s+){0,3}(?:chip-para|puan|bonus|indirim|maxipuan|parafpara|taksit|kazan|kazandır|iade|fırsatı)/gi;
    const rewardMatches = combinedText.match(relaxedRewardRegex);
    if (rewardMatches) {
        earning = rewardMatches[0].trim();
        earning_currency = detectCurrency(earning);
        if (earning.toLowerCase().includes('taksit')) {
            discount = earning;
            earning = null;
        }
    }

    // 6. Final Calculation (Deterministic Hub)
    const mathResult: any = {
        min_spend,
        min_spend_currency,
        earning,
        earning_currency,
        max_discount,
        max_discount_currency,
        discount_percentage,
        math_flags,
        required_spend_for_max_benefit: null,
        required_spend_currency: min_spend_currency,
        has_mixed_currency
    };

    if (!has_mixed_currency) {
        recalculateMathRequirement(mathResult, combinedText);
    }

    // Reward Type Finalization
    if (!reward_type) {
        if (earning && discount) reward_type = 'mixed';
        else if (discount && discount.toLowerCase().includes('taksit')) reward_type = 'installment';
        else if (discount_percentage) reward_type = 'discount_pct';
        else if (earning && /(puan|bonus|chip|maxipuan|parafpara|lira)/i.test(earning)) reward_type = 'points';
        else if (earning && /iade|cashback|nakit/i.test(earning)) reward_type = 'cashback';
        else reward_type = 'unknown';
    }

    return {
        ...mathResult,
        discount,
        perk_text,
        coupon_code,
        reward_type
    };
}

export function recalculateMathRequirement(math: any, text: string): void {
    const lowerText = text.toLowerCase();

    // Formula Inputs
    const min_spend = math.min_spend || 0;
    const max_discount = math.max_discount;
    const discount_percentage = math.discount_percentage;
    const earning = (math.earning || '').toLowerCase();

    let requirement: number | null = null;

    // 1. Incremental Logic
    const incrementalRegex = /(?:her|her bir)\s+(\d+)[\d.,]*\s*tl(?:\s+için|\s+['’][ye|ya|e|a]|\s+harcamaya)?\s+(\d+)[\d.,]*\s*tl/i;
    const incMatch = text.match(incrementalRegex);

    if (incMatch) {
        const stepSpend = parseInt(incMatch[1].replace(/[.,]/g, ''));
        const stepReward = parseInt(incMatch[2].replace(/[.,]/g, ''));

        if (max_discount && stepReward > 0) {
            const steps = Math.ceil(max_discount / stepReward);
            requirement = steps * stepSpend;
            if (math.min_spend === 0) math.min_spend = stepSpend;
        } else if (!max_discount) {
            requirement = stepSpend;
            if (!math.math_flags.includes('no_cap_in_incremental')) math.math_flags.push('no_cap_in_incremental');
            if (math.min_spend === 0) math.min_spend = stepSpend;
        }
    }
    // 2. Percentage + Cap
    else if (max_discount && discount_percentage) {
        requirement = Math.ceil((max_discount * 100) / discount_percentage);
    }
    // 3. Cap without Rate
    else if (max_discount && !discount_percentage && !incMatch) {
        if (!math.math_flags.includes('cap_without_rate')) math.math_flags.push('cap_without_rate');
    }
    // 4. Basic Min Spend
    else if (min_spend > 0) {
        requirement = min_spend;
    }

    // Safety: min_spend is floor
    if (requirement && min_spend > 0) {
        requirement = Math.max(requirement, min_spend);
    }

    // Rule 2: Point-based rewards without caps
    const isPointsReward = earning && /(puan|bonus|chip-para|maxipuan|parafpara|bankkart lira)/i.test(earning);
    if (isPointsReward && !max_discount && !incMatch) {
        requirement = null;
        if (!math.math_flags.includes('no_cap_for_points_reward')) math.math_flags.push('no_cap_for_points_reward');
    }

    math.required_spend_for_max_benefit = requirement;

    // USER REQUIREMENT: min_spend should represent the total requirement for max benefit
    if (requirement !== null && requirement > 0) {
        math.min_spend = requirement;
    }
}

export function extractEarning(title: string, content: string): string | null {
    // Keep for backward compatibility or direct calls
    return extractMathDetails(title, content).earning;
}

export function extractMinSpend(text: string): number {
    // Keep for backward compatibility or direct calls
    return extractMathDetails('', text).min_spend;
}

/**
 * Extracts discount/installment info - EXPANDED for audit
 */
export function extractDiscount(title: string, content: string): string | null {
    // Expanded patterns to catch more taksit variations
    const installmentPatterns = [
        /(faizsiz|vade\s*farksız|masrafsız)\s+(\d+)\s+taksit/gi,
        /(ilave|ek|ekstra)\s+(\d+)\s+taksit/gi,
        /\+(\d+)\s+taksit/gi,
        /(peşin\s+fiyatına|peşine)\s+(\d+)\s+taksit/gi,
        /(\d+)\s+aya?\s+varan\s+taksit/gi,
        /(\d+)\s+taksit/gi // Generic fallback
    ];

    for (const pattern of installmentPatterns) {
        const titleMatch = title.match(pattern);
        if (titleMatch) {
            // Extract number from match
            const numMatch = titleMatch[0].match(/(\d+)/);
            if (numMatch) {
                return `${numMatch[1]} Taksit`;
            }
        }

        const contentMatch = content.match(pattern);
        if (contentMatch) {
            const numMatch = contentMatch[0].match(/(\d+)/);
            if (numMatch) {
                return `${numMatch[1]} Taksit`;
            }
        }
    }

    return null;
}

export function extractValidCards(text: string): string[] {
    const cards = ['Axess', 'Wings', 'Free', 'Akbank Kart', 'Neo', 'Ticari Kart'];
    const found: string[] = [];
    const lowerText = text.toLowerCase();

    cards.forEach(card => {
        if (lowerText.includes(card.toLowerCase())) {
            const index = lowerText.indexOf(card.toLowerCase());
            const context = lowerText.substring(index, index + 60);

            if (!context.includes('dahil değil') && !context.includes('geçerli değil')) {
                found.push(card);
            }
        }
    });
    return found;
}

export type ParticipationMethod = string;

export function extractJoinMethod(text: string): ParticipationMethod | null {
    const lowerText = text.toLowerCase();

    // Priority order: specific signals first
    if (lowerText.includes('juzdan') || lowerText.includes('juzdan ile')) return 'Juzdan ile katılım';

    // Expanded SMS signals for audit
    const smsSignals = [
        'sms',
        /kayıt\s+yazıp/i,
        /kayit\s+yaz/i,
        /katıl\s+yazıp/i,
        /katil\s+yaz/i,
        /gönder/i,
        /gonder/i,
        /\d{4}['']?e\s+sms/i,
        /\d{4}['']?e\s+gönder/i,
        /mesaj\s+gönder/i,
        /kısa\s+mesaj/i,
        /kisa\s+mesaj/i,
        /sms\s+ile/i
    ];

    for (const signal of smsSignals) {
        if (typeof signal === 'string') {
            if (lowerText.includes(signal)) return 'SMS ile katılım';
        } else {
            if (signal.test(text)) return 'SMS ile katılım';
        }
    }

    if (lowerText.includes('müşteri hizmetleri') || lowerText.includes('çağrı merkezi') || lowerText.includes('444 25 25')) return 'Müşteri Hizmetleri üzerinden katılım';
    if (lowerText.includes('mobil şube') || lowerText.includes('akbank mobil') || lowerText.includes('mobil uygulama')) return 'Mobil Uygulama üzerinden katılım';
    if (lowerText.includes('otomatik')) return 'Otomatik katılım';
    if (lowerText.includes('internet şubesi') || lowerText.includes('web sitesi')) return 'Web üzerinden katılım';
    if (lowerText.includes('otomatik') || lowerText.includes('başvuru gerekmez')) return 'AUTO';
    if (lowerText.includes('web') || lowerText.includes('internet şube')) return 'WEB';

    return null;
}

export type SpendChannel = 'IN_STORE_POS' | 'ONLINE' | 'IN_APP' | 'MERCHANT_SPECIFIC' | 'MEMBER_MERCHANT' | 'UNKNOWN';

export function extractSpendChannel(text: string, brand?: string | null): { channel: SpendChannel, detail: string | null } {
    const lowerText = text.toLowerCase();
    let detail: string | null = null;

    // 1. IN_APP (Priority - Juzdan/App specific shopping)
    if (lowerText.includes('juzdan üzerinden') || lowerText.includes('juzdan ile öde') || (lowerText.includes('mobil uygulama') && lowerText.includes('üzerinden'))) {
        return { channel: 'IN_APP', detail: null };
    }

    // 2. MERCHANT_SPECIFIC (brand + "mağazalarında/şubelerinde")
    if (brand) {
        const brandLower = brand.toLowerCase();
        if (lowerText.includes(`${brandLower} mağaza`) || lowerText.includes(`${brandLower} şube`) || lowerText.includes(`${brandLower} restoran`)) {
            return { channel: 'MERCHANT_SPECIFIC', detail: `${brand} Şubeleri` };
        }
    }

    // 3. ONLINE
    if (lowerText.includes('online') || lowerText.includes('internet site') || lowerText.includes('e-ticaret') || lowerText.includes('.com') || lowerText.includes('mobil uygulama')) {
        return { channel: 'ONLINE', detail: null };
    }

    // 4. IN_STORE_POS
    if (lowerText.includes(' pos ') || lowerText.includes(' pos\'') || lowerText.includes('fiziki') || lowerText.includes('mağaza') || lowerText.includes('üye işyeri')) {
        return { channel: 'IN_STORE_POS', detail: null };
    }

    // 5. MEMBER_MERCHANT
    if (lowerText.includes('üye işyeri') || lowerText.includes('anlaşmalı')) {
        return { channel: 'MEMBER_MERCHANT', detail: null };
    }

    return { channel: 'UNKNOWN', detail: null };
}

/**
 * Extracts brand and sector using master data hints
 * Strictly follows "ANTIGRAVITY TEK DÜZELTME KOMUTU" Points 4 & 5.
 */
export function extractClassification(
    title: string,
    content: string,
    masterBrands: Array<{ name: string, sector_id?: number }> = [],
    masterSectors: any[] = SECTORS
): {
    brand: string | null,
    sector_slug: string | null,
    category: string | null,
    confidence: number,
    method: string
} {
    const nTitle = normalizeTurkishText(title);
    const nContent = normalizeTurkishText(content); // content is already clean/normalized when passed

    // 1. Find Brand (Priority: Title Match > early content match)
    let foundBrand: { name: string, sector_id?: number } | null = null;
    const sortedBrands = [...masterBrands].sort((a, b) => b.name.length - a.name.length);

    for (const mb of sortedBrands) {
        if (nTitle.includes(normalizeTurkishText(mb.name))) {
            foundBrand = mb;
            break;
        }
    }

    if (!foundBrand) {
        const contentSnippet = nContent.substring(0, 1000);
        for (const mb of sortedBrands) {
            if (contentSnippet.includes(normalizeTurkishText(mb.name))) {
                foundBrand = mb;
                break;
            }
        }
    }

    // 2. Brand-Based Sector Mapping (Point 4 - AI-Free)
    if (foundBrand && foundBrand.sector_id) {
        const sector = masterSectors.find(s => s.id === foundBrand.sector_id);
        if (sector) {
            return {
                brand: foundBrand.name,
                sector_slug: sector.slug,
                category: sector.name,
                confidence: 1.0, // Brand mapping is definitive
                method: 'brand_mapping'
            };
        }
    }

    // 3. Keyword Scoring with Formal Confidence (Point 5)
    let topSector: any = null;
    let maxTitleMatches = 0;
    let maxContentMatches = 0;

    const sectorScores = masterSectors.map(sector => {
        let titleCount = 0;
        let contentCount = 0;

        for (const kw of (sector.keywords || [])) {
            const nKw = normalizeTurkishText(kw);
            const regex = new RegExp(`\\b${nKw.toLowerCase().replace(/[.*+?^${}()|[\]\\]/g, '\\$&')}\\b`, 'g');

            const tMatches = nTitle.match(regex);
            if (tMatches) titleCount += tMatches.length;

            const cMatches = nContent.substring(0, 1000).match(regex);
            if (cMatches) contentCount += cMatches.length;
        }

        const totalScore = (titleCount * 5) + contentCount;
        return { ...sector, titleCount, contentCount, totalScore };
    });

    const sortedByScore = sectorScores.sort((a, b) => b.totalScore - a.totalScore);
    const candidate = sortedByScore[0];

    let confidence = 0;
    if (candidate && candidate.totalScore > 0) {
        // High (1.0): Title match AND at least one other signal
        if (candidate.titleCount > 0 && (candidate.titleCount > 1 || candidate.contentCount > 0)) {
            confidence = 1.0;
        }
        // Medium (0.7): Title match OR significant content signals
        else if (candidate.titleCount > 0 || candidate.contentCount >= 2) {
            confidence = 0.7;
        }
        // Low (0.3): Weak content signals
        else {
            confidence = 0.3;
        }
        topSector = candidate;
    }

    return {
        brand: foundBrand?.name || null,
        sector_slug: topSector?.slug || 'diger',
        category: topSector?.name || 'Diğer',
        confidence: confidence,
        method: 'keyword_scoring'
    };
}
