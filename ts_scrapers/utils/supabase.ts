import { createClient } from '@supabase/supabase-js';
import * as dotenv from 'dotenv';
import * as path from 'path';

// Load directly from current execution context
dotenv.config();

// Fallback: Try to find .env relative to this file
if (!process.env.SUPABASE_URL) {
    const envPath = path.resolve(process.cwd(), '.env');
    dotenv.config({ path: envPath });
}

const supabaseUrl = process.env.SUPABASE_URL ? process.env.SUPABASE_URL.trim().replace(/^"|"$/g, '') : '';
const supabaseAnonKey = process.env.SUPABASE_ANON_KEY ? process.env.SUPABASE_ANON_KEY.trim().replace(/^"|"$/g, '') : '';
const supabaseServiceKey = process.env.SUPABASE_SERVICE_ROLE_KEY ? process.env.SUPABASE_SERVICE_ROLE_KEY.trim().replace(/^"|"$/g, '') : '';

if (!supabaseUrl || (!supabaseAnonKey && !supabaseServiceKey)) {
    console.error('❌ CRITICAL: Supabase environment variables are missing!');
    console.error('Check SUPABASE_URL and SUPABASE_ANON_KEY in your .env file.');
    process.exit(1);
}

/**
 * Campaign Data Type
 * Update this interface when adding new columns to the 'campaigns' table.
 */
export interface Campaign {
    id: number;
    title: string;
    description?: string;
    url?: string;
    reference_url?: string;
    image?: string;
    image_url?: string;
    bank: string;
    card_name?: string;
    category?: string;
    sector_slug?: string;
    brand?: string;
    badge_text?: string;
    badge_color?: string;
    valid_from?: string;
    valid_until?: string;
    min_spend?: number;
    earning?: string;
    discount_percentage?: number;
    max_discount?: number;
    is_active: boolean;
    provider: string;
    ai_enhanced: boolean;
    ai_parsing_incomplete?: boolean;
    auto_corrected?: boolean;
    quality_score?: number;
    created_at?: string;
    updated_at?: string;
    [key: string]: any; // Allows for dynamic columns added to Supabase
}

/**
 * Centralized Supabase Client
 */
export const supabase = createClient(supabaseUrl, supabaseServiceKey || supabaseAnonKey, {
    auth: { persistSession: false },
    db: { schema: 'public' }
});

/**
 * Typed table helper
 */
export const campaignsTable = () => supabase.from('campaigns');

/**
 * Validates connection to Supabase
 */
export async function testConnection() {
    try {
        const { data, error } = await supabase.from('campaigns').select('id').limit(1);
        if (error) throw error;
        return { success: true, message: 'Supabase connection established' };
    } catch (error: any) {
        return { success: false, message: `Supabase connection failed: ${error.message}` };
    }
}
