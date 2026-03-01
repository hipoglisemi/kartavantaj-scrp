
import { createClient } from '@supabase/supabase-js';
import * as dotenv from 'dotenv';
dotenv.config();

const supabase = createClient(
    process.env.SUPABASE_URL!,
    process.env.SUPABASE_ANON_KEY!
);

async function main() {
    console.log("📊 SYSTEM PERFORMANCE & DATA AUDIT...");

    // 1. Campaign Count
    const start = Date.now();
    const { count, error } = await supabase
        .from('campaigns')
        .select('*', { count: 'exact', head: true });
    const end = Date.now();

    if (error) console.error("❌ Campaign Count Error:", error);
    else console.log(`✅ Total Campaigns: ${count} (Fetched in ${end - start}ms)`);

    // 2. Check Recent Campaigns (Metadata check)
    const { data: recent, error: recentError } = await supabase
        .from('campaigns')
        .select('id, title, created_at, bank')
        .order('created_at', { ascending: false })
        .limit(5);

    if (recentError) console.error("❌ Recent Campaigns Error:", recentError);
    else {
        console.log("🆕 Recently Added Campaigns:");
        recent.forEach(c => console.log(`   - [${c.created_at}] ${c.bank}: ${c.title}`));
    }

    // 3. Settings Inspection (Who updated last?)
    const { data: settings, error: settingsError } = await supabase
        .from('site_settings')
        .select('*')
        .single();

    if (settingsError) console.error("❌ Settings Error:", settingsError);
    else {
        console.log("⚙️ Site Settings Meta:");
        console.log(`   Updated At: ${settings.updated_at}`);
        // console.log(`   Content Preview: ${JSON.stringify(settings.settings).substring(0, 100)}...`);
    }

    // 4. Test a "Full Fetch" like the Dashboard does
    const startFull = Date.now();
    const { data: all, error: allError } = await supabase
        .from('campaigns')
        .select('id, title, bank, brand, sector_slug, is_active, created_at')
        .limit(1000); // Typical dashboard load
    const endFull = Date.now();

    if (allError) console.error("❌ Full Fetch Error:", allError);
    else console.log(`⚡ Full Fetch (1000 rows): ${all.length} items in ${endFull - startFull}ms`);
}
main();
