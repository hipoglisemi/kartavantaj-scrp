/**
 * Dynamic Bank Name Mapper - Fetches from Supabase master_banks
 * This replaces the static BANK_NAME_MAP with a dynamic system
 */

import { supabase } from './supabase';

interface MasterCard {
    id: string;
    name: string;
    logo?: string;
}

interface MasterBank {
    id: number;
    name: string;
    slug: string;
    aliases: string[];
    logo_url?: string;
    is_active: boolean;
    cards: MasterCard[];
}

let cachedBanks: MasterBank[] = [];
let lastFetch: number = 0;
const CACHE_DURATION = 5 * 60 * 1000; // 5 minutes

/**
 * Fetch banks from Supabase (with caching)
 */
async function fetchMasterBanks(): Promise<MasterBank[]> {
    const now = Date.now();

    // Return cache if still valid
    if (cachedBanks.length > 0 && (now - lastFetch) < CACHE_DURATION) {
        return cachedBanks;
    }

    try {
        const { data, error } = await supabase
            .from('bank_configs')
            .select('bank_id, bank_name, aliases, logo, cards')
            .order('bank_name');

        if (error) {
            console.error('❌ Error fetching bank_configs:', error.message);
            // Fallback to static list if DB fails
            return getStaticBankList();
        }

        cachedBanks = data.map(b => ({
            id: 0,
            name: b.bank_name,
            slug: b.bank_id,
            aliases: b.aliases || [],
            logo_url: b.logo,
            cards: b.cards || [],
            is_active: true
        })) || [];

        lastFetch = now;
        return cachedBanks;
    } catch (err) {
        console.error('❌ Exception fetching bank_configs:', err);
        return getStaticBankList();
    }
}

/**
 * Normalize bank name using master_banks table
 */
export async function normalizeBankName(inputName: string): Promise<string> {
    if (!inputName) return '';

    // Normalize whitespace (trim and collapse multiple spaces)
    const normalizedInput = inputName.trim().replace(/\s+/g, ' ');

    const banks = await fetchMasterBanks();

    // Try exact match with normalized DB names
    const exactMatch = banks.find(b => b.name.trim().replace(/\s+/g, ' ') === normalizedInput);
    if (exactMatch) return exactMatch.name;

    // Try case-insensitive match with normalized DB names
    const normalizedInputLower = normalizedInput.toLowerCase();
    const caseMatch = banks.find(b => b.name.trim().replace(/\s+/g, ' ').toLowerCase() === normalizedInputLower);
    if (caseMatch) return caseMatch.name;

    // Try aliases
    const aliasMatch = banks.find(b =>
        b.aliases && b.aliases.some(alias => alias.trim().replace(/\s+/g, ' ').toLowerCase() === normalizedInputLower)
    );
    if (aliasMatch) return aliasMatch.name;

    // No match found, return original (will be caught in data quality checks)
    console.warn(`⚠️  Unknown bank name: "${inputName}" - please add to bank_configs`);
    return inputName;
}

/**
 * Normalize card name for a specific bank
 */
export async function normalizeCardName(bankName: string, inputCardName: string): Promise<string> {
    if (!inputCardName) return '';

    const normalizedInput = inputCardName.trim().replace(/\s+/g, ' ');
    const banks = await fetchMasterBanks();

    // Find the bank first
    const bank = banks.find(b =>
        b.name.toLowerCase() === bankName.toLowerCase() ||
        (b.aliases && b.aliases.some(alias => alias.toLowerCase() === bankName.toLowerCase()))
    );

    if (!bank) return normalizedInput;

    // Try matches with normalized DB names
    const normalizedInputLower = normalizedInput.toLowerCase();
    const exactMatch = bank.cards.find(c => c.name.trim().replace(/\s+/g, ' ') === normalizedInput);
    if (exactMatch) return exactMatch.name;

    const caseMatch = bank.cards.find(c => c.name.trim().replace(/\s+/g, ' ').toLowerCase() === normalizedInputLower);
    if (caseMatch) return caseMatch.name;

    // No match, return original
    return normalizedInput;
}

/**
 * Get all official bank names
 */
export async function getOfficialBankNames(): Promise<string[]> {
    const banks = await fetchMasterBanks();
    return banks.map(b => b.name);
}

/**
 * Fallback static bank list (used if Supabase is unavailable)
 */
function getStaticBankList(): MasterBank[] {
    return [
        { id: 1, name: 'Garanti BBVA', slug: 'garanti-bbva', aliases: ['Garanti', 'BBVA'], is_active: true, cards: [{ id: 'bonus', name: 'Bonus' }] },
        { id: 2, name: 'Akbank', slug: 'akbank', aliases: ['Akbank'], is_active: true, cards: [{ id: 'axess', name: 'Axess' }, { id: 'wings', name: 'Wings' }] },
        { id: 3, name: 'İş Bankası', slug: 'is-bankasi', aliases: ['Is Bankasi', 'Isbank'], is_active: true, cards: [{ id: 'maximum', name: 'Maximum' }] },
        { id: 4, name: 'Yapı Kredi', slug: 'yapi-kredi', aliases: ['Yapı Kredi', 'Yapi Kredi', 'YKB'], is_active: true, cards: [{ id: 'world', name: 'World' }] },
        { id: 5, name: 'Ziraat', slug: 'ziraat-bankasi', aliases: ['Ziraat Bankası', 'Ziraat Bankasi'], is_active: true, cards: [{ id: 'bankkart', name: 'Bankkart' }] },
        { id: 6, name: 'Halkbank', slug: 'halkbank', aliases: ['Halk Bankası'], is_active: true, cards: [{ id: 'paraf', name: 'Paraf' }] },
        { id: 7, name: 'Vakıfbank', slug: 'vakifbank', aliases: ['Vakifbank', 'VakıfBank'], is_active: true, cards: [{ id: 'world', name: 'World' }] },
    ];
}

/**
 * Synchronous version (uses cache, may be stale)
 * Use this only when async is not possible
 */
export function normalizeBankNameSync(inputName: string): string {
    if (!inputName) return '';

    // Normalize whitespace (trim and collapse multiple spaces)
    const normalizedInput = inputName.trim().replace(/\s+/g, ' ');

    // Use cached data
    if (cachedBanks.length === 0) {
        // No cache, use static fallback
        cachedBanks = getStaticBankList();
    }

    const exactMatch = cachedBanks.find(b => b.name === normalizedInput);
    if (exactMatch) return exactMatch.name;

    const caseMatch = cachedBanks.find(b => b.name.toLowerCase() === normalizedInput.toLowerCase());
    if (caseMatch) return caseMatch.name;

    const aliasMatch = cachedBanks.find(b =>
        b.aliases && b.aliases.some(alias => alias.toLowerCase() === normalizedInput.toLowerCase())
    );
    if (aliasMatch) return aliasMatch.name;

    return normalizedInput;
}
