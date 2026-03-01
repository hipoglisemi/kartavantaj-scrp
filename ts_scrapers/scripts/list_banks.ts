
import { createClient } from '@supabase/supabase-js';
import * as dotenv from 'dotenv';
dotenv.config();

const supabase = createClient(
    process.env.SUPABASE_URL!,
    process.env.SUPABASE_ANON_KEY!
);

async function main() {
    const { data: banks, error } = await supabase.from('banks').select('*');
    if (error) console.error(error);
    else {
        console.log("🏦 Banks in DB Table:");
        banks.forEach(b => console.log(` - [${b.id}] Name: "${b.name}", Slug: "${b.slug}"`));
    }
}
main();
