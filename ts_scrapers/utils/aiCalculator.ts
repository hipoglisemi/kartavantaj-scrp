// src/utils/aiCalculator.ts
import { supabase } from './supabase';

const GEMINI_API_KEY = process.env.GOOGLE_GEMINI_KEY!;
const MODEL_NAME = 'gemini-2.0-flash';
const API_URL = `https://generativelanguage.googleapis.com/v1beta/models/${MODEL_NAME}:generateContent?key=${GEMINI_API_KEY}`;

interface MasterData {
    categories: string[];
    brands: string[];
}

let cachedMasterData: MasterData | null = null;

async function fetchMasterData(): Promise<MasterData> {
    if (cachedMasterData) return cachedMasterData;

    const [sectorsRes, brandsRes] = await Promise.all([
        supabase.from('master_sectors').select('name'),
        supabase.from('master_brands').select('name')
    ]);

    const categories = sectorsRes.data?.map(c => c.name) || [
        'Market & Gıda', 'Akaryakıt', 'Giyim & Aksesuar', 'Restoran & Kafe',
        'Elektronik', 'Mobilya & Dekorasyon', 'Kozmetik & Sağlık', 'E-Ticaret',
        'Ulaşım', 'Dijital Platform', 'Kültür & Sanat', 'Eğitim',
        'Sigorta', 'Otomotiv', 'Vergi & Kamu', 'Turizm & Konaklama', 'Diğer'
    ];

    const brands = brandsRes.data?.map(b => b.name) || [];

    cachedMasterData = { categories, brands };
    return cachedMasterData;
}

const DISABLE_AI_COMPLETELY = false; // Enabled for advanced calculation features

/**
 * Modern AI Calculation Engine
 * Uses Gemini 2.0 Flash with Python Code Execution for mathematical precision
 */
export async function calculateCampaignBonus(campaignText: string) {
    if (DISABLE_AI_COMPLETELY) return null;
    if (!GEMINI_API_KEY) {
        throw new Error("GOOGLE_GEMINI_KEY bulunamadı. Lütfen .env dosyanızı kontrol edin.");
    }

    const prompt = `
    Aşağıdaki banka kampanya metnini analiz et ve matematiksel hesaplamaları Python kullanarak doğrula.
    
    KAMPANYA METNİ:
    "${campaignText}"
    
    GÖREV:
    1. Metindeki harcama alt limitlerini, bonus oranlarını ve maksimum kazanım limitlerini ayıkla.
    2. Python kodunu kullanarak farklı harcama senaryoları için kazanılacak bonusu hesapla.
    3. Eğer "n. harcamadan sonra" veya "farklı günlerde" gibi şartlar varsa bunları Python mantığına (if/else) dök.
    4. Kampanya toplam üst limitini (max_bonus) her zaman bir kısıt olarak uygula.
    
    ÇIKTI FORMATI (Sadece saf JSON döndür):
    {
      "min_spend": number,
      "bonus_ratio": number,
      "max_bonus": number,
      "is_cumulative": boolean,
      "calculated_scenarios": {
          "scenario_1000tl": number,
          "scenario_5000tl": number,
          "scenario_max_target": number
      },
      "explanation": "Hesaplama mantığının kısa özeti"
    }`;

    try {
        const response = await fetch(API_URL, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                contents: [{ parts: [{ text: prompt }] }],
                tools: [{ code_execution: {} }],
                generationConfig: {
                    temperature: 0.1
                }
            })
        });

        if (!response.ok) {
            const errorBody = await response.text();
            throw new Error(`Gemini API error: ${response.status} - ${errorBody}`);
        }

        const data: any = await response.json();
        const candidates = data.candidates?.[0]?.content?.parts || [];

        // Find the part containing the JSON result (looking through all parts)
        for (const part of candidates) {
            // Check in normal text part
            if (part.text && part.text.includes('{')) {
                const jsonMatch = part.text.match(/\{[\s\S]*\}/);
                if (jsonMatch) {
                    try {
                        return JSON.parse(jsonMatch[0]);
                    } catch (e) { /* continue */ }
                }
            }
            // Check in code execution result
            if (part.codeExecutionResult && part.codeExecutionResult.output) {
                const jsonMatch = part.codeExecutionResult.output.match(/\{[\s\S]*\}/);
                if (jsonMatch) {
                    try {
                        return JSON.parse(jsonMatch[0]);
                    } catch (e) { /* continue */ }
                }
            }
        }

        throw new Error("Modelden geçerli bir JSON çıktısı alınamadı.");

    } catch (error) {
        console.error("   ❌ AI Hesaplama Hatası:", error);
        return null;
    }
}

/**
 * Legacy support for extracting brand and category (uses simpler parameters)
 */
export async function calculateMissingFields(
    rawHtml: string,
    extracted: any
): Promise<any> {
    if (DISABLE_AI_COMPLETELY) {
        return { brand: null, category: 'Diğer' };
    }

    const masterData = await fetchMasterData();

    const prompt = `Sen bir kampanya analiz asistanısın. Aşağıdaki HTML'den kampanyanın MARKA ve KATEGORİ bilgilerini çıkar.

KURALLAR:
1. MARKA: Kampanyada geçen mağaza/firma adı (örn: Teknosa, CarrefourSA, FG Europe, Türk Hava Yolları)
   - Banka adları (Akbank, Axess vb.) MARKA DEĞİLDİR
   - Kart adları (Axess, Bonus vb.) MARKA DEĞİLDİR
   - Eğer belirli bir marka yoksa null döndür
2. KATEGORİ: Aşağıdaki listeden EN UYGUN olanı seç:
   ${masterData.categories.join(', ')}

KAMPANYA BAŞLIĞI: ${extracted.title}

HTML İÇERİĞİ:
${rawHtml.substring(0, 2000)}

ÇIKTI FORMATI (sadece JSON döndür):
{
  "brand": "Marka Adı veya null",
  "category": "Kategori Adı"
}`;

    try {
        const response = await fetch(API_URL, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                contents: [{ parts: [{ text: prompt }] }],
                generationConfig: {
                    temperature: 0.1,
                    response_mime_type: "application/json"
                }
            })
        });

        if (!response.ok) throw new Error(`Gemini API error: ${response.status}`);
        const data: any = await response.json();
        const text = data.candidates?.[0]?.content?.parts?.[0]?.text;

        if (!text) throw new Error('No response from Gemini');
        const result = JSON.parse(text.replace(/```json|```/g, '').trim());

        // Normalize brand name
        if (result.brand && typeof result.brand === 'string') {
            const brandLower = result.brand.toLowerCase();
            const forbiddenTerms = ['akbank', 'axess', 'bonus', 'world', 'maximum', 'paraf', 'bankkart', 'wings', 'free', 'adios', 'play', 'crystal'];
            if (forbiddenTerms.some(term => brandLower.includes(term))) {
                result.brand = null;
            }
        }

        // Validate category
        if (result.category && !masterData.categories.includes(result.category)) {
            result.category = 'Diğer';
        }

        return {
            brand: result.brand || null,
            category: result.category || 'Diğer'
        };
    } catch (error: any) {
        console.error('   ❌ AI calculation error:', error.message);
        return {
            brand: null,
            category: 'Diğer'
        };
    }
}

