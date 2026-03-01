
import { createClient } from '@supabase/supabase-js';
import * as dotenv from 'dotenv';

dotenv.config();

const supabase = createClient(
    process.env.SUPABASE_URL!,
    process.env.SUPABASE_ANON_KEY!
);

const CF_ACCOUNT_ID = process.env.CLOUDFLARE_ACCOUNT_ID;
const CF_API_TOKEN = process.env.CLOUDFLARE_API_TOKEN;

async function deleteFromCloudflare(url: string): Promise<boolean> {
    if (!url || !url.includes('imagedelivery.net')) return false;
    if (!CF_ACCOUNT_ID || !CF_API_TOKEN) {
        console.error('      ⚠️  Cloudflare credentials missing. Skipping deletion.');
        return false;
    }

    try {
        // Extract ID from: https://imagedelivery.net/[HASH]/[IMAGE_ID]/[VARIANT]
        const parts = url.split('/');
        const imageId = parts[parts.length - 2]; // The item before the variant

        if (!imageId) return false;

        console.log(`      📤 Deleting from Cloudflare: ${imageId}`);
        const response = await fetch(
            `https://api.cloudflare.com/client/v4/accounts/${CF_ACCOUNT_ID}/images/v1/${imageId}`,
            {
                method: 'DELETE',
                headers: {
                    Authorization: `Bearer ${CF_API_TOKEN}`,
                },
            }
        );

        const data = await response.json();
        return data.success;
    } catch (e: any) {
        console.error(`      ❌ Cloudflare DELETE error: ${e.message}`);
        return false;
    }
}

async function garbageCollect() {
    console.log('🧹 Running Garbage Collector for Campaigns...\n');

    // ============================================
    // STAGE 1: Deactivate Expired Campaigns
    // ============================================
    console.log('📦 STAGE 1: Deactivating expired campaigns...');

    const today = new Date();
    today.setHours(0, 0, 0, 0);
    const todayStr = today.toISOString().split('T')[0];

    console.log(`   Today: ${todayStr}`);
    console.log(`   Deactivating campaigns with valid_until < ${todayStr}\n`);

    // Count expired campaigns that are still active
    const { count: expiredCount } = await supabase
        .from('campaigns')
        .select('*', { count: 'exact', head: true })
        .lt('valid_until', todayStr)
        .eq('is_active', true);

    if (!expiredCount || expiredCount === 0) {
        console.log('   ✅ No expired campaigns to deactivate.\n');
    } else {
        console.log(`   📂 Found ${expiredCount} expired campaigns. Deactivating...`);

        const { error: deactivateError } = await supabase
            .from('campaigns')
            .update({
                is_active: false
            })
            .lt('valid_until', todayStr)
            .eq('is_active', true);

        if (deactivateError) {
            console.error(`   ❌ Error deactivating expired campaigns: ${deactivateError.message}\n`);
        } else {
            console.log(`   ✅ Successfully deactivated ${expiredCount} expired campaigns.\n`);
        }
    }

    // ============================================
    // STAGE 2: Delete Old Inactive Campaigns + Cloudflare Images
    // ============================================
    console.log('🗑️  STAGE 2: Deleting old inactive campaigns...');

    const tenDaysAgo = new Date();
    tenDaysAgo.setDate(tenDaysAgo.getDate() - 10);
    const tenDaysAgoStr = tenDaysAgo.toISOString().split('T')[0];

    console.log(`   Older than: ${tenDaysAgoStr}\n`);

    // 1. Fetch campaigns to get their image URLs before deletion
    const { data: oldCampaigns, error: fetchError } = await supabase
        .from('campaigns')
        .select('id, image_url')
        .eq('is_active', false)
        .lt('valid_until', tenDaysAgoStr);

    if (fetchError) {
        console.error(`   ❌ Error fetching old campaigns: ${fetchError.message}\n`);
        return;
    }

    if (!oldCampaigns || oldCampaigns.length === 0) {
        console.log('   ✅ No old inactive campaigns to delete.\n');
    } else {
        console.log(`   🗑️  Processing ${oldCampaigns.length} campaigns for cleanup...`);

        let deletedImages = 0;
        for (const camp of oldCampaigns) {
            // Delete image from Cloudflare if it exists
            if (camp.image_url) {
                const success = await deleteFromCloudflare(camp.image_url);
                if (success) deletedImages++;
            }
        }

        // 2. Perform DB deletion
        const { error: deleteError } = await supabase
            .from('campaigns')
            .delete()
            .in('id', oldCampaigns.map(c => c.id));

        if (deleteError) {
            console.error(`   ❌ Error deleting DB records: ${deleteError.message}\n`);
        } else {
            console.log(`   ✅ Successfully deleted ${oldCampaigns.length} DB records.`);
            console.log(`   ✅ Successfully cleaned up ${deletedImages} images from Cloudflare.\n`);
        }
    }

    // ============================================
    // Summary
    // ============================================
    console.log('📊 SUMMARY:');
    console.log(`   Deactivated: ${expiredCount || 0} expired campaigns`);
    console.log(`   Permanently Deleted: ${oldCampaigns?.length || 0} campaigns`);
    console.log('\n✅ Garbage collection completed!');
}

garbageCollect();
