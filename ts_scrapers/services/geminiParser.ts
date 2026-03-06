import * as dotenv from 'dotenv';
import { generateSectorSlug, generateCampaignSlug } from '../utils/slugify';
import { syncEarningAndDiscount } from '../utils/dataFixer';
import { supabase } from '../utils/supabase';
import { cleanCampaignText } from '../utils/textCleaner';


// Smart Hybrid: Two models for optimal performance
const FLASH_MODEL = 'gemini-2.5-flash-lite';
const THINKING_MODEL = 'gemini-2.5-flash-lite'; // Standardized to Flash to avoid 404s while maintaining logic

const CRITICAL_FIELDS = ['valid_until', 'eligible_customers', 'min_spend', 'category', 'bank', 'earning'];

interface MasterData {
    categories: string[];
    brands: string[];
    banks: string[];
}

let cachedMasterData: MasterData | null = null;

async function fetchMasterData(): Promise<MasterData> {
    if (cachedMasterData) return cachedMasterData;

    console.log('📚 Supabase\'den ana veriler çekiliyor...');

    const [sectorsRes, brandsRes] = await Promise.all([
        supabase.from('master_sectors').select('name'),
        supabase.from('master_brands').select('name')
    ]);

    // Use master_sectors (same as frontend) instead of master_categories
    const categories = sectorsRes.data?.map(c => c.name) || [
        'Market & Gıda', 'Akaryakıt', 'Giyim & Aksesuar', 'Restoran & Kafe',
        'Elektronik', 'Mobilya & Dekorasyon', 'Kozmetik & Sağlık', 'E-Ticaret',
        'Ulaşım', 'Dijital Platform', 'Kültür & Sanat', 'Eğitim',
        'Sigorta', 'Otomotiv', 'Vergi & Kamu', 'Turizm & Konaklama', 'Diğer'
    ];

    const brands = brandsRes.data?.map(b => b.name) || [];

    const banks = [
        'Yapı Kredi',
        'Garanti BBVA',
        'İş Bankası',
        'Akbank',
        'QNB Finansbank',
        'Ziraat',
        'Halkbank',
        'Vakıfbank',
        'Denizbank',
        'TEB',
        'ING',
        'Diğer'
    ];

    cachedMasterData = { categories, brands, banks };
    console.log(`✅ Veriler Yüklendi: ${categories.length} kategori, ${brands.length} marka, ${banks.length} banka`);

    return cachedMasterData;
}

/**
 * Bank-Aware HTML Cleaner
 */
function bankAwareCleaner(rawHtml: string, bank: string): string {
    if (!rawHtml) return '';

    let cleaned = rawHtml;
    const bankLower = bank.toLowerCase();
    const isAkbank = bankLower.includes('akbank');

    // 1. Tag Stripping Logic
    if (isAkbank) {
        // Akbank/Wings sites are SPAs, data is often in scripts. Keep scripts, strip styles.
        cleaned = cleaned.replace(/<style\b[^<]*(?:(?!<\/style>)<[^<]*)*<\/style>/gi, '');
    } else {
        // Standard cleaning for non-SPA sites
        cleaned = cleaned
            .replace(/<script\b[^<]*(?:(?!<\/script>)<[^<]*)*<\/script>/gi, '')
            .replace(/<style\b[^<]*(?:(?!<\/style>)<[^<]*)*<\/style>/gi, '');

        // For general sites, aggressive tag stripping usually helps AI focus
        cleaned = cleaned.replace(/<[^>]+>/g, ' ').replace(/\s+/g, ' ');
    }

    // 2. Entity Decoding
    let decoded = cleaned
        .replace(/&ndash;/g, '-')
        .replace(/&mdash;/g, '—')
        .replace(/&rsquo;/g, "'")
        .replace(/&lsquo;/g, "'")
        .replace(/&rdquo;/g, '"')
        .replace(/&ldquo;/g, '"')
        .replace(/&ouml;/g, 'ö')
        .replace(/&uuml;/g, 'ü')
        .replace(/&ccedil;/g, 'ç')
        .replace(/&nbsp;/g, ' ')
        .replace(/&amp;/g, '&')
        .replace(/&lt;/g, '<')
        .replace(/&gt;/g, '>')
        .replace(/&#(\d+);/g, (match, dec) => String.fromCharCode(dec));

    // 3. Custom Sorthand Entities (Akbank specific)
    if (isAkbank) {
        decoded = decoded
            .replace(/&q;/g, '"')
            .replace(/&l;/g, '<')
            .replace(/&g;/g, '>');
    }

    return decoded;
}

/**
 * Bank-Specific AI Instructions
 */
function getBankInstructions(bankName: string, cardName: string): string {
    const bank = bankName.toLowerCase();

    const instructions: Record<string, string> = {
        'akbank': `
🚨 AKBANK SPECIFIC RULES:
- TERMINOLOGY: 
    - For Axess/Free/Akbank Kart: Uses "chip-para" instead of "puan". 1 chip-para = 1 TL.
    - For Wings: Uses "Mil" or "Mil Puan". 1 Mil = 0.01 TL (unless specified as '1 TL değerinde').
- PARTICIPATION: Primary method is "Jüzdan" app. Always look for "Jüzdan'dan Hemen Katıl" button.
- SMS: Usually 4566. SMS keyword is usually a single word (e.g., "A101", "TEKNOSA").
- REWARD: If it says "8 aya varan taksit", it's an installment campaign. Earning: "Taksit İmkanı".
- ELIGIBLE CARDS (CRITICAL):
    - 🚨 TITLE TRAP: Even if the title says "Axess'e Özel" or "Wings'e Özel", most Akbank campaigns apply to multiple cards. You MUST scan the footer/details for phrases like "Axess, Wings, Free, Akbank Kart dahildir".
    - Scan for keywords: "Axess", "Wings", "Free", "Akbank Kart", "Ticari", "Business", "KOBİ", "TROY", "Bank’O Card Axess".
    - If it says "Axess, Wings, Free, Akbank Kart, Ticari kartlar dahildir", include ALL of them.
    - "Ticari kartlar" / "Business" / "KOBİ" = ["Axess Business", "Wings Business"].
    - "Akbank Kart" = ["Akbank Kart"].
    - "Bank’O Card Axess" = ["Bank’O Card Axess"].
    - 🚨 EXCLUSIONS: If "Bank’O Card Axess dahil değildir" or "hariçtir" is mentioned, ensure it's NOT in the list. IF IT SAYS "DAHİLDİR", MUST INCLUDE "Bank’O Card Axess".
    - 🚨 TROY: If "TROY" is mentioned for specific cards, use formats like "Axess TROY", "Akbank Kart TROY".
`,

        'yapı kredi': `
🚨 YAPI KREDI (WORLD) SPECIFIC RULES:
- TERMINOLOGY: "Worldpuan" is the currency.
    - ⚠️ IMPORTANT: "TL Worldpuan" means the value is in TL. If it says "100 TL Worldpuan", earning is "100 TL Worldpuan".
    - If it says "1000 Worldpuan", check context. Usually 1 Worldpuan = 0.005 TL. prefer explicitly stated TL value if available.
- ELIGIBLE CARDS:
    - Look for: "Yapı Kredi Kredi Kartları", "Worldcard", "Opet Worldcard", "Gold", "Platinum", "Business", "World Eko", "Adios", "Crystal", "Play".
    - "Bireysel kredi kartları" implies all consumer cards (World, Gold, Platinum, Opet, Play, Adios, Crystal).
    - "Business" / "Ticari" implies World Business.
- PARTICIPATION:
    - "World Mobil" or "Yapı Kredi Mobil" is the primary method. Look for "Hemen Katıl", "Katıl" button.
    - SMS: Look for SMS keywords sent to 4454.
`,
        'garanti': `
🚨 GARANTI BBVA/BONUS SPECIFIC RULES:
- TERMINOLOGY: "Bonus" is the currency. 1 Bonus = 1 TL. "Mil" for Shop&Fly/Miles&Smiles.
- ELIGIBLE CARDS:
    - Keywords: "Bonus", "Bonus Gold", "Bonus Platinum", "Bonus American Express", "Shop&Fly", "Miles&Smiles", "Flexi", "Money Bonus".
    - "Ticari" means "Bonus Business".
- PARTICIPATION:
    - Primary: "BonusFlaş" app. Look for "Hemen Katıl" button in app.
    - SMS: Often 3340.
`,
        'halkbank': `
🚨 HALKBANK/PARAF SPECIFIC RULES:
- TERMINOLOGY: "ParafPara" is the currency. 1 ParafPara = 1 TL.
- ELIGIBLE CARDS:
    - Keywords: "Paraf", "Paraf Gold", "Paraf Platinum", "Parafly", "Paraf Genç", "Halkcard".
    - "Esnaf"/"Kobi" means "Paraf Esnaf" or "Paraf Kobi".
- PARTICIPATION:
    - Primary: "Paraf Mobil" or "Halkbank Mobil".
    - SMS: Often 3404.
`,
        'vakıfbank': `
🚨 VAKIFBANK/WORLD SPECIFIC RULES:
- TERMINOLOGY: "Worldpuan". 1 Worldpuan = 0.005 TL usually, BUT "TL Worldpuan" means raw TL.
- ELIGIBLE CARDS:
    - Keywords: "VakıfBank Worldcard", "Platinum", "Rail&Miles", "Bankomat Kart" (Debit).
- PARTICIPATION:
    - Primary: "Cepte Kazan" app or "VakıfBank Mobil".
    - SMS: Often 6635.
`,
        'ziraat': `
🚨 ZIRAAT/BANKKART SPECIFIC RULES:
- TERMINOLOGY: "Bankkart Lira" is the currency. 1 Bankkart Lira = 1 TL.
- ELIGIBLE CARDS:
    - Keywords: "Bankkart", "Bankkart Genç", "Bankkart Başak" (Commercial), "Bankkart Combo".
- PARTICIPATION:
    - Primary: "Bankkart Mobil".
    - SMS: Often 4757.
`,
        'iş bankası': `
🚨 IS BANKASI/MAXIMUM SPECIFIC RULES:
- TERMINOLOGY: "Maxipuan" (Points) or "MaxiMil" (Miles).
- ELIGIBLE CARDS:
    - Keywords: "Maximum Kart", "Maximum Gold", "Maximum Platinum", "Maximiles", "Privia", "İş Bankası Bankamatik Kartı".
    - "Ticari" means "Maximum Ticari".
- PARTICIPATION:
    - Primary: "Maximum Mobil" or "İşCep". Look for "Katıl" button.
    - SMS: Usually 4402.
`,
        'chippin': `
🚨 CHIPPIN SPECIFIC RULES:
- PARTICIPATION: 
    - Primary method is "Chippin uygulaması" (Chippin app).
    - Look for phrases like "Chippin uygulamasından kampanyaya katılın", "Chippin'den katıl", "Kampanyaya katılım için Chippin uygulamasını kullanın".
    - ALWAYS extract participation_method if campaign text mentions "katıl", "katılım", "uygulama", "Chippin'den".
    - Format: "Chippin uygulamasından kampanyaya katılın" or similar clear instruction.
- REWARD: Uses "ChipPuan" or "Worldpuan". 1 ChipPuan = 1 TL, 1 Worldpuan = 1 TL.
- ELIGIBLE CARDS: Usually just "Chippin" (the card itself).
`,
        'teb': `
🚨 TEB SPECIFIC RULES:
- TERMINOLOGY: "Bonus" is the currency. 1 Bonus = 1 TL.
- ELIGIBLE CARDS:
    - Keywords: "TEB Bonus", "CEPTETEB", "TEB Worldcard", "TEB Bireysel Kredi Kartları".
    - "Ticari" means "TEB Bonus Ticari".
- PARTICIPATION:
    - Primary: "CEPTETEB Mobil" or "BonusFlaş".
    - SMS: Often 4663.
`
    };

    const key = Object.keys(instructions).find(k => bank.includes(k));
    return key ? instructions[key] : '';
}

// Rate limiting: Track last request time
let lastRequestTime = 0;
const MIN_REQUEST_INTERVAL_MS = 1000; // Minimum 1 second between requests (unlimited RPM with 2.5-flash)

// Sleep utility
const sleep = (ms: number) => new Promise(resolve => setTimeout(resolve, ms));

/**
 * Smart Hybrid: Detect if campaign needs Thinking model
 * Returns true for complex campaigns requiring advanced reasoning
 */
function shouldUseThinking(campaignText: string, category?: string): boolean {
    const text = campaignText.toLowerCase();

    // 1. Sector-based priority (Market and E-Commerce are notoriously complex in math)
    const complexSectors = ['market', 'gida', 'e-ticaret', 'elektronik', 'akaryakit'];
    if (category && complexSectors.includes(category.toLowerCase())) return true;

    // 2. Mathematical complexity
    if (/her\s+[\d.]+\s*tl.*?(toplam|toplamda|kazanç|puan)/is.test(text)) return true;  // Tiered: "Her X TL'ye Y TL"
    if (/kademeli|adim|adım/i.test(text)) return true; // Keywords for tiered rewards
    if (/[\d.]+\s*tl\s*-\s*[\d.]+\s*tl.*?(%|indirim|puan)/is.test(text)) return true;  // Range + percentage
    if (/(\d+)\s+(farklı\s+gün|farklı\s+işlem|işlem)/is.test(text)) return true;  // Multi-transaction
    if (/%[0-9]+.*?(maksimum|en fazla|toplam|puan|tl|varan)/is.test(text)) return true; // Percentage with limit
    if (/bankkart\s*lira/i.test(text)) return true; // Ziraat Bankkart Lira complexity
    if (/kademeli|adım|seviye/i.test(text)) return true; // Step/Tiered rewards

    // 2. Complex participation
    if (/\s+(ve|veya)\s+(sms|juzdan|jüzdan|uygulama|bankkart\s*mobil)/i.test(text)) return true;  // Multiple methods
    if (/harcamadan\s+önce.*?(katıl|sms)/i.test(text)) return true;  // Constraints
    if (/\d{4}.*?(sms|mesaj).*?\w+/i.test(text)) return true;  // SMS with keyword

    // 3. Card logic complexity
    if (/(hariç|geçerli\s+değil|dahil\s+değil|kapsam\s+dışı)/i.test(text)) return true;  // Exclusions
    if (/(ticari|business|kobi|esnaf).*?(kart|card)/i.test(text)) return true;  // Business cards
    if (/(platinum|gold|classic|premium).*?(ve|veya|hariç)/i.test(text)) return true;  // Card variants

    // 4. Conflicting information
    if (/son\s+(katılım|gün|tarih).*?\d{1,2}\s+(ocak|şubat|mart|nisan|mayıs|haziran|temmuz|ağustos|eylül|ekim|kasım|aralık)/i.test(text)) return true;  // Date conflicts

    return false;
}

async function callGeminiAPI(prompt: string, modelName: string = FLASH_MODEL, usePython: boolean = false, retryCount = 0): Promise<any> {
    const MAX_RETRIES = 3;
    const BASE_DELAY_MS = 2000;
    let totalTokens = 0;

    // Lazy load API Key to ensure dotenv has run
    const apiKey = process.env.GOOGLE_GEMINI_KEY;
    if (!apiKey) {
        throw new Error("❌ Missing GOOGLE_GEMINI_KEY in environment variables!");
    }

    try {
        const now = Date.now();
        const timeSinceLastRequest = now - lastRequestTime;
        if (timeSinceLastRequest < MIN_REQUEST_INTERVAL_MS) {
            const waitTime = MIN_REQUEST_INTERVAL_MS - timeSinceLastRequest;
            console.log(`   ⏳ Hız sınırlama: ${waitTime}ms bekleniyor...`);
            await sleep(waitTime);
        }
        lastRequestTime = Date.now();

        const response = await fetch(
            `https://generativelanguage.googleapis.com/v1beta/models/${modelName}:generateContent?key=${apiKey}`,
            {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    contents: [{ parts: [{ text: prompt }] }],
                    // Toggle Python Code Execution based on usePython flag
                    tools: usePython ? [{ code_execution: {} }] : [],
                    generationConfig: {
                        temperature: 0.1
                    }
                })
            }
        );

        if (response.status === 429) {
            if (retryCount >= MAX_RETRIES) {
                throw new Error(`Gemini API rate limit exceeded after ${MAX_RETRIES} retries`);
            }
            const retryDelay = BASE_DELAY_MS * Math.pow(2, retryCount);
            console.log(`   ⚠️  Hız limitine takıldı (429). Deneme ${retryCount + 1}/${MAX_RETRIES}, ${retryDelay}ms sonra...`);
            await sleep(retryDelay);
            return callGeminiAPI(prompt, modelName, usePython, retryCount + 1);
        }

        if (!response.ok) {
            const errorBody = await response.text();
            throw new Error(`Gemini API error: ${response.status} - ${errorBody}`);
        }

        const data: any = await response.json();
        const usage = data.usageMetadata;
        if (usage) {
            totalTokens = usage.totalTokenCount;
            console.log(`   📊 AI Usage: ${usage.totalTokenCount} tokens (P: ${usage.promptTokenCount}, C: ${usage.candidatesTokenCount})${usePython ? ' [PYTHON]' : ''}`);
        }

        const candidates = data.candidates?.[0]?.content?.parts || [];
        if (candidates.length === 0) throw new Error('No candidates from Gemini');

        // Robust Multi-part Extraction: Check text parts AND code results
        for (const part of candidates) {
            // Priority 1: Text part containing JSON
            if (part.text && part.text.includes('{')) {
                const jsonMatch = part.text.match(/\{[\s\S]*\}/);
                if (jsonMatch) {
                    try { return { data: JSON.parse(jsonMatch[0]), totalTokens }; } catch (e) { /* ignore and continue */ }
                }
            }
            // Priority 2: Code Execution Result containing JSON
            if (part.codeExecutionResult && part.codeExecutionResult.output) {
                const jsonMatch = part.codeExecutionResult.output.match(/\{[\s\S]*\}/);
                if (jsonMatch) {
                    try { return { data: JSON.parse(jsonMatch[0]), totalTokens }; } catch (e) { /* ignore and continue */ }
                }
            }
        }

        throw new Error(`AI returned but no valid JSON object was found in multi-part response.`);
    } catch (error: any) {
        const is404 = error.message.includes('404') || error.message.includes('not found');
        if (retryCount < MAX_RETRIES && !error.message.includes('rate limit') && !is404) {
            const retryDelay = BASE_DELAY_MS * Math.pow(2, retryCount);
            console.log(`   ⚠️  Error: ${error.message}. Retry ${retryCount + 1}/${MAX_RETRIES} after ${retryDelay}ms...`);
            await sleep(retryDelay);
            return callGeminiAPI(prompt, modelName, usePython, retryCount + 1);
        }
        throw error;
    }
}

function checkMissingFields(data: any): string[] {
    const missing: string[] = [];

    CRITICAL_FIELDS.forEach(field => {
        const value = data[field];

        // For numeric fields (min_spend, max_discount, discount_percentage),
        // 0 is a valid value. Only null/undefined means missing.
        if (field === 'min_spend') {
            if (value === null || value === undefined) {
                missing.push(field);
            }
        }
        // For other fields, check for empty/falsy values
        else if (!value ||
            (Array.isArray(value) && value.length === 0) ||
            value === null ||
            value === undefined ||
            (typeof value === 'string' && value.trim() === '')) {
            missing.push(field);
        }
    });

    return missing;
}

/**
 * Stage 3: Surgical Correction
 * Focuses ONLY on specific missing fields to save tokens and improve accuracy.
 */
export async function parseSurgical(
    html: string,
    existingData: any,
    missingFields: string[],
    url: string,
    bank?: string,
    metadata?: any
): Promise<any> {
    const cleaned = bankAwareCleaner(html, bank || '');
    const text = cleaned.substring(0, 20000);

    const masterData = await fetchMasterData();
    const bankInstructions = getBankInstructions(bank || '', existingData.card_name || '');

    // Use Python for surgical if complexity is detected or if it's a critical math field
    const usePython = shouldUseThinking(text, metadata?.category || existingData?.category) || missingFields.some(f => ['min_spend', 'max_discount'].includes(f));

    const surgicalPrompt = `
You are a precision data extraction tool. We have an existing campaign entry, but it's missing specific info.
DO NOT guess other fields. ONLY extract the fields requested.
${usePython ? `🚨 ZORUNLU PYTHON İŞ AKIŞI: Python code execution tool'u kullanarak matematiksel hesaplamaları doğrula.` : ''}
${bankInstructions}

EXISTING DATA (for context):
Title: ${existingData.title}
Current Category: ${existingData.category}

MISSING FIELDS TO EXTRACT:
${missingFields.map(f => `- ${f}`).join('\n')}

FIELD DEFINITIONS:
- valid_until: YYYY-MM-DD
- eligible_customers: Array of strings
- min_spend: Number
- earning: String (e.g. "500 TL Puan"). CRITICAL: DO NOT return null. If no numeric reward, summarize the main benefit in 2-3 words (e.g., "Uçak Bileti Fırsatı", "3 Taksit Ayrıcalığı", "Özel İndirim").
- category: MUST be one of [${masterData.categories.join(', ')}]
- bank: MUST be one of [${masterData.banks.join(', ')}]
- brand: ARRAY of brand names mentioned. E.g. ["Burger King", "Migros"]. Match to: ${masterData.brands.slice(0, 100).join(', ')}

TEXT TO SEARCH:
"${text.replace(/"/g, '\\"')}"

RETURN ONLY VALID JSON. NO MARKDOWN.
`;

    const { data: surgicalData, totalTokens } = await callGeminiAPI(surgicalPrompt, FLASH_MODEL, usePython);

    if (surgicalData && typeof surgicalData === 'object') {
        surgicalData.ai_method = `${FLASH_MODEL} [SURGICAL]${usePython ? ' [PYTHON]' : ''}`;
        surgicalData.ai_tokens = totalTokens;
    }

    // Merge and Clean
    const result = { ...existingData, ...surgicalData };
    const title = result.title || '';
    const description = result.description || '';

    // STAGE 3: Bank Service Detection & "Genel" logic
    // Refined: Only identify as bank service if it's strictly banking and lacks merchant markers.
    const isBankService = /ekstre|nakit avans|kredi kartı başvurusu|limit artış|borç transferi|borç erteleme|başvuru|otomatik ödeme|kira|harç|bağış/i.test(title + ' ' + description);

    // STAGE 4: Historical Assignment Lookup
    const { data: pastCampaign } = await supabase
        .from('campaigns')
        .select('brand, category')
        .eq('title', title)
        .not('brand', 'is', null)
        .not('brand', 'eq', '')
        .order('created_at', { ascending: false })
        .limit(1)
        .maybeSingle();

    // Strict Brand Cleanup
    const brandCleaned = await cleanupBrands(result.brand, masterData);
    result.brand = brandCleaned.brand;
    result.brand_suggestion = brandCleaned.suggestion;

    if (isBankService) {
        console.log(`   🏦 Bank service detected for "${title}", mapping to "Genel"`);
        result.brand = 'Genel';
        result.brand_suggestion = '';
    } else if (pastCampaign) {
        console.log(`   🧠 Learning: Previously mapped to brand "${pastCampaign.brand}" for "${title}"`);
        result.brand = pastCampaign.brand;
        result.brand_suggestion = '';
        result.category = pastCampaign.category || result.category;
    }

    // Ensure category -> sector_slug consistency
    if (result.category) {
        result.sector_slug = generateSectorSlug(result.category);
    }

    return result;
}

/**
 * Standardizes brand names (Sync with frontend metadataService)
 */
function normalizeBrandName(name: string): string {
    if (!name) return '';

    // 1. Remove common domain extensions and noise suffixes
    let cleanName = name
        .replace(/\.com\.tr|\.com|\.net|\.org/gi, '')
        .replace(/\s+notebook$|\s+market$|\s+marketleri$|[\s-]online$|[\s-]türkiye$|[\s-]turkiye$/gi, '')
        .trim();

    // 2. Specialized Merges (Canonical Mapping)
    const lower = cleanName.toLowerCase();

    // Amazon Group
    if (lower.includes('amazon')) return 'Amazon';

    // Migros Group
    if (lower.includes('migros') || lower === 'sanal market') return 'Migros';

    // Getir Group
    if (lower.startsWith('getir')) return 'Getir';

    // Yemeksepeti Group
    if (lower.includes('yemeksepeti') || lower === 'banabi') return 'Yemeksepeti';

    // Carrefour Group
    if (lower.includes('carrefoursa') || lower.includes('carrefour')) return 'CarrefourSA';

    // Netflix
    if (lower.includes('netflix')) return 'Netflix';

    // Disney
    if (lower.includes('disney')) return 'Disney+';

    // Other common ones
    if (lower === 'monsternotebook') return 'Monster';
    if (lower === 'mediamarkt') return 'Media Markt';
    if (lower === 'trendyolmilla' || lower === 'trendyol man') return 'Trendyol';
    if (lower === 'hepsiburada') return 'Hepsiburada';
    if (lower === 'n11') return 'n11';
    if (lower.includes('boyner')) return 'Boyner';
    if (lower.includes('beymen')) return 'Beymen';
    if (lower.includes('teknosa')) return 'Teknosa';
    if (lower.includes('vatan bilgisayar')) return 'Vatan Bilgisayar';
    if (lower.includes('şok market') || lower === 'cepte şok') return 'Şok';
    if (lower.includes('a101')) return 'A101';
    if (lower.includes('bim')) return 'BİM';

    // 3. Title Case with Turkish support
    return cleanName.split(' ').map(word => {
        if (word.length === 0) return '';
        return word.charAt(0).toLocaleUpperCase('tr-TR') + word.slice(1).toLocaleLowerCase('tr-TR');
    }).join(' ').trim();
}

/**
 * Normalizes and cleans brand data to ensure it's a flat string and matches master data.
 * Automatically adds new brands to master_brands if they are valid and not existing.
 */
async function cleanupBrands(brandInput: any, masterData: MasterData): Promise<{ brand: string, suggestion: string }> {
    let brands: string[] = [];

    // 1. Normalize input to array
    if (Array.isArray(brandInput)) {
        brands = brandInput.map(b => String(b));
    } else if (typeof brandInput === 'string') {
        const cleaned = brandInput.replace(/[\[\]"]/g, '').trim();
        if (cleaned.includes(',')) {
            brands = cleaned.split(',').map(b => b.trim());
        } else if (cleaned) {
            brands = [cleaned];
        }
    }

    if (brands.length === 0) return { brand: '', suggestion: '' };

    const forbiddenTerms = [
        'yapı kredi', 'yapı', 'world', 'worldcard', 'worldpuan', 'puan', 'taksit', 'indirim',
        'kampanya', 'fırsat', 'troy', 'visa', 'mastercard', 'express', 'bonus', 'maximum',
        'axess', 'bankkart', 'paraf', 'card', 'kredi kartı', 'nakit', 'chippin', 'adios', 'play',
        'wings', 'free', 'wings card', 'black', 'mil', 'chip-para', 'puan', 'tl', 'ödeme', 'alisveris', 'alişveriş',
        'juzdan', 'jüzdan', 'bonusflaş', 'bonusflas', 'ayrıcalık', 'avantaj', 'pos', 'üye işyeri', 'üye iş yerleri',
        'mobilya', 'sigorta', 'nalburiye', 'kozmetik', 'akaryakıt', 'giyim', 'aksesuar', 'elektronik', 'market', 'gıda',
        'restoran', 'kafe', 'e-ticaret', 'ulaşım', 'turizm', 'konaklama', 'otomotiv', 'kamu', 'eğitim',
        ...masterData.banks.map(b => b.toLowerCase()),
        ...masterData.categories.map(c => c.toLowerCase())
    ];

    const matched: string[] = [];
    const unmatched: string[] = [];

    for (const b of brands) {
        const lower = b.trim().toLowerCase();
        if (!lower || lower.length <= 1) continue;
        if (lower === 'yok' || lower === 'null' || lower === 'genel') continue;
        if (forbiddenTerms.some(term => lower === term || lower.startsWith(term + ' '))) continue;

        const match = masterData.brands.find(mb => mb.toLowerCase() === lower);
        if (match) {
            matched.push(match);
        } else {
            // New brand found!
            const normalized = normalizeBrandName(b);
            if (normalized && normalized.length > 1) {
                unmatched.push(normalized);
            }
        }
    }

    // Process new brands: Add to DB if they don't exist
    if (unmatched.length > 0) {
        console.log(`   🆕 New brands detected: ${unmatched.join(', ')}`);
        for (const newBrand of unmatched) {
            try {
                // Double check if it exists in DB (case insensitive)
                const { data: existing } = await supabase
                    .from('master_brands')
                    .select('name')
                    .ilike('name', newBrand)
                    .single();

                if (!existing) {
                    const { error } = await supabase
                        .from('master_brands')
                        .insert([{ name: newBrand }]);

                    if (!error) {
                        console.log(`   ✅ Added new brand: ${newBrand}`);
                        matched.push(newBrand);
                        // Update cache to include this new brand for future matches in this run
                        masterData.brands.push(newBrand);
                    } else {
                        console.error(`   ❌ Error adding brand ${newBrand}:`, error.message);
                    }
                } else {
                    matched.push(existing.name);
                }
            } catch (err) {
                console.error(`   ❌ Failed to process brand ${newBrand}`);
            }
        }
    }

    return {
        brand: [...new Set(matched)].join(', '),
        suggestion: '' // Suggestions are now automatically added to matched if verified/added
    };
}

export async function parseWithGemini(campaignText: string, url: string, bank: string, card: string, metadata?: any): Promise<any> {
    const cleaned = bankAwareCleaner(campaignText, bank);
    const text = cleaned.substring(0, 30000);

    const masterData = await fetchMasterData();

    // Sort everything to ensure perfectly STABLE prefix for Caching
    const sortedCategories = [...masterData.categories].sort().join(', ');
    const sortedBanks = [...masterData.banks].sort().join(', ');
    const sortedBrands = [...masterData.brands].sort((a, b) => a.localeCompare(b, 'tr')).slice(0, 300).join(', ');

    const today = new Date().toISOString().split('T')[0];
    // STAGE 1: Full Parse
    // Smart Hybrid: Model selection
    const useThinking = shouldUseThinking(text, metadata?.category);
    const selectedModel = useThinking ? THINKING_MODEL : FLASH_MODEL;
    // Smart Switch: Use Python for complex campaigns or specific bank patterns
    const usePython = useThinking;
    const modelLabel = usePython ? `${selectedModel} [PYTHON]` : selectedModel;

    // Metadata Authority: If we have specific metadata (JSON-LD), tell AI it's the GROUND TRUTH
    let metadataInstruction = "";
    if (metadata) {
        metadataInstruction = `
🚨 METADATA AUTHORITY (CRITICAL):
The following data was extracted directly from the site's JSON-LD metadata.
Treat this as the absolute authority for [brand] and [title].
Metadata: ${JSON.stringify(metadata)}
`;
    }

    const pythonWorkflowPrompt = usePython ? `
🚨 ZORUNLU PYTHON İŞ AKIŞI (MANDATORY WORKFLOW):
  ADIM 1: Metindeki tüm sayıları, tutarları ve yüzde sembollerini tespit et.
  ADIM 2: Kampanya türünü belirle:
    A) Sabit: "X TL harcaya Y TL"
    B) Yüzde: "Harcamanın %X'i kadar, max Y TL"
    C) Periyodik/Kademeli (KATLANAN): "Her X TL harcamaya Y TL, toplam max Z TL"
    D) Çoklu Tier: "50k'ya 5k, 100k'ya 12k reward"
  ADIM 3: Python code execution tool'u KULLANARAK hesabı yap:
    - B (Yüzde) için: min_spend = max_discount / (percentage / 100).
    - C (Periyodik/Katlanan) için: 
        n = max_discount / per_transaction_reward
        min_spend = n * per_transaction_spend
        🚨 ÖRNEK: Her 1500'e 100 bonus, toplam 1200 bonus -> n=12 -> min_spend = 12 * 1500 = 18.000 TL.
    - D (Çoklu Tier) için: max_discount (en yüksek olan) değerini al ve BU DEĞERE ULAŞMAK İÇİN GEREKLİ olan harcamayı (min_spend) al. 
  ADIM 4: Python çıktısını JSON alanlarına YAZ.
  ADIM 5: Final JSON'u DÖNDÜR.
🚨 UYARI: Matematik içeren kampanyalarda Python kullanmadan işlem yapmak KESİNLİKLE YASAK!
` : '';

    const staticPrefix = `
Extract campaign data into JSON matching this EXACT schema.
${pythonWorkflowPrompt}
${metadataInstruction}
${getBankInstructions(bank, card)}

{
  "title": "string (catchy campaign title, clear and concise)",
  "description": "string (Short, exciting, marketing-style summary. Max 2 sentences. Use 1-2 relevant emojis. Language: Turkish. Do NOT include boring legal terms.)",
  "ai_marketing_text": "string (Ultra-short, punchy summary for card view. Max 10 words. Add 1 relevant emoji at the start. Focus on the main benefit. E.g. '💰 500 TL Chips Fırsatı' or '🎁 %50 İndirim ve Taksit')",
  "conditions": ["string (List of important campaign terms, limits, and exclusions. Extract key rules as separate items.)"],
  "category": "string (MUST be one of: ${sortedCategories})",
  "discount": "string (Use ONLY for installment info, e.g. '9 Taksit', '+3 Taksit'. FORMAT: '{Number} Taksit'. NEVER mention fees/interest.)",
  "earning": "string (🚨 HİYERARŞİ KURALI - ÖNCE YÜZDE KONTROL ET:\n    1️⃣ Metinde '%' sembolü VARSA:\n       → MUTLAKA '%{X} (max {Y}TL)' formatını kullan\n       → Örnek: '%10 (max 500TL)', '%25 (max 300TL)'\n       → 🚨 ASLA '500 TL Puan' gibi sabit tutar YAZMA!\n    2️⃣ Metinde '%' sembolü YOKSA:\n       → '{Amount} TL Puan' veya '{Amount} TL İndirim' kullan\n       → 🚨 MİL: 'Mil' veya 'MaxiMil' kelimesi varsa MUTLAKA '{Amount} Mil' yaz\n       → 🚨 SAYI FORMATI: 1.000+ sayılarda NOKTA kullan (örn: '30.000 TL Puan')\n    3️⃣ Sayısal ödül YOKSA:\n       → 2-3 kelime özet: 'Uçak Bileti', 'Taksit İmkanı', 'Özel Fırsat'\n    ⚠️  UYARI: Yüzde bazlı kampanyayı '500 TL Puan' şeklinde kısaltmak min_spend hesaplamasını BOZAR!)",
  "min_spend": number (CRITICAL: Required spend to reach the benefit stated in 'earning'. If 'earning' is '%20 (max 10.000 TL)', min_spend = 50.000. HOWEVER, if there are tiers like '4.000 TL -> %10, 8.000 TL -> %20' and you choose %20 for earning, min_spend = 8000 (threshold for that tier) IF the full-cap math results in an unrealistic number for a single month/merchant.),
  "min_spend_currency": "string (Currency code: TRY, USD, EUR, GBP. Default: TRY. ONLY change if campaign explicitly mentions foreign currency like 'yurt dışı', 'dolar', 'USD', 'euro')",
  "max_discount": number (Max reward limit per customer/campaign),
  "max_discount_currency": "string (Currency code: TRY, USD, EUR, GBP. Default: TRY. ONLY change if reward is in foreign currency)",
  "earning_currency": "string (Currency code: TRY, USD, EUR, GBP. Default: TRY. Match the currency mentioned in earning)",
  "discount_percentage": number (If % based reward, e.g. 15 for %15),
  "valid_from": "string (🚨 FORMAT: 'YYYY-MM-DD' - örn: '2024-01-01'. Yıl yoksa 2024 veya 2025 al. Ay isimlerini (Ocak, Şubat...) sayıya çevir.)",
  "valid_until": "string (🚨 FORMAT: 'YYYY-MM-DD'. Metinde 'Şu tarihe kadar', 'Son gün: X' gibi ifadeleri ara. ⚠️ Belirsizse '2026-12-31' yazma, null veya mantıklı bir tarih (ay sonu) yaz.)",
  "eligible_customers": ["array of strings (Simple card names: Axess, Wings, Business, Free etc. IMPORTANT: ALWAYS include 'TROY' if specifically mentioned for these cards, e.g. 'Axess TROY', 'Akbank Kart TROY')"],
  "eligible_cards_detail": {
    "variants": ["array of strings (ONLY if text mentions: Gold, Platinum, Business, Classic, etc.)"],
    "exclude": ["array of strings (ONLY if text says: X hariç, X geçerli değil)"],
    "notes": "string (ONLY if text has special notes: Ticari kartlar hariç, etc.)"
  } | null,
  "participation_method": "string (TAM KATILIM TALİMATI: SADECE NASIL ve NEREDEN (SMS/Uygulama). Tarih veya Harcama Miktarı GİRMEYİN. 🚨 YASAK: 'Juzdan'ı indirin', 'Uygulamayı yükleyin' gibi genel ifadeler KULLANMA! DOĞRU FORMAT: 'Harcamadan önce Juzdan'dan Hemen Katıl butonuna tıklayın' veya 'MARKET yazıp 4566ya SMS gönderin'. Örn: 'Juzdan uygulamasından Hemen Katıla tıklayın veya MARKET yazıp 4566ya SMS gönderin.')",
  "participation_detail": {
    "sms_to": "string (ONLY if SMS number in text: 4442525, etc.)",
    "sms_keyword": "string (ONLY if keyword in text: KATIL, KAMPANYA, etc.)",
    "wallet_name": "string (ONLY if app name in text: Jüzdan, BonusFlaş, etc.)",
    "instructions": "string (ONLY if detailed steps in text: 1-2 sentences)",
    "constraints": ["array of strings (ONLY if conditions: Harcamadan önce katıl, etc.)"]
  } | null,
  "merchant": "string (Primary shop/brand name)",
  "bank": "string (AUTHORITY: MUST be exactly as provided. Allowed: ${sortedBanks})",
  "card_name": "string (AUTHORITY: MUST be exactly as provided.)",
  "brand": [
    "array of strings (🚨 SADECE GERÇEK MARKA İSİMLERİ! Official brand names. YASAK: Kart isimleri (Axess, Wings, Bonus, Free, Juzdan, World, Play, Crystal), Banka isimleri (Akbank, Yapı Kredi, vb.), Genel terimler. 🚨 SEKTÖR KAMPANYASI KURALI: Eğer metinde belirli bir marka adı GEÇMİYOR, sadece 'Marketlerde geçerli', 'Giyim sektöründe' deniyorsa markayı ['Genel'] yap. ÖRNEK: ['CarrefourSA'], ['Teknosa'], ['Genel']. MAX 3 marka. Her marka max 40 karakter.)"
  ],
  "tags": [
    "array of strings (🏷️ AKILLI ETİKETLER: Markalar, Sektör, Kampanya Türü, Ödeme Yöntemi. Örn: ['Amazon', 'Elektronik', 'Taksit', 'Mastercard']. Metinde geçen TÜM önemli anahtar kelimeleri ekle. MAX 15 etiket.)"
  ],
  "ai_enhanced": true
}

### 🛑 ULTRA-STRICT RULES:

1. **BANK & CARD AUTHORITY:**
   - Use the provided Bank and Card Name. DO NOT hallucinate.

1.5. **KATEGORİ SEÇİMİ (CATEGORY SELECTION):**
   - 🚨 MERCHANT/BRAND'E GÖRE DOĞRU KATEGORİ SEÇ!
   - 🚨 MUST be one of THESE 18: ${sortedCategories}
   - Koçtaş, Bauhaus, Karaca, Özdilek, İdaş, Korkmaz, Evidea → "Mobilya & Dekorasyon"
   - Teknosa, MediaMarkt, Vatan, Apple, Samsung, Vestel, Arçelik, Nespresso, Dyson → "Elektronik"
   - CarrefourSA, Migros, A101, BİM, ŞOK, GetirBüyük, Yemeksepeti Market, Tarım Kredi → "Market & Gıda"
   - H&M, Zara, LC Waikiki, Mango, Koton, Nike, Adidas, FLO, Desa, Boyner, Beymen → "Giyim & Aksesuar"
   - Pegasus, THY, Tatilsepeti, Enuygun, ETS Tur, Jolly Tur, Otelz, Trivago → "Turizm & Konaklama"
   - Shell, Opet, BP, Petrol Ofisi, Lassa, Pirelli, AutoKing, TUVTURK → "Otomotiv"
   - Trendyol, Hepsiburada, Amazon, Pazarama, Çiçeksepeti, n11 → "E-Ticaret"
   - Yemeksepeti, Getir, Starbucks, Kahve Dünyası, Dominos, KFC, Burger King → "Restoran & Kafe"
   - Netflix, Disney+, Spotify, YouTube, TOD, BluTV → "Dijital Platform"
   - Martı, BinBin, Hop, Uber, BiTaksi → "Ulaşım"
   - Sağlık, Hastane, Klinik, Eczane, Watson, Gratis (Güzellik tarafı) → "Kozmetik & Sağlık"
   - Sigorta, Allianz, AkSigorta → "Sigorta"
   - Vergi, MTV, SGK, Trafik Cezası → "Vergi & Kamu"
   - DİKKAT: "Diğer" kategorisini SADECE yukarıdakilere uymayan ve spesifik bir sektörü olmayan kampanyalar için kullan!
   
2. **HARCAMA-KAZANÇ KURALLARI (MATHEMATIC LOGIC):**
   - discount: SADECE "{N} Taksit" veya "+{N} Taksit"
   - earning: Max 30 karakter. "{AMOUNT} TL Puan" | "{AMOUNT} TL İndirim" | "{AMOUNT} TL İade" | "%{P} (max {Y}TL)" | "%{P} İndirim"
     - 🚨 YÜZDE + MAX LİMİT KURALI: Eğer kampanyada yüzde bazlı kazanç VAR ve max_discount değeri VARSA, earning formatı MUTLAKA "%{P} (max {Y}TL)" olmalı.
       - ÖRNEK: "%10 indirim, maksimum 200 TL" metni → earning: "%10 (max 200TL)", max_discount: 200
       - ÖRNEK: "%5 chip-para, toplam 500 TL'ye kadar" → earning: "%5 (max 500TL)", max_discount: 500
      - 🚨 PUAN vs İNDİRİM AYIRIMI:
        - "Puan", "Chip-Para", "Worldpuan", "Maxipuan" içeriyorsa → "{AMOUNT} TL Puan"
        - "Mil", "MaxiMil" içeriyorsa → "{AMOUNT} Mil"
        - "İndirim", "İade", "Cashback" içeriyorsa → "{AMOUNT} TL İndirim"
        - ÖRNEK: "300 TL chip-para" → earning: "300 TL Puan"
        - ÖRNEK: "500 TL indirim" → earning: "500 TL İndirim"
        - ÖRNEK: "400 MaxiMil" → earning: "400 Mil"
        - DİKKAT: Puan ≠ İndirim ≠ Mil! Doğru terimi kullan.
      - 🚨 ÇOKLU TIER (HARCAMA KADEMELERİ) KURALI:
        - Eğer kampanya "X TL harcamaya %10, Y TL harcamaya %20" gibi kademeliyse:
        - earning: "EN YÜKSEK" kademeyi yaz. Örn: "%20 (max Z TL)"
        - min_spend: "EN YÜKSEK" kademe tutarını (Y) yaz.
        - ÖRNEK: "4.000 TL'ye %10, 8.000 TL'ye %20" → earning: "%20 (max ...)", min_spend: 8000.
        - ⚠️ DİKKAT: Eğer %20'lik dilim için min_spend: 8.000 iken, max_discount: 10.000 ise ve matematiksel olarak 10.000 için 50.000 TL gerekiyorsa, min_spend olarak 8.000 yazmayı TERCİH ET (yoksa kullanıcıya çok yüksek görünebilir).
     - 🚨 KATLANAN KAMPANYA - TOPLAM KAZANÇ KURALI:
       - "Her X TL'ye Y TL, toplam Z TL" formatında kampanyalarda:
       - earning: "Z TL Puan" veya "Z TL İndirim" (TOPLAM kazanç, işlem başı Y değil!)
       - max_discount: Z (TOPLAM kazanç)
       - ÖRNEK: "Her 100 TL'ye 20 TL, toplam 100 TL puan" → earning: "100 TL Puan" (20 DEĞİL!)
       - ÖRNEK: "Her 500 TL'ye 50 TL, toplam 300 TL indirim" → earning: "300 TL İndirim" (50 DEĞİL!)
      - 🚨 BAŞLIK ÖNCELİĞİ (VARAN KAMPANYALAR & EKSİK VERİ):
        - KURAL 1: Başlıkta "X TL'ye varan" geçiyorsa ve metindeki hesaplama düşükse -> BAŞLIĞI AL.
        - KURAL 2: Metinden mantıklı bir para/puan çıkaramadıysan (veya "Özel Fırsat" gibi belirsizse) VE Başlıkta net para varsa ("1.000 TL İndirim") -> BAŞLIĞI AL.
        - ÖRNEK: Başlık "3.500 TL'ye varan puan" -> Earning: "3.500 TL Puan"
   - min_spend: KESİNLİKLE KAZANCI ELDE ETMEK İÇİN GEREKEN "TOPLAM" HARCAMA.
      - 🚨 YÜZDE KAMPANYALARI İÇİN ZORUNLU HESAPLAMA:
        - Eğer kampanya yüzde bazlı (%X indirim) VE max_discount belirtilmişse:
        - FORMÜL: min_spend = max_discount / (yüzde / 100)
        - ÖRNEK 1: "%10 indirim, maksimum 8.000 TL" → min_spend = 8000 / 0.10 = 80.000 TL
        - ÖRNEK 2: "%20 indirim, max 10.000 TL" → min_spend = 10000 / 0.20 = 50.000 TL
        - ÖRNEK 3: "%15 indirim, toplam 200 TL" → min_spend = 200 / 0.15 = 1.333 TL
        - ⚠️  DİKKAT: Metinde "minimum harcama" belirtilmese BİLE, bu formülü KULLAN!
        - ⚠️  ASLA min_spend: 0 YAZMA (yüzde kampanyalarında 0 mantıksız)!
      - 🚨 ARALIK KURALI (MIN-MAX): 
        - Eğer "1.000 TL - 20.000 TL arası" gibi aralık varsa:
        - min_spend = MİNİMUM değer (1.000)
        - ASLA maksimum değer (20.000) KULLANMA!
        - ÖRNEK: "2.000 TL - 500.000 TL arası 3 taksit" → min_spend: 2000 (500000 DEĞİL!)
      - 🚨 KRİTİK KURAL (KATLANAN HARCAMA): Metinde "her X TL harcamaya Y TL, toplam Z TL" veya "X TL ve üzeri her harcamaya..." kalıbı varsa, SAKIN "X" değerini yazma!
        - FORMÜL: min_spend = (Toplam Kazanç / Sefer Başı Kazanç) * Sefer Başı Harcama
        - 🚨 ÖRNEK 1: "Her 1.500 TL'ye 80 TL, toplam 1.200 TL" → (1200/80)*1500 = 22.500 TL (1500 DEĞİL!)
        - ÖRNEK 2: "Her 500 TL'ye 300 TL, toplam 1.200 TL" → (1200/300)*500 = 2.000 TL (500 DEĞİL!)
        - ⚠️  DİKKAT: "Her X TL'ye Y TL" gördüğünde MUTLAKA toplam kazanç için gereken toplam harcamayı hesapla! SADECE X'i yazarsan veri HATALI olur.
      - 🚨 ÇOKLU İŞLEM KAMPANYALARI: "3 farklı günde 750 TL", "4 işlemde 100 TL" gibi kampanyalar:
        - FORMÜL: min_spend = İşlem Başı Tutar * İşlem Sayısı
        - ÖRNEK 1: "3 farklı günde 750 TL ve üzeri" → 750 * 3 = 2.250 TL
        - ÖRNEK 2: "4 işlemde 100 TL ve üzeri" → 100 * 4 = 400 TL
      - 🚨 ÖNCELİK KURALI: Eğer kampanyada AYNI ANDA birden fazla pattern varsa:
        - 1. ÖNCELİK: Aralık kuralı ("X TL - Y TL arası") → min_spend = X (minimum değer)
        - 2. ÖNCELİK: Katlanan kampanya ("Her X TL'ye Y TL") → Formülü uygula
        - 3. ÖNCELİK: Yüzde kampanya → Formülü uygula
        - ÖRNEK: "15.000-29.999 TL arası %5 indirim" → min_spend = 15.000 (50.000 DEĞİL!)
      - Örnek (Tek Sefer): "Tek seferde 2.000 TL harcamanıza" → 2000 TL.
      - Örnek (X. Harcama): "İkinci 500 TL harcamaya" → 1000 TL (500+500).
      - ÖNEMLİ: Eğer metinde "Tek seferde en az 500 TL harcama yapmanız gerekir" yazsa BİLE, yukarıdaki hesaplama daha yüksek bir tutar çıkarıyorsa ONU YAZ.
   - 3- TARİH TESPİTİ (DATE DETECTION):
     - Metinde "Ocak, Şubat, Mart..." gibi ay isimlerini bul ve sayısal formata çevir.
     - "31 Aralık 2024" -> 2024-12-31.
     - "X Ocak - Y Şubat" -> valid_from: 2025-01-X, valid_until: 2025-02-Y.
     - 🚨 ÖNEMLİ: Eğer yıl belirtilmemişse ve kampanya geleceğe dönükse 2025, geçmişe dönükse ve hala aktifse 2025 veya 2026 yılına göre akıl yürüt.
   - max_discount: Kampanyadan kazanılabilecek EN YÜKSEK (TOPLAM) tutar. Eğer "toplamda 500 TL" diyorsa, bu değer 500 olmalı.
   - 🚨 PARA BİRİMİ TESPİTİ (CURRENCY DETECTION):
     - Varsayılan: TRY (Türk Lirası)
     - Eğer kampanya "yurt dışı", "abroad", "foreign", "dolar", "USD", "euro", "EUR" içeriyorsa:
       - min_spend_currency, max_discount_currency, earning_currency alanlarını uygun para birimine çevir
       - ÖRNEK: "Yurt dışı harcamalarınıza 15 USD indirim" → earning_currency: "USD", max_discount_currency: "USD"
       - DİKKAT: Para birimi değiştiğinde min_spend hesaplaması da o para biriminde olmalı!

3. **KATILIM ŞEKLİ (participation_method):**
   - **TAM VE NET TALİMAT.** Ne çok kısa ne çok uzun.
   - GEREKSİZ SÖZCÜKLERİ ("Kampanyaya katılmak için", "Harcama yapmadan önce", "tarihlerinde") ATIN.
   - SADECE EYLEMİ DETAYLANDIRIN (Hangi buton? Hangi SMS kodu?).
   - YASAK (Çok Kısa): "Juzdan'dan katılın." (Hangi buton?)
   - YASAK (Çok Uzun): "Alışveriş yapmadan önce Juzdan uygulamasındaki kampanyalar menüsünden Hemen Katıl butonuna tıklayarak katılım sağlayabilirsiniz."
   - DOĞRU (İDEAL): "Juzdan'dan 'Hemen Katıl' butonuna tıklayın veya '[ANAHTAR_KELİME]' yazıp 4566'ya SMS gönderin."
   - DOĞRU (İDEAL): "Juzdan üzerinden 'Hemen Katıl' deyin."
   - **SMS VARSA ZORUNLU KURAL:** Asla "SMS ile katılın" yazıp bırakma! Metinde GÖRDÜĞÜN anahtar kelimeyi (örn: TEKNOSA, TATIL, MARKET) ve numarayı yaz.
   - **YASAK (HALÜSİNASYON):** Metinde SMS kodu yoksa ASLA uydurma (özellikle 'A101' gibi başka kodları YAZMA).
   - YANLIŞ: "SMS ile kayıt olun." (NUMARA VE KOD NEREDE?)

4. **ÖRNEK SENARYOLAR (FEW-SHOT TRAINING - MUTLAKA OKU):**
   - **SENARYO 1: VARAN PUAN (EN ZOR)**
     - GİRDİ: "Market harcamalarınıza 3.500 TL'ye varan MaxiPuan... Her 2.000 TL'ye 200 TL, toplamda 3.500 TL..."
     - ÇIKTI: earning: "3.500 TL MaxiPuan", min_spend: 35000  (Formül: 3500/200 * 2000)
   - **SENARYO 2: TAKSİT**
     - GİRDİ: "Gree Klima'da peşin fiyatına 11 taksit!"
     - ÇIKTI: earning: "Peşin Fiyatına 11 Taksit", discount: "11 Taksit", min_spend: 0
   - **SENARYO 3: YÜZDE İNDİRİM**
     - GİRDİ: "Teknosa'da %10 indirim, maksimum 500 TL"
     - ÇIKTI: earning: "500 TL İndirim", percent: "%10", min_spend: 5000 (Formül: 500/0.10)
    - **SENARYO 4: HER HARCAMAYA PUAN Y (KATLANAN / CUMULATIVE) - KRİTİK**
      - GİRDİ: "Market ve Restoran... tek seferde yapılacak 2.000 TL ve üzeri her harcamaya 125 TL, toplam 1.500 TL ParafPara..."
      - MANTIK: Kullanıcı 1.500 TL kazanmak için kaç tane 2.000 TL harcamalı? (1500 / 125 = 12 adet). Toplam Harcama = 12 * 2.000 = 24.000 TL.
      - ÇIKTI: earning: "1.500 TL Puan" (Toplam ödül), min_spend: 24000 (Toplam gereken harcama), max_discount: 1500
    - **SENARYO 5: EKSİK METİN (BAŞLIK KURTARMA)**
      - GİRDİ: Başlık="1.000 TL İndirim", Metin="Detaylar için tıklayın..." (Para yok)
      - ÇIKTI: earning: "1.000 TL İndirim", min_spend: 0 (Metin olmadığı için hesaplanamaz)

5. **KART TESPİTİ (eligible_customers):**
   - Metin içinde "Ticari", "Business", "KOBİ" geçiyorsa, eligible_customers listesine ilgili kartları (Axess Business, Wings Business vb.) MUTLAKA ekle. Bireysel kartları EKSİK ETME.

6. **BRAND MATCHING:**
   - Match brands against: [${sortedBrands} ... and others].

7. **MAXIMUM KAMPANYALARI İÇİN ÖZEL KURALLAR:**
   - 🚨 Maximum kampanyaları TEK PARAGRAF halinde gelir, tüm bilgiler iç içe!
   - **KATILIM ŞEKLİ (participation_method):**
     - Paragrafta "katılım" kelimesi YOKSA bile, kampanya OTOMATİK olabilir
     - Eğer "İşCep", "Jüzdan", "SMS", "katıl" gibi kelimeler YOKSA → participation_method: null
     - Eğer "peşin fiyatına taksit", "vade farksız", "indirim" gibi kelimeler varsa → Otomatik kampanya, participation_method: null
   - **GEÇERLİ KARTLAR (eligible_customers) - ÇOK ÖNEMLİ:**
     - 🚨 TEK KART BULUP DURMA! Metinde geçen TÜM kartları listele.
     - Özellikle şunları ARA: "Maximiles", "Privia", "MercedesCard", "Pati Kart", "Maximum Genç", "İş'te Üniversiteli", "Business", "Ticari".
     - Örnek: "Maximum ve Maximiles kartlarınızla" -> ["Maximum", "Maximiles"]
     - Örnek: "Maximum, Maximiles ve Privia ile" -> ["Maximum", "Maximiles", "Privia"]
     - "Tüm Maximum kartlar" derse -> ["Maximum", "Maximum Gold", "Maximum Platinum", "Maximum Genç"] ekle.
     - "İş Bankası Visa", "Visa logolu" -> ["Maximum Visa"]
     - "İş Bankası TROY", "TROY logolu" -> ["Maximum TROY"]
     - ASLA sadece ["Maximum"] yazma, metinde başka kart tipleri varsa MUTLAKA ekle!
   - **KOŞULLAR (conditions):**
     - Uzun paragrafı cümlelere böl
     - Her cümle ayrı bir koşul olmalı
     - Max 3-4 koşul, en önemlilerini seç
     - Örnek: "01 Ocak - 31 Aralık 2025 tarihleri arasında Maximum Kart'ınız ile etstur.com üzerinden alacağınız yurt içi uçak biletlerinde peşin fiyatına vade farksız 3 veya 6 taksit imkanından faydalanabilirsiniz için taksit harcamalarınız için taksit uygulamaz."
       → conditions: [
         "Kampanya 01 Ocak - 31 Aralık 2025 tarihleri arasında geçerlidir",
         "etstur.com üzerinden yurt içi uçak bileti alımlarında geçerlidir",
         "Peşin fiyatına 3 veya 6 taksit imkanı sunulmaktadır"
       ]

7. **ABSOLUTE NO-HALLUCINATION RULE:**
   - IF not explicitly found -> return null.
   - NEVER use placeholder numbers.
`;

    const dynamicContent = `
CONTEXT: Today is ${today}.
BANK AUTHORITY: ${bank || 'Akbank'}
CARD AUTHORITY: ${card || 'Axess'}

TEXT TO PROCESS:
"${text.replace(/"/g, '\\"')}"
`;

    const stage1Prompt = staticPrefix + dynamicContent;

    console.log(`   ${modelLabel} Stage 1: Full parse...`);

    const { data: stage1Data, totalTokens: tokens1 } = await callGeminiAPI(stage1Prompt, selectedModel, usePython);

    // Check for missing critical fields
    const missingFields = checkMissingFields(stage1Data);

    if (missingFields.length === 0) {
        console.log('   ✅ Stage 1: Complete (all fields extracted)');

        // Ensure brand is properly formatted as a string/json for DB
        if (Array.isArray(stage1Data.brand)) {
            stage1Data.brand = stage1Data.brand.join(', ');
        }

        // STRICT OVERRIDE: Source Bank/Card TRUMPS AI
        if (bank) {
            stage1Data.bank = bank;
        }
        if (card) {
            stage1Data.card_name = card;
        }

        // 🚨 VALIDATION LAYER - Gemini's Recommendation
        const { validateAIParsing } = await import('./aiValidator');
        const validation = validateAIParsing(stage1Data);

        if (!validation.isValid) {
            console.log('   ⚠️  Validation errors detected:');
            validation.errors.forEach(err => console.log(`      ${err}`));
            console.log('   🔄 Triggering Surgical Parse to fix issues...');

            // Determine which fields need fixing based on validation errors
            const fieldsToFix: string[] = [];
            if (validation.errors.some(e => e.includes('min_spend'))) fieldsToFix.push('min_spend');
            if (validation.errors.some(e => e.includes('earning'))) fieldsToFix.push('earning');

            if (fieldsToFix.length > 0) {
                const fixedData = await parseSurgical(campaignText, stage1Data, fieldsToFix, url, bank, metadata);

                // Re-validate after surgical fix
                const revalidation = validateAIParsing(fixedData);
                if (!revalidation.isValid) {
                    console.log('   ⚠️  WARNING: Still has validation errors after surgical fix:');
                    revalidation.errors.forEach(err => console.log(`      ${err}`));
                }

                return fixedData;
            }
        }

        if (stage1Data && typeof stage1Data === 'object') {
            stage1Data.ai_method = modelLabel;
            stage1Data.ai_tokens = tokens1;

            // 🚨 FAILSAFE: Truncate marketing text if too long (ONLY FOR CHIPPIN)
            if (stage1Data.ai_marketing_text && bank.toLowerCase().includes('chippin')) {
                const words = stage1Data.ai_marketing_text.split(/\s+/);
                if (words.length > 12) { // Tolerance of 12
                    console.log(`   ✂️ Truncating long marketing text (${words.length} words): "${stage1Data.ai_marketing_text}"`);
                    // Prefer earning if available, otherwise truncate
                    if (stage1Data.earning && stage1Data.earning.length < 50 && !stage1Data.earning.includes('%')) {
                        stage1Data.ai_marketing_text = stage1Data.earning;
                    } else {
                        stage1Data.ai_marketing_text = words.slice(0, 10).join(' ') + '...';
                    }
                }
            }
        }

        return stage1Data;
    }

    // STAGE 2: Fill Missing Fields
    console.log(`   🔄 Stage 2: Filling missing fields: ${missingFields.join(', ')} `);

    const stage2Prompt = `
You are refining campaign data. The following fields are MISSING and MUST be extracted:

${missingFields.map(field => `- ${field}`).join('\n')}

Extract ONLY these missing fields from the text below. Return JSON with ONLY these fields.
${getBankInstructions(bank || '', card || '')}

FIELD DEFINITIONS:
- valid_until: Campaign end date in YYYY-MM-DD format
  🚨 DATE EXTRACTION RULES:
  1. Look for patterns like: "1 Ocak - 31 Aralık 2026", "31 Aralık 2026'ya kadar", "2026 yılı sonuna kadar"
  2. Turkish months: Ocak=01, Şubat=02, Mart=03, Nisan=04, Mayıs=05, Haziran=06, Temmuz=07, Ağustos=08, Eylül=09, Ekim=10, Kasım=11, Aralık=12
  3. For date ranges (e.g., "1 Ocak - 31 Aralık 2026"), use the END date (31 Aralık 2026 → 2026-12-31)
  4. If only month+year mentioned (e.g., "Aralık 2026"), use last day of that month (2026-12-31)
  5. If "yıl sonuna kadar" or similar, use December 31 of that year
  6. Format: YYYY-MM-DD (e.g., 2026-12-31)
  7. If NO date found, return null
- eligible_customers: Array of eligible card types
- min_spend: Minimum spending amount as a number
- earning: Reward amount or description (e.g. "500 TL Puan")
  - If it's JUST an installment campaign (taksit) and NO points/rewards mentioned, earning MUST be a 2-3 word summary (e.g., "Taksit İmkanı", "Vade Farksız")
- category: MUST be EXACTLY one of: ${masterData.categories.join(', ')}. If unsure, return "Diğer".
- bank: MUST be EXACTLY one of: ${masterData.banks.join(', ')}. ${bank ? `(Source: ${bank})` : ''}
- brand: Array of strings representing ALL mentioned merchants/brands. DO NOT include card names (Axess, Wings, etc.).

### 🛑 CRITICAL: NO HALLUCINATION
- If the requested field is NOT clearly present in the text, return null.
- DO NOT invent numbers or dates.
- DO NOT use previous campaign values.

TEXT:
"${text.replace(/"/g, '\\"')}"

Return ONLY valid JSON with the missing fields, no markdown.
`;

    const { data: stage2Data, totalTokens: tokens2 } = await callGeminiAPI(stage2Prompt, FLASH_MODEL, usePython);

    if (stage2Data && typeof stage2Data === 'object') {
        stage2Data.ai_method = `${selectedModel} [STAGE2]${usePython ? ' [PYTHON]' : ''}`;
        stage2Data.ai_tokens = tokens1 + tokens2;
    }

    // Merge stage 1 and stage 2 data
    const finalData = {
        ...stage1Data,
        ...stage2Data
    };

    const title = finalData.title || '';
    const description = finalData.description || '';

    // STAGE 3: Bank Service Detection & "Genel" logic
    // Detect keywords for bank-only services (not related to a specific merchant brand)
    const isBankService = /ekstre|nakit avans|kredi kartı başvurusu|limit artış|borç transferi|borç erteleme|başvuru|otomatik ödeme|kira|harç|bağış/i.test(title + ' ' + description);

    // STAGE 4: Historical Assignment Lookup (Learning Mechanism)
    // Check if this specific campaign was previously mapped to a brand by the user
    const { data: pastCampaign } = await supabase
        .from('campaigns')
        .select('brand, category')
        .eq('title', title)
        .not('brand', 'is', null)
        .not('brand', 'eq', '')
        .order('created_at', { ascending: false })
        .limit(1)
        .maybeSingle();

    // Use unified brand cleanup
    const masterDataForFinal = await fetchMasterData();
    const brandCleaned = await cleanupBrands(finalData.brand, masterDataForFinal);

    finalData.brand = brandCleaned.brand;
    finalData.brand_suggestion = brandCleaned.suggestion;

    if (isBankService) {
        console.log(`   🏦 Bank service detected for "${title}", mapping to "Genel"`);
        finalData.brand = 'Genel';
        finalData.brand_suggestion = ''; // Clear suggestion if it's a bank service
    } else if (pastCampaign) {
        console.log(`   🧠 Learning: Previously mapped to brand "${pastCampaign.brand}" for "${title}"`);
        finalData.brand = pastCampaign.brand;
        finalData.brand_suggestion = ''; // Use historical data, clear suggestion

        // Validate learned category against master list logic
        if (pastCampaign.category && masterData.categories.includes(pastCampaign.category)) {
            finalData.category = pastCampaign.category;
        } else if (pastCampaign.category) {
            console.log(`   ⚠️  Ignoring invalid learned category: "${pastCampaign.category}"`);
        }
    }

    // 🔗 Generic Brand Fallback (Genel) if still empty
    if (!finalData.brand || finalData.brand === '') {
        const titleLower = title.toLowerCase();
        const descLower = description.toLowerCase();

        // Keywords that strongly hint at "Genel" (non-brand specific or loyalty points)
        const genericKeywords = [
            'marketlerde', 'akaryakıt istasyonlarında', 'giyim mağazalarında',
            'restoranlarda', 'kafe', 'tüm sektörler', 'seçili sektörl',
            'üye işyeri', 'pos', 'vade farksız', 'taksit', 'faizsiz', 'masrafsız',
            'alışverişlerinizde', 'harcamanıza', 'ödemelerinize', 'chip-para', 'puan'
        ];

        if (genericKeywords.some(kw => titleLower.includes(kw) || descLower.includes(kw))) {
            finalData.brand = 'Genel';
        }
    }

    // Category Validation: Ensure it's in the master list
    const masterCategories = masterData.categories;
    if (finalData.category && !masterCategories.includes(finalData.category)) {
        console.warn(`   ⚠️  AI returned invalid category: "${finalData.category}", mapping to "Diğer"`);
        finalData.category = 'Diğer';
    }

    // Generate sector_slug from category
    if (finalData.category) {
        if (finalData.category === 'Diğer' || finalData.category === 'Genel') {
            const titleLower = title.toLowerCase();
            if (titleLower.includes('market') || titleLower.includes('gıda')) finalData.category = 'Market & Gıda';
            else if (titleLower.includes('giyim') || titleLower.includes('moda') || titleLower.includes('aksesuar')) finalData.category = 'Giyim & Aksesuar';
            else if (titleLower.includes('akaryakıt') || titleLower.includes('benzin') || titleLower.includes('otopet') || titleLower.includes('yakıt')) finalData.category = 'Akaryakıt';
            else if (titleLower.includes('restoran') || titleLower.includes('yemek') || titleLower.includes('kafe')) finalData.category = 'Restoran & Kafe';
            else if (titleLower.includes('seyahat') || titleLower.includes('tatil') || titleLower.includes('uçak') || titleLower.includes('otel') || titleLower.includes('konaklama')) finalData.category = 'Turizm & Konaklama';
            else if (titleLower.includes('elektronik') || titleLower.includes('teknoloji')) finalData.category = 'Elektronik';
            else if (titleLower.includes('mobilya') || titleLower.includes('dekorasyon')) finalData.category = 'Mobilya & Dekorasyon';
            else if (titleLower.includes('kozmetik') || titleLower.includes('sağlık')) finalData.category = 'Kozmetik & Sağlık';
        }
        finalData.sector_slug = generateSectorSlug(finalData.category);
    } else {
        finalData.category = 'Diğer';
        finalData.sector_slug = 'diger';
    }

    console.log('   ✅ Stage 2: Complete');

    // SYNC EARNING AND DISCOUNT
    syncEarningAndDiscount(finalData);

    const stillMissing = checkMissingFields(finalData);
    if (stillMissing.length > 0) {
        console.warn(`   ⚠️  WARNING: Still missing critical fields: ${stillMissing.join(', ')} `);
        finalData.ai_parsing_incomplete = true;
        finalData.missing_fields = stillMissing;
    }

    // STRICT OVERRIDE BEFORE RETURN: Source Bank/Card TRUMPS AI
    // this ensures that no matter what the AI hallucinated for bank/card, the scraper's authority wins
    if (bank) {
        finalData.bank = bank;
    }
    if (card) {
        finalData.card_name = card;
    }

    // Slug generation moved to end (after all data cleaning)

    // 🚨 FAILSAFE: Truncate marketing text if too long (Apply to Final Data too)
    if (finalData.ai_marketing_text && bank.toLowerCase().includes('chippin')) {
        const words = finalData.ai_marketing_text.split(/\s+/);
        if (words.length > 12) { // Tolerance of 12
            console.log(`   ✂️ Truncating long marketing text (${words.length} words): "${finalData.ai_marketing_text}"`);
            // Prefer earning if available, otherwise truncate
            if (finalData.earning && finalData.earning.length < 50 && !finalData.earning.includes('%')) {
                finalData.ai_marketing_text = finalData.earning;
            } else {
                finalData.ai_marketing_text = words.slice(0, 10).join(' ') + '...';
            }
        }
    }

    // 🏷️ TAGS INTEGRATION
    if (!finalData.tags) finalData.tags = [];
    // Markaları da tags içine al
    if (finalData.brand && finalData.brand !== 'Genel') {
        const brands = finalData.brand.split(',').map((b: string) => b.trim());
        brands.forEach((b: string) => {
            if (!finalData.tags.includes(b)) finalData.tags.unshift(b);
        });
    }
    // Temizlik: Tekrarları kaldır
    finalData.tags = [...new Set(finalData.tags)];

    // 🔍 AŞAMA 1: TERSİNE MARKA ARAMA (Dedektif Modu)
    // AI markayı bulamadıysa ama başlıkta geçiyorsa yakala
    if (!finalData.brand || finalData.brand === 'Genel' || finalData.brand.trim() === '') {
        const titleLower = (finalData.title || '').toLocaleLowerCase('tr-TR');
        const descLower = (finalData.description || '').toLocaleLowerCase('tr-TR');
        const searchText = `${titleLower} ${descLower}`;

        for (const masterBrand of masterData.brands) {
            const brandLower = masterBrand.toLocaleLowerCase('tr-TR');
            if (searchText.includes(brandLower)) {
                finalData.brand = masterBrand;
                finalData.brand_suggestion = '';
                console.log(`   🔍 Dedektif: Başlıkta gizli marka bulundu -> ${masterBrand}`);
                break; // İlk eşleşmeyi al
            }
        }
    }

    // 🛡️ AŞAMA 2: KELİME BAZLI SEKTÖR DÜZELTME (Sektör Kurtarıcı)
    // Marka 'Genel' kalsa bile sektörü 'Diğer' olmaktan kurtar
    if (finalData.brand === 'Genel' || finalData.category === 'Diğer') {
        const titleLower = (finalData.title || '').toLocaleLowerCase('tr-TR');
        const descLower = (finalData.description || '').toLocaleLowerCase('tr-TR');
        const searchText = `${titleLower} ${descLower}`;

        // Sektör eşleştirme kuralları
        const sectorRules = [
            { keywords: ['market', 'gıda', 'bakkal', 'süpermarket', 'manav'], category: 'Market & Gıda', slug: 'market-gida' },
            { keywords: ['akaryakıt', 'benzin', 'mazot', 'otogaz', 'istasyon', 'petrol'], category: 'Akaryakıt', slug: 'akaryakit' },
            { keywords: ['giyim', 'moda', 'kıyafet', 'ayakkabı', 'tekstil', 'çanta'], category: 'Giyim & Aksesuar', slug: 'giyim-aksesuar' },
            { keywords: ['restoran', 'yemek', 'kafe', 'kahve', 'burger', 'pizza', 'fast food'], category: 'Restoran & Kafe', slug: 'restoran-kafe' },
            { keywords: ['seyahat', 'tatil', 'otel', 'uçak', 'bilet', 'turizm', 'konaklama'], category: 'Turizm & Konaklama', slug: 'turizm-konaklama' },
            { keywords: ['elektronik', 'teknoloji', 'telefon', 'bilgisayar', 'beyaz eşya'], category: 'Elektronik', slug: 'elektronik' },
            { keywords: ['mobilya', 'dekorasyon', 'yatak', 'ev tekstili'], category: 'Mobilya & Dekorasyon', slug: 'mobilya-dekorasyon' },
            { keywords: ['sağlık', 'hastane', 'eczane', 'kozmetik', 'bakım'], category: 'Kozmetik & Sağlık', slug: 'kozmetik-saglik' },
            { keywords: ['e-ticaret', 'internet alışverişi', 'online alışveriş'], category: 'E-Ticaret', slug: 'e-ticaret' }
        ];

        for (const rule of sectorRules) {
            if (rule.keywords.some(keyword => searchText.includes(keyword))) {
                finalData.category = rule.category;
                finalData.sector_slug = rule.slug;
                console.log(`   🛡️ Sektör Kurtarıcı: '${rule.category}' olarak güncellendi (Kelime: ${rule.keywords.find(k => searchText.includes(k))})`);
                break;
            }
        }
    }

    // 🔗 GENERATE SEO SLUG (Final step - after all data is cleaned and finalized)
    // Note: Scrapers may override title, so they should regenerate slug if they do
    if (finalData.title) {
        finalData.slug = generateCampaignSlug(finalData.title);
    }

    return finalData;
}

function normalizeBrands(brandData: any): string[] {
    // Handle null/undefined
    if (!brandData) return [];

    // If it's already an array
    if (Array.isArray(brandData)) {
        return brandData
            .map(b => {
                // Remove quotes and extra whitespace
                if (typeof b === 'string') {
                    return b.replace(/^["']|["']$/g, '').trim();
                }
                return String(b).trim();
            })
            .filter(b => b && b !== '""' && b !== "''") // Remove empty strings and quote-only strings
            .flatMap(b => {
                // Split comma-separated brands
                if (b.includes(',')) {
                    return b.split(',').map(x => x.trim()).filter(x => x);
                }
                return [b];
            });
    }

    // If it's a string (shouldn't happen but handle it)
    if (typeof brandData === 'string') {
        const cleaned = brandData.replace(/^["'\[]|["'\]]$/g, '').trim();

        if (!cleaned || cleaned === '""' || cleaned === "''") {
            return [];
        }

        // Split by comma if present
        if (cleaned.includes(',')) {
            return cleaned.split(',')
                .map(b => b.trim().replace(/^["']|["']$/g, '').trim())
                .filter(b => b && b !== '""' && b !== "''");
        }

        return [cleaned];
    }

    return [];
}
