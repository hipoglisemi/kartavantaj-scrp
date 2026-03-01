import * as dotenv from 'dotenv';
import { generateCampaignSlug } from '../utils/slugify';
import { Page } from 'puppeteer';
import { supabase } from '../utils/supabase';
import axios from 'axios';
import FormData from 'form-data';

dotenv.config();

const BUCKET_NAME = 'campaign-images';
const CLOUDFLARE_ACCOUNT_ID = process.env.CLOUDFLARE_ACCOUNT_ID;
const CLOUDFLARE_API_TOKEN = process.env.CLOUDFLARE_API_TOKEN;
const CLOUDFLARE_ACCOUNT_HASH = process.env.NEXT_PUBLIC_CLOUDFLARE_ACCOUNT_HASH;

// 🛡️ Banks that MUST use Supabase Bridge to bypass WAF/Hotlinking
const BRIDGE_BANKS = ['maximum', 'maximiles', 'chippin', 'turkcell', 'isbankasi', 'is-bankasi'];

/**
 * Downloads an image and uploads to either Supabase Storage (Bridge) or Cloudflare Directly.
 * 
 * Logic:
 * - Bridge Banks (Maximum etc.): Bank -> Supabase -> Cloudflare (via cron)
 * - Safe Banks (Axess etc.): Bank -> Cloudflare Directly
 */
export async function downloadImageDirectly(
    imageUrl: string,
    title: string,
    bankName: string = 'chippin',
    page?: Page
): Promise<string> {
    if (!imageUrl) return '';

    const bankLower = bankName.toLowerCase();
    const isBridgeBank = BRIDGE_BANKS.includes(bankLower);

    // Consistent filename for "Upsert" logic
    const slug = generateCampaignSlug(title).substring(0, 50);
    const filename = `${bankLower}/${slug}.jpg`;

    try {
        let buffer: Buffer;
        let contentType: string = 'image/jpeg';

        // 1. CAPTURE IMAGE DATA (Always prioritize Browser Context if page exists)
        if (page) {
            console.log(`   🌐 Fetching via Browser Context: ${imageUrl}`);
            const base64Data = await page.evaluate(async (url) => {
                const response = await fetch(url);
                const blob = await response.blob();
                return new Promise<string>((resolve, reject) => {
                    const reader = new FileReader();
                    reader.onloadend = () => resolve(reader.result as string);
                    reader.onerror = reject;
                    reader.readAsDataURL(blob);
                });
            }, imageUrl);

            const base64Content = base64Data.split(',')[1];
            buffer = Buffer.from(base64Content, 'base64');
            contentType = base64Data.split(';')[0].split(':')[1] || 'image/jpeg';
        } else {
            console.log(`   🖼️  Attempting server-side download: ${imageUrl}`);
            const response = await axios.get(imageUrl, {
                responseType: 'arraybuffer',
                headers: {
                    'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                    'Referer': imageUrl.includes('chippin') ? 'https://www.chippin.com/' : 'https://www.maximum.com.tr/',
                },
                timeout: 10000
            });
            buffer = Buffer.from(response.data);
            contentType = response.headers['content-type'] || 'image/jpeg';
        }

        // 2. CHOOSE DESTINATION (Supabase Bridge vs Cloudflare Direct)
        if (isBridgeBank) {
            console.log(`   🏗️  [BRIDGE] Uploading to Supabase Storage: ${filename}`);
            const { error: uploadError } = await supabase.storage
                .from(BUCKET_NAME)
                .upload(filename, buffer, {
                    contentType: contentType,
                    upsert: true
                });

            if (uploadError) throw new Error(`Supabase Error: ${uploadError.message}`);

            const { data: { publicUrl } } = supabase.storage.from(BUCKET_NAME).getPublicUrl(filename);
            console.log(`   ✅ Supabase Bridge Ready: ${publicUrl}`);
            return publicUrl;
        } else {
            // Direct Cloudflare Upload for Safe Banks
            console.log(`   ⚡ [DIRECT] Uploading to Cloudflare: ${filename}`);
            const imageId = `campaign-${bankLower}-${slug}-${Date.now()}`;
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
                console.log(`   ✅ Cloudflare Direct Success: ${cloudflareUrl}`);
                return cloudflareUrl;
            } else {
                throw new Error(`Cloudflare Error: ${JSON.stringify(cfResponse.data.errors)}`);
            }
        }

    } catch (error: any) {
        console.error(`   ❌ Failed to process image for ${bankName}: ${error.message}`);
        return imageUrl; // Fallback to original
    }
}

/**
 * Cleanup: Deletes processed image from Supabase after successful Cloudflare migration.
 */
export async function deleteFromSupabase(path: string): Promise<void> {
    const { error } = await supabase.storage.from(BUCKET_NAME).remove([path]);
    if (error) {
        console.error(`   ⚠️ Failed to delete from Supabase: ${error.message}`);
    } else {
        console.log(`   🗑️ Cleaned up from Supabase: ${path}`);
    }
}
