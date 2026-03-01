// Sector Cache Service
// Provides cached access to sectors and keywords from Supabase
// Reduces database queries and enables dynamic keyword management

import { supabase } from './supabase';

interface SectorWithKeywords {
    id: number;
    name: string;
    slug: string;
    keywords: string[];
}

interface CacheData {
    sectors: SectorWithKeywords[];
    lastUpdated: number;
}

let cache: CacheData | null = null;
const CACHE_TTL = 5 * 60 * 1000; // 5 minutes

/**
 * Get sectors with keywords from cache or database
 * Cache is refreshed every 5 minutes
 */
export async function getSectorsWithKeywords(): Promise<SectorWithKeywords[]> {
    const now = Date.now();

    // Return cached data if fresh
    if (cache && (now - cache.lastUpdated) < CACHE_TTL) {
        return cache.sectors;
    }

    // Fetch from database
    const { data: sectorsData, error: sectorsError } = await supabase
        .from('sectors')
        .select('id, name, slug');

    if (sectorsError) {
        console.error('Error fetching sectors:', sectorsError);
        // Return empty array or fallback to hardcoded if needed
        return [];
    }

    const { data: keywordsData, error: keywordsError } = await supabase
        .from('sector_keywords')
        .select('sector_id, keyword, weight')
        .eq('is_active', true);

    if (keywordsError) {
        console.error('Error fetching keywords:', keywordsError);
        // Return sectors without keywords
        return sectorsData?.map(s => ({ ...s, keywords: [] })) || [];
    }

    // Build sectors with keywords
    const sectorsWithKeywords: SectorWithKeywords[] = sectorsData?.map(sector => {
        const sectorKeywords = keywordsData
            ?.filter(kw => kw.sector_id === sector.id)
            .flatMap(kw => {
                // Repeat keyword based on weight for scoring
                const keywords = [];
                for (let i = 0; i < (kw.weight || 1); i++) {
                    keywords.push(kw.keyword);
                }
                return keywords;
            }) || [];

        return {
            ...sector,
            keywords: sectorKeywords
        };
    }) || [];

    // Update cache
    cache = {
        sectors: sectorsWithKeywords,
        lastUpdated: now
    };

    console.log(`✅ Sector cache updated: ${sectorsWithKeywords.length} sectors, ${keywordsData?.length || 0} keywords`);

    return sectorsWithKeywords;
}

/**
 * Force cache refresh (useful for admin panel updates)
 */
export function invalidateSectorCache(): void {
    cache = null;
    console.log('🔄 Sector cache invalidated');
}

/**
 * Get cache status (for debugging)
 */
export function getCacheStatus(): { isCached: boolean, age: number | null, sectorCount: number } {
    if (!cache) {
        return { isCached: false, age: null, sectorCount: 0 };
    }

    const age = Date.now() - cache.lastUpdated;
    return {
        isCached: true,
        age: age,
        sectorCount: cache.sectors.length
    };
}
