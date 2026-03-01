/**
 * Post-Processing Service
 * Automatically cleans up campaign data after AI parsing
 */

export interface CleanupResult {
    brand: string;
    earning: string;
    changes: string[];
}

/**
 * Normalize brand data - removes quotes, splits comma-separated values
 */
export function normalizeBrands(brandData: any): string[] {
    if (!brandData) return [];

    let brands: string[] = [];

    // If already array
    if (Array.isArray(brandData)) {
        brands = brandData;
    }
    // If string
    else if (typeof brandData === 'string') {
        if (brandData.startsWith('[')) {
            try {
                brands = JSON.parse(brandData);
            } catch {
                brands = [brandData];
            }
        } else {
            brands = [brandData];
        }
    }

    // Clean each brand
    return brands
        .map(b => {
            if (typeof b !== 'string') return String(b);
            // Remove quotes
            return b.replace(/^["']|["']$/g, '').trim();
        })
        .filter(b => b && b !== '""' && b !== "''") // Remove empty
        .flatMap(b => {
            // Split comma-separated
            if (b.includes(',')) {
                return b.split(',')
                    .map(x => x.trim())
                    .filter(x => x && x !== '""' && x !== "''");
            }
            return [b];
        });
}

/**
 * Extract specific installment information from campaign data
 */
export function extractInstallmentInfo(campaign: any): string {
    const { title, description, earning, discount, badge_text } = campaign;

    // If badge is not TAKSİT, return earning as is
    if (badge_text !== 'TAKSİT') {
        return earning || '';
    }

    // If earning already has specific info, keep it
    if (earning && earning !== 'TAKSİT' && /\d+/.test(earning)) {
        return earning;
    }

    // Combine all text fields
    const text = `${title || ''} ${description || ''} ${earning || ''} ${discount || ''}`.toLowerCase();

    // Pattern matching for installment info
    const patterns = [
        { regex: /peşin\s+fiyatına\s+(\d+)\s+(?:aya?\s+varan\s+)?taksit/i, format: (n: string) => `Peşin Fiyatına ${n} Taksit` },
        { regex: /(\d+)\s+aya?\s+varan\s+taksit/i, format: (n: string) => `${n} Aya Varan Taksit` },
        { regex: /(\d+)\s+ay(?:a)?\s+(?:kadar\s+)?taksit/i, format: (n: string) => `${n} Aya Kadar Taksit` },
        { regex: /(\d+)\s+taksit/i, format: (n: string) => `${n} Taksit` },
    ];

    for (const pattern of patterns) {
        const match = text.match(pattern.regex);
        if (match) {
            return pattern.format(match[1]);
        }
    }

    // Default fallback
    return earning || 'Taksit';
}

/**
 * Post-process campaign data after AI parsing
 */
export function postProcessCampaign(campaign: any): CleanupResult {
    const changes: string[] = [];

    // 1. Clean up brands
    const originalBrand = campaign.brand;
    const cleanedBrand = normalizeBrands(originalBrand);

    if (JSON.stringify(originalBrand) !== JSON.stringify(cleanedBrand)) {
        changes.push(`Brand normalized: ${JSON.stringify(originalBrand)} → ${JSON.stringify(cleanedBrand)}`);
    }

    // 2. Extract installment info
    const originalEarning = campaign.earning;
    const enhancedEarning = extractInstallmentInfo(campaign);

    if (originalEarning !== enhancedEarning) {
        changes.push(`Earning enhanced: "${originalEarning}" → "${enhancedEarning}"`);
    }

    return {
        brand: cleanedBrand.join(', '),
        earning: enhancedEarning,
        changes
    };
}
