import { createClient } from '@supabase/supabase-js';
import * as dotenv from 'dotenv';
import axios from 'axios';
import FormData from 'form-data';

dotenv.config();

const CLOUDFLARE_ACCOUNT_ID = process.env.CLOUDFLARE_ACCOUNT_ID;
const CLOUDFLARE_API_TOKEN = process.env.CLOUDFLARE_API_TOKEN;
const CLOUDFLARE_ACCOUNT_HASH = process.env.NEXT_PUBLIC_CLOUDFLARE_ACCOUNT_HASH;

// Scraper project uses SUPABASE_URL instead of NEXT_PUBLIC_SUPABASE_URL
const supabaseUrl = process.env.SUPABASE_URL!;
const supabaseKey = process.env.SUPABASE_SERVICE_ROLE_KEY || process.env.SUPABASE_ANON_KEY!;
const supabase = createClient(supabaseUrl, supabaseKey);

async function fixCampaigns(ids: number[]) {
    console.log(`🛠️ Fixing campaigns: ${ids.join(', ')}`);

    for (const id of ids) {
        try {
            // 1. Get campaign data
            const { data: campaign, error } = await supabase.from('campaigns').select('*').eq('id', id).single();
            if (error || !campaign) {
                console.error(`❌ Campaign ${id} not found: ${error?.message}`);
                continue;
            }

            const imageUrl = campaign.image;
            if (imageUrl && !imageUrl.includes('maximum.com.tr') && !imageUrl.includes('isbank.com.tr')) {
                console.log(`✅ Campaign ${id} already has a non-bank image: ${imageUrl}`);
                continue;
            }

            const targetUrl = imageUrl || campaign.image_url;
            console.log(`🖼️ Migrating image for ${id}: ${targetUrl}`);

            // 2. Download via proxy
            const proxyUrl = `https://kartavantaj.com/api/proxy?url=${encodeURIComponent(targetUrl)}`;
            const response = await axios.get(proxyUrl, { responseType: 'arraybuffer' });

            // 3. Upload to Cloudflare
            const imageId = `campaign-manual-fix-${id}-${Date.now()}`;
            const formData = new FormData();
            formData.append('file', Buffer.from(response.data), 'image.jpg');
            formData.append('id', imageId);

            const cfResponse = await axios.post(
                `https://api.cloudflare.com/client/v4/accounts/${CLOUDFLARE_ACCOUNT_ID}/images/v1`,
                formData,
                {
                    headers: {
                        ...formData.getHeaders(),
                        'Authorization': `Bearer ${CLOUDFLARE_API_TOKEN}`
                    }
                }
            );

            if (cfResponse.data.success) {
                const cloudflareUrl = `https://imagedelivery.net/${CLOUDFLARE_ACCOUNT_HASH}/${cfResponse.data.result.id}/public`;
                console.log(`✅ Uploaded to Cloudflare: ${cloudflareUrl}`);

                // 4. Update Database
                const { error: updateError } = await supabase
                    .from('campaigns')
                    .update({
                        image: cloudflareUrl,
                        image_url: cloudflareUrl
                    })
                    .eq('id', id);

                if (updateError) console.error(`❌ Database update error for ${id}:`, updateError);
                else console.log(`🎉 Campaign ${id} fixed!`);
            } else {
                console.error(`❌ Cloudflare Error for ${id}:`, cfResponse.data.errors);
            }

        } catch (e: any) {
            console.error(`❌ Failed to fix campaign ${id}:`, e.message);
        }
    }
}

fixCampaigns([25099, 25100]);
