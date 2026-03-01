/**
 * AI Re-Parser for Campaigns
 * Re-parses campaigns that have no AI data or missing critical fields
 */

import { supabase } from './utils/supabase';
import { parseWithGemini } from './services/geminiParser';
import { assignBadge } from './services/badgeAssigner';
import { lookupIDs } from './utils/idMapper';
import { syncEarningAndDiscount } from './utils/dataFixer';

const sleep = (ms: number) => new Promise(resolve => setTimeout(resolve, ms));

async function reprocessCampaigns(limit?: number) {
    console.log('\n🔄 AI Re-Parser for Campaigns\n');
    console.log('='.repeat(80));

    // Fetch campaigns without AI enhancement
    const { data: campaigns, error } = await supabase
        .from('campaigns')
        .select('*')
        .or('ai_enhanced.is.null,ai_enhanced.eq.false,publish_status.eq.processing')
        .order('created_at', { ascending: false });

    if (error) {
        console.error('❌ Error:', error);
        return;
    }

    if (!campaigns || campaigns.length === 0) {
        console.log('✅ All campaigns are already AI enhanced!');
        return;
    }

    const toProcess = limit ? campaigns.slice(0, limit) : campaigns;
    console.log(`📝 Found ${campaigns.length} campaigns to re-process`);

    if (limit) {
        console.log(`🧪 Processing first ${limit} campaigns (test mode)\n`);
    } else {
        console.log(`🚀 Processing all ${campaigns.length} campaigns\n`);
    }

    let success = 0;
    let failed = 0;

    for (const campaign of toProcess) {
        try {
            console.log(`\n🤖 Processing: ${campaign.title}`);
            console.log(`   URL: ${campaign.url}`);

            // Fetch campaign HTML
            const response = await fetch(campaign.url);
            if (!response.ok) {
                throw new Error(`HTTP ${response.status}`);
            }

            const html = await response.text();

            // Parse with AI
            console.log('   🔍 Parsing with Gemini AI...');
            const aiData = await parseWithGemini(html, campaign.url, campaign.bank, campaign.card_name);

            // Assign badge based on AI data
            const badge = assignBadge(aiData);

            // Perform ID Lookup
            const ids = await lookupIDs(
                aiData.bank || campaign.bank,
                aiData.card_name || campaign.card_name,
                aiData.brand,
                aiData.sector_slug,
                aiData.category
            );

            // Merge with existing data
            const updatedCampaign: any = {
                ...aiData,
                ...ids,
                badge_text: badge.text,
                badge_color: badge.color,
                ai_enhanced: true,
                publish_status: 'clean', // Set to clean after successful reprocess
                publish_updated_at: new Date().toISOString()
            };

            // Fix earnings before update
            syncEarningAndDiscount(updatedCampaign);

            // Update in Supabase
            const { error: updateError } = await supabase
                .from('campaigns')
                .update(updatedCampaign)
                .eq('id', campaign.id);

            if (updateError) {
                console.error(`   ❌ Failed to update: ${updateError.message}`);
                failed++;
            } else {
                console.log(`   ✅ Success!`);
                console.log(`      Category: ${aiData.category || 'N/A'}`);
                console.log(`      Badge: ${badge.text}`);
                console.log(`      Valid Until: ${aiData.valid_until || 'N/A'}`);
                success++;
            }

            // Rate limiting
            await sleep(3000); // 3 seconds between requests

        } catch (error: any) {
            console.error(`   ❌ Error: ${error.message}`);
            failed++;
            await sleep(1000);
        }
    }

    console.log('\n' + '='.repeat(80));
    console.log(`\n📊 Results: ${success} success, ${failed} failed\n`);
}

// Get limit from command line args
const args = process.argv.slice(2);
const testMode = args.includes('--test');
const limit = testMode ? 3 : undefined;

reprocessCampaigns(limit).catch(console.error);
