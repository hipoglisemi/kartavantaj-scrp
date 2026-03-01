
import { createClient } from '@supabase/supabase-js';
import * as dotenv from 'dotenv';
dotenv.config();

const supabase = createClient(
    process.env.SUPABASE_URL!,
    process.env.SUPABASE_ANON_KEY!
);

async function main() {
    console.log("🔍 INSPECTING DB...");

    // 1. Check Settings
    const { data: settings, error: sErr } = await supabase.from('site_settings').select('*').single();
    if (sErr) console.error("❌ Settings Read Error:", sErr);
    else {
        console.log("✅ Settings Found. Header:", JSON.stringify(settings.settings?.header?.announcements || "MISSING"));
        console.log("   Footer:", JSON.stringify(settings.settings?.footer || "MISSING"));
    }

    // 2. Check İş Bankası Config
    const { data: isBank, error: bErr } = await supabase
        .from('banks')
        .select('*, cards(*)')
        .eq('name', 'İş Bankası')
        .single();

    if (bErr) console.error("❌ İş Bankası Not Found:", bErr);
    else {
        console.log("✅ İş Bankası Config:");
        console.log(`   Slug: ${isBank.slug}`);
        console.log(`   Cards:`, isBank.cards.map((c: any) => `${c.name} (${c.slug})`));
    }

    // 3. Check a Sample "Uncategorized" Campaign
    // We look for a recent campaign from 'İş Bankası'
    const { data: campaigns } = await supabase
        .from('campaigns')
        .select('title, bank, card_name, reference_url')
        .eq('bank', 'İş Bankası')
        .limit(3);

    if (campaigns) {
        console.log("✅ Sample Campaigns:");
        campaigns.forEach(c => {
            console.log(`   Title: ${c.title.substring(0, 30)}... | Bank: ${c.bank} | Card: ${c.card_name}`);
        });
    }
}
main();
