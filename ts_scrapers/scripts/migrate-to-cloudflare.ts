import { createClient } from '@supabase/supabase-js';
import * as dotenv from 'dotenv';
import axios from 'axios';
import FormData from 'form-data';
import { supabase } from '../utils/supabase';
import { deleteFromSupabase } from '../services/imageService';

dotenv.config();

const CLOUDFLARE_ACCOUNT_ID = process.env.CLOUDFLARE_ACCOUNT_ID ? process.env.CLOUDFLARE_ACCOUNT_ID.trim() : '';
const CLOUDFLARE_API_TOKEN = process.env.CLOUDFLARE_API_TOKEN ? process.env.CLOUDFLARE_API_TOKEN.trim() : '';
const CLOUDFLARE_ACCOUNT_HASH = process.env.NEXT_PUBLIC_CLOUDFLARE_ACCOUNT_HASH ? process.env.NEXT_PUBLIC_CLOUDFLARE_ACCOUNT_HASH.trim() : '';

async function migrateImages() {
    console.log('🚀 Starting Image Migration: Supabase -> Cloudflare');

    if (!CLOUDFLARE_ACCOUNT_ID || !CLOUDFLARE_API_TOKEN || !CLOUDFLARE_ACCOUNT_HASH) {
        console.error('❌ Cloudflare credentials missing!');
        return;
    }

    // 1. Find campaigns that need migration
    // These are campaigns where image_migrated is false AND the image is stored in our Supabase bucket
    const { data: campaigns, error } = await supabase
        .from('campaigns')
        .select('id, title, image, image_url, bank')
        .eq('image_migrated', false)
        .or(`image.like.%supabase.co/storage/v1/object/public/campaign-images/%,image_url.like.%supabase.co/storage/v1/object/public/campaign-images/%`)
        .limit(500); // Increased batch size to clear backlog

    if (error) {
        console.error('❌ Error fetching campaigns:', error.message);
        return;
    }

    if (!campaigns || campaigns.length === 0) {
        console.log('✅ No images need migration.');
        return;
    }

    console.log(`📦 Found ${campaigns.length} images to migrate.`);

    for (const campaign of campaigns) {
        try {
            console.log(`\n🖼️  Processing: ${campaign.title.substring(0, 40)}...`);

            // Extract path from Supabase URL
            // e.g. https://...supabase.co/storage/v1/object/public/campaign-images/maximum/slug.jpg
            const supabaseUrl = campaign.image?.includes('supabase.co/storage') ? campaign.image : campaign.image_url;

            if (!supabaseUrl || !supabaseUrl.includes('/campaign-images/')) {
                console.warn(`   ⚠️ Invalid or missing Supabase URL for ID ${campaign.id}`);
                continue;
            }

            const pathParts = supabaseUrl.split('/campaign-images/');
            const storagePath = pathParts[1];

            // 2. Download from Supabase
            const response = await axios.get(supabaseUrl, { responseType: 'arraybuffer' });
            const buffer = Buffer.from(response.data);
            const contentType = response.headers['content-type'] || 'image/jpeg';

            // 3. Upload to Cloudflare
            const imageId = `campaign-${storagePath.replace(/\//g, '-').replace('.jpg', '')}-${Date.now()}`;
            const formData = new FormData();
            formData.append('file', buffer, { filename: 'image.jpg', contentType });
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
                console.log(`   ✅ Cloudflare Upload Success: ${cloudflareUrl}`);

                // 4. Update Database
                const { error: updateError } = await supabase
                    .from('campaigns')
                    .update({
                        image: cloudflareUrl,
                        image_url: cloudflareUrl,
                        image_migrated: true
                    })
                    .eq('id', campaign.id);

                if (updateError) {
                    console.error(`   ❌ DB Update Error: ${updateError.message}`);
                } else {
                    console.log(`   🎉 Database updated and marked as migrated.`);

                    // 5. Cleanup Supabase Storage (Optional but keeps repo clean)
                    await deleteFromSupabase(storagePath);
                }
            } else {
                console.error(`   ❌ Cloudflare Error:`, cfResponse.data.errors);
            }

        } catch (err: any) {
            console.error(`   ❌ Migration failed for id ${campaign.id}: ${err.message}`);
        }
    }

    console.log('\n✅ Migration batch complete.');
}

migrateImages().catch(console.error);
