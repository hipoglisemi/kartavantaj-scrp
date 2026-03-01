
import { createClient } from '@supabase/supabase-js';
import * as dotenv from 'dotenv';
import * as path from 'path';

dotenv.config({ path: path.resolve(__dirname, '../../.env') });

const supabase = createClient(
    process.env.SUPABASE_URL!,
    process.env.SUPABASE_ANON_KEY!
);

async function main() {
    console.log("🔍 Debugging Data...");

    // 1. List all banks
    const { data: banks, error } = await supabase
        .from('campaigns')
        .select('bank');

    if (banks) {
        const uniqueBanks = [...new Set(banks.map(b => b.bank))];
        console.log("🏦 Banks in DB:", uniqueBanks);
    }

    // 2. Check Site Settings
    const { data: settings, error: settingsError } = await supabase
        .from('site_settings')
        .select('*')
        .single();

    if (settingsError) console.error("Settings Error:", settingsError);
    else {
        console.log("⚙️ Site Settings (Partial):");
        if (settings && settings.settings) {
            console.log("   Footer Desc:", settings.settings.footer?.description);
            console.log("   Announcement:", settings.settings.header?.announcements?.[0]?.text);
        } else {
            console.log("   Settings found but 'settings' column logic is weird.");
        }
    }
}

main();
