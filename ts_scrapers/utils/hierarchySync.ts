// src/utils/hierarchySync.ts
import { supabase } from './supabase';

/**
 * Get or create a brand in master_brands table (deduplication)
 */
export async function getOrCreateBrand(brandName: string | null): Promise<string | null> {
    if (!brandName || brandName.trim() === '') return null;

    const normalized = brandName.trim();

    try {
        // 1. Check if brand exists (case-insensitive)
        const { data: existing, error: searchError } = await supabase
            .from('master_brands')
            .select('id, name')
            .ilike('name', normalized)
            .single();

        if (existing) {
            return existing.id;
        }

        // 2. Brand doesn't exist, create it
        const { data: newBrand, error: insertError } = await supabase
            .from('master_brands')
            .insert([{ name: normalized }])
            .select('id')
            .single();

        if (insertError) {
            console.error(`   ❌ Error creating brand "${normalized}":`, insertError.message);
            return null;
        }

        console.log(`   ✅ Created new brand: "${normalized}" (ID: ${newBrand.id})`);
        return newBrand.id;
    } catch (error: any) {
        console.error(`   ❌ Brand deduplication error:`, error.message);
        return null;
    }
}

/**
 * Get sector ID from master_sectors table
 */
export async function getSectorId(sectorSlug: string): Promise<string | null> {
    try {
        const { data, error } = await supabase
            .from('master_sectors')
            .select('id')
            .eq('slug', sectorSlug)
            .single();

        if (error || !data) {
            console.warn(`   ⚠️  Sector not found: ${sectorSlug}`);
            return null;
        }

        return data.id;
    } catch (error: any) {
        console.error(`   ❌ Sector lookup error:`, error.message);
        return null;
    }
}

/**
 * Sync complete hierarchy: bank_id, card_id, brand_id, sector_id
 */
export async function syncHierarchy(
    bankName: string,
    cardName: string,
    brandName: string | null,
    sectorSlug: string
): Promise<{
    bank_id: string | null;
    card_id: string | null;
    brand_id: string | null;
    sector_id: string | null;
}> {
    try {
        // 1. Get bank_id and card_id from bank_configs
        const { data: bankData, error: bankError } = await supabase
            .from('bank_configs')
            .select('bank_id, cards')
            .eq('bank_name', bankName)
            .single();

        if (bankError || !bankData) {
            console.warn(`   ⚠️  Bank not found in bank_configs: ${bankName}`);
            return { bank_id: null, card_id: null, brand_id: null, sector_id: null };
        }

        const bank_id = bankData.bank_id;

        // 2. Find card_id from cards array
        const cards = bankData.cards || [];
        const card = cards.find((c: any) => c.name === cardName);
        const card_id = card?.id || null;

        if (!card_id) {
            console.warn(`   ⚠️  Card not found for ${bankName}: ${cardName}`);
        }

        // 3. Get or create brand_id
        const brand_id = await getOrCreateBrand(brandName);

        // 4. Get sector_id
        const sector_id = await getSectorId(sectorSlug);

        return { bank_id, card_id, brand_id, sector_id };
    } catch (error: any) {
        console.error(`   ❌ Hierarchy sync error:`, error.message);
        return { bank_id: null, card_id: null, brand_id: null, sector_id: null };
    }
}
