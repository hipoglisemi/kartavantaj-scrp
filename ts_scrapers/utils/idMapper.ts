/**
 * ID Mapper Utility
 * Maps string values (bank, card, brand, sector) to their database IDs
 */

import { supabase } from './supabase';

export interface IDMapping {
    bank_id?: string;
    card_id?: string;
    brand_id?: string;
    sector_id?: string;
}

/**
 * Looks up IDs for bank, card, brand, and sector from master tables
 */
export async function lookupIDs(
    bank?: string,
    cardName?: string,
    brand?: string,
    sectorSlug?: string,
    category?: string
): Promise<IDMapping> {
    const ids: IDMapping = {};

    // 1. Lookup bank_id and card_id from relational tables (banks/cards)
    if (bank) {
        // Search banks by name or aliases
        const { data: bankData } = await supabase
            .from('banks')
            .select('id, slug')
            .or(`name.ilike."${bank}",aliases.cs.{"${bank}"}`)
            .maybeSingle();

        if (bankData) {
            ids.bank_id = bankData.slug; // USE SLUG, NOT INTEGER ID

            // Find card_id within this bank's cards
            if (cardName) {
                const { data: cardData } = await supabase
                    .from('cards')
                    .select('id, slug')
                    .eq('bank_id', bankData.id)
                    .ilike('name', cardName)
                    .maybeSingle();

                if (cardData) {
                    ids.card_id = cardData.slug; // USE SLUG, NOT INTEGER ID
                }
            }
        } else {
            // Fallback for banks not yet in relational table (Legacy lookup)
            const { data: bankConfig } = await supabase
                .from('bank_configs')
                .select('bank_id, cards')
                .ilike('bank_name', bank)
                .maybeSingle();

            if (bankConfig) {
                ids.bank_id = bankConfig.bank_id;
                if (cardName && bankConfig.cards) {
                    const card = bankConfig.cards.find((c: any) =>
                        c.name.toLowerCase() === cardName.toLowerCase()
                    );
                    if (card) ids.card_id = card.id;
                }
            }
        }
    }

    // 2. Lookup brand_id from master_brands
    if (brand && brand !== 'null') {
        // Cleaning: Remove common noise
        let brandToSearch = brand.split(',')[0].split('(')[0].trim();

        // Handle "Genel" specifically
        if (brandToSearch.toLowerCase().includes('genel')) {
            brandToSearch = 'Genel';
        }

        const { data: brandData } = await supabase
            .from('master_brands')
            .select('id')
            .ilike('name', brandToSearch)
            .maybeSingle();

        if (brandData) {
            ids.brand_id = brandData.id;
        } else {
            // Fuzzy fallback: Try searching if brand name is part of a master brand
            const { data: fuzzyBrand } = await supabase
                .from('master_brands')
                .select('id')
                .ilike('name', `%${brandToSearch}%`)
                .maybeSingle();

            if (fuzzyBrand) {
                ids.brand_id = fuzzyBrand.id;
            } else if (brand.includes(',')) {
                // If comma-separated, try different parts
                const parts = brand.split(',').map(p => p.trim());
                for (const part of parts) {
                    const { data: partData } = await supabase.from('master_brands').select('id').ilike('name', part).maybeSingle();
                    if (partData) {
                        ids.brand_id = partData.id;
                        break;
                    }
                }
            }
        }
    }

    // 3. Lookup sector_id from master_sectors
    if (sectorSlug || category) {
        let query = supabase.from('master_sectors').select('id, name');

        // Priority logic: If sectorSlug is 'genel' or empty, use category
        const effectiveSlug = (sectorSlug && sectorSlug !== 'genel') ? sectorSlug : null;

        if (effectiveSlug) {
            query = query.eq('slug', effectiveSlug);
        } else if (category) {
            query = query.ilike('name', category);
        }

        const { data: sectorData } = await query.maybeSingle();

        if (sectorData) {
            ids.sector_id = sectorData.id;
        } else {
            // Fuzzy fallback: Try searching if category/slug is part of a sector name
            // e.g., "Market" -> "Market & Gıda"
            const searchTerm = category || sectorSlug;
            if (searchTerm) {
                const { data: fuzzySector } = await supabase
                    .from('master_sectors')
                    .select('id')
                    .or(`name.ilike.%${searchTerm}%,slug.ilike.%${searchTerm}%`)
                    .maybeSingle();

                if (fuzzySector) ids.sector_id = fuzzySector.id;
            }
        }
    }

    if (!ids.brand_id && brand) console.log(`   ⚠️  Brand Lookup Failed: "${brand}"`);
    if (!ids.sector_id && (sectorSlug || category)) console.log(`   ⚠️  Sector Lookup Failed: "${sectorSlug || category}"`);

    return ids;
}
