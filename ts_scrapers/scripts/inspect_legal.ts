
import { createClient } from '@supabase/supabase-js';
import * as dotenv from 'dotenv';
dotenv.config();

const supabase = createClient(
    process.env.SUPABASE_URL!,
    process.env.SUPABASE_ANON_KEY!
);

async function main() {
    console.log("🔍 INSPECTING LEGAL TEXTS...");

    const { data: settings, error } = await supabase.from('site_settings').select('settings').single();
    if (error) console.error("❌ Error:", error);
    else {
        const legal = settings.settings?.legal;
        if (legal) {
            console.log("📜 KVKK Length:", legal.kvkk?.length);
            console.log("📜 KVKK Preview:", legal.kvkk?.substring(0, 50));
            console.log("📜 Terms Length:", legal.terms?.length);
            console.log("📜 Terms Preview:", legal.terms?.substring(0, 50));
        } else {
            console.log("❌ No Legal section found in settings.");
        }
    }
}
main();
