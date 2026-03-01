// src/utils/uniqueSlugGenerator.ts
import { supabase } from './supabase';

function slugify(text: string): string {
    return text
        .toLowerCase()
        .replace(/\s*&\s*/g, '-')
        .replace(/\s+/g, '-')
        .replace(/ı/g, 'i')
        .replace(/ğ/g, 'g')
        .replace(/ü/g, 'u')
        .replace(/ş/g, 's')
        .replace(/ö/g, 'o')
        .replace(/ç/g, 'c')
        .replace(/[^a-z0-9-]/g, '')
        .replace(/-+/g, '-')
        .replace(/^-|-$/g, '');
}

/**
 * Generate unique slug in format: /bank-brand-sector
 * Example: /axess-teknosa-elektronik
 */
export async function generateUniqueSlug(
    cardName: string,
    brand: string | null,
    sectorSlug: string
): Promise<string> {
    const parts = [
        slugify(cardName),
        brand ? slugify(brand) : null,
        sectorSlug
    ].filter(Boolean);

    let baseSlug = parts.join('-');
    let finalSlug = baseSlug;
    let counter = 1;

    // Check for uniqueness
    while (true) {
        const { data, error } = await supabase
            .from('campaigns')
            .select('id')
            .eq('slug', finalSlug)
            .single();

        if (error || !data) {
            // Slug is unique
            break;
        }

        // Slug exists, try with counter
        finalSlug = `${baseSlug}-${counter}`;
        counter++;

        if (counter > 100) {
            // Safety limit
            console.warn(`   ⚠️  Slug generation exceeded 100 attempts: ${baseSlug}`);
            break;
        }
    }

    return finalSlug;
}
