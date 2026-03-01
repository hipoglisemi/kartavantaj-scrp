
import { assignBadge } from '../services/badgeAssigner';

/**
 * Utility function to standardize and shorten benefit text for UI badges.
 * E.g., "Peşin fiyatına 6 aya varan taksit fırsatı" -> "6 Taksit"
 */
export function standardizeBenefit(text: string): string {
    if (!text || text.toLowerCase() === 'yok' || text.toLowerCase() === 'null') return text;

    let clean = text.trim();

    // 1. Standardize Taksit (extract max installment)
    // "Peşin fiyatına 6 taksit" -> "6 Taksit"
    // "100 TL Puan + 3 Taksit" -> "100 TL Puan + 3 Taksit"
    if (clean.toLowerCase().includes('taksit')) {
        const match = clean.match(/(\d+)\s*(?:aya?\s*varan\s*)?taksit/i);
        if (match) {
            // Check for additional point benefit in the SAME string
            const pointMatch = clean.match(/(\d+[\d.,]*)\s*(?:TL|Puan)/i);
            const instVal = match[1];
            if (pointMatch && pointMatch[1] !== instVal) {
                return `${pointMatch[1]} TL Puan + ${instVal} Taksit`;
            }
            return `${instVal} Taksit`;
        }
    }

    // 2. Standardize Puan / TL sums and remove fillers
    // This is for cases like "Her 500 TL'ye 50 TL, toplam 500 TL Puan"
    const lower = clean.toLowerCase();

    // Explicit TOTAL detection (common in Paraf/Ziraat)
    const totalMatch = lower.match(/toplam(?:da)?\s*(\d+[\d.,]*)\s*(?:tl|puan)/i);
    if (totalMatch) {
        return `${totalMatch[1]} TL Puan`;
    }

    clean = clean
        .replace(/['’](?:ye|ya|e|a)(?=\s)/gi, '') // Remove suffixes like TL'ye -> TL
        .replace(/peşin fiyatına|vade farksız|toplamda|varan|değerinde|hediye|fırsatı|imkanı|kazanma|ayrıcalığı/gi, '')
        .replace(/worldpuan|bonus|chip-?para|maxipuan|bankkart lira|parafpara/gi, 'Puan')
        .replace(/\s+/g, ' ')
        .trim();

    // 3. Extract pure amounts if explicitly monetary
    if (clean.toLowerCase().includes('tl')) {
        // PRESERVE: If specifically formatted for percentage + limit
        if (clean.includes('%') && (clean.includes('max') || clean.includes('en fazla'))) {
            return clean;
        }

        // Look for the LARGEST number associated with Puan/TL/Discount as it's usually the total benefit
        const amounts = clean.match(/(\d+[\d.,]*)\s*(?:TL|Puan|İndirim)/gi);
        if (amounts && amounts.length > 0) {
            const values = amounts.map(curr => {
                const num = curr.replace(/[^\d]/g, '');
                // Handle cases where we might have thousands with dots (e.g. 1.000)
                return parseFloat(num);
            });
            const maxVal = Math.max(...values);

            // If the max value is part of the original text as a benefit
            if (maxVal > 0) {
                // Return original if it's nicely formatted with limit
                if (clean.toLowerCase().includes('max')) return clean;

                if (clean.toLowerCase().includes('indirim')) return `${maxVal.toLocaleString('tr-TR')} TL İndirim`;
                return `${maxVal.toLocaleString('tr-TR')} TL Puan`;
            }
        }
    }

    // 4. Percentage simplifications
    if (clean.includes('%')) {
        // PRESERVE: Detailed percentage info (e.g. %15 (max 750TL))
        if (clean.includes('max') || clean.includes('limit') || clean.includes('en fazla')) {
            return clean;
        }

        const pctMatch = clean.match(/(%\s*\d+|\d+\s*%)/);
        if (pctMatch) {
            if (clean.toLowerCase().includes('indirim')) return `${pctMatch[1].replace(/\s/g, '')} İndirim`;
            if (clean.match(/puan|chip|bonus|world|maxi|paraf/i)) return `${pctMatch[1].replace(/\s/g, '')} Puan`;
            return `${pctMatch[1].replace(/\s/g, '')} İndirim`;
        }
    }

    // 5. Cleanup Junk (Only truly empty/zero values)
    if (clean === '0% Puan' || clean === '0 TL Puan' || clean === '0 TL' || clean === '0%') {
        return '';
    }

    // 6. Final max length truncation (be careful not to break meaningful summaries)
    if (clean.length > 25) {
        clean = clean.substring(0, 22) + '...';
    }

    return clean;
}

/**
 * Ensures earning and discount fields are synchronized and standardized.
 */
export function syncEarningAndDiscount(data: any): any {
    if (!data) return data;

    let earning = data.earning || '';
    let discount = data.discount || '';

    // Standardize both
    earning = standardizeBenefit(earning);
    discount = standardizeBenefit(discount);

    data.earning = earning;
    data.discount = discount;

    // Auto-populate merchant from brand if missing (precaution for frontend)
    if (!data.merchant && data.brand) {
        data.merchant = data.brand;
    }

    // Auto-assign badge if missing or needs update
    if (!data.badge_text) {
        const badge = assignBadge(data);
        data.badge_text = badge.text;
        data.badge_color = badge.color;
    }

    syncBrands(data);

    return data;
}

/**
 * Deduplicates and standardizes brands based on comma distribution.
 */
export function syncBrands(data: any): any {
    if (!data || !data.brand) return data;

    let brandsStr = data.brand;
    if (typeof brandsStr !== 'string') return data;

    // Split, clean, deduplicate
    const brandList = brandsStr
        .split(',')
        .map((b: string) => b.trim())
        .filter((b: string) => b && b.toLowerCase() !== 'yok' && b.toLowerCase() !== 'null');

    const uniqueBrands = [...new Set(brandList)];
    data.brand = uniqueBrands.join(', ');

    return data;
}
