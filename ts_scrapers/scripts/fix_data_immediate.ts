
import { createClient } from '@supabase/supabase-js';
import * as dotenv from 'dotenv';
dotenv.config();

const supabase = createClient(
    process.env.SUPABASE_URL!,
    process.env.SUPABASE_ANON_KEY!
);

async function main() {
    console.log("🚑 EMERGENCY DATA REPAIR STARTING...");

    // 1. FIX UNKNOWN CARDS (For İş Bankası)
    console.log("🔧 Fixing 'Unknown Card' for İş Bankası...");
    const { error: updateError, count } = await supabase
        .from('campaigns')
        .update({ card_name: 'Maximum' })
        .eq('bank', 'İş Bankası')
        .eq('card_name', 'Unknown Card')
        .select('id', { count: 'exact' });

    if (updateError) {
        console.error("❌ Failed to update campaigns:", updateError);
    } else {
        console.log(`✅ Fixed ${count} Maximum Campaigns! (Set card_name='Maximum')`);
    }

    // 2. TOUCH SETTINGS (Force Refresh)
    console.log("🔧 Refreshing Site Settings...");
    const { data: settings } = await supabase.from('site_settings').select('settings').single();
    if (settings) {
        const { error: settingsError } = await supabase
            .from('site_settings')
            .update({ updated_at: new Date().toISOString() })
            .eq('id', 1); // Assuming ID 1

        if (settingsError) console.error("❌ Failed to touch settings:", settingsError);
        else console.log("✅ Site Settings refreshed.");
    }

    console.log("🏁 Repair Completed.");
}

main();
