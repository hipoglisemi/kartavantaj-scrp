import { createClient } from '@supabase/supabase-js';
import * as dotenv from 'dotenv';
import * as fs from 'fs';
import { parseWithGemini } from '../../services/geminiParser';
import { generateSectorSlug, generateCampaignSlug } from '../../utils/slugify';
import { normalizeBankName, normalizeCardName } from '../../utils/bankMapper';
import { optimizeCampaigns } from '../../utils/campaignOptimizer';
import { lookupIDs } from '../../utils/idMapper';
import { assignBadge } from '../../services/badgeAssigner';
import { markGenericBrand } from '../../utils/genericDetector';
import { downloadImageDirectly } from '../../services/imageService';

dotenv.config();

const supabase = createClient(
    process.env.SUPABASE_URL!,
    process.env.SUPABASE_ANON_KEY!
);

const CARD_CONFIG = {
    cardName: 'Maximum',
    bankName: 'İş Bankası'
};

const sleep = (ms: number) => new Promise(resolve => setTimeout(resolve, ms));

async function importMaximumCampaigns() {
    const normalizedBank = await normalizeBankName(CARD_CONFIG.bankName);
    const normalizedCard = await normalizeCardName(normalizedBank, CARD_CONFIG.cardName);

    console.log(`\n💳 Maximum Import (TypeScript)...`);
    console.log(`   Bank: ${normalizedBank}`);
    console.log(`   Card: ${normalizedCard}\n`);

    // Read Python output
    const jsonPath = 'maximum_campaigns_full.json';
    if (!fs.existsSync(jsonPath)) {
        console.error('❌ maximum_campaigns_full.json not found!');
        return;
    }

    const campaigns: any[] = JSON.parse(fs.readFileSync(jsonPath, 'utf8'));
    console.log(`📋 Loaded ${campaigns.length} campaigns from Python\n`);

    // Optimize
    const urls = campaigns.map(c => c.url);
    const { urlsToProcess } = await optimizeCampaigns(urls, normalizedCard);
    console.log(`   🚀 Processing ${urlsToProcess.length} campaigns\n`);

    // Create URL map
    const campaignMap = new Map(campaigns.map(c => [c.url, c]));

    // Process each
    for (const url of urlsToProcess) {
        const pythonData = campaignMap.get(url);
        if (!pythonData) continue;

        console.log(`   🔍 ${pythonData.title.substring(0, 40)}...`);

        try {
            // AI Processing
            let campaignData: any = {};
            try {
                console.log(`      🧠 AI processing...`);
                campaignData = await parseWithGemini(
                    pythonData.raw_html || pythonData.description,
                    url,
                    normalizedBank,
                    normalizedCard
                );
            } catch (err: any) {
                console.error(`      ⚠️  AI Error: ${err.message}`);
                campaignData = {
                    title: pythonData.title,
                    description: pythonData.description,
                    category: 'Diğer'
                };
            }

            // Merge Python + AI data
            campaignData.title = pythonData.title;
            campaignData.slug = generateCampaignSlug(pythonData.title); // Regenerate slug

            // DOWNLOAD IMAGE IF EXISTS
            let finalImage = pythonData.image || campaignData.image;
            if (finalImage && finalImage.startsWith('http')) {
                finalImage = await downloadImageDirectly(finalImage, campaignData.title, 'maximum');
            }
            campaignData.image = finalImage;

            campaignData.card_name = normalizedCard;
            campaignData.bank = normalizedBank;
            campaignData.url = url;
            campaignData.reference_url = url;
            campaignData.category = campaignData.category || 'Diğer';
            campaignData.sector_slug = generateSectorSlug(campaignData.category);
            campaignData.is_active = true;
            campaignData.min_spend = campaignData.min_spend || 0;

            // Lookup IDs
            const ids = await lookupIDs(
                campaignData.bank,
                campaignData.card_name,
                campaignData.brand,
                campaignData.sector_slug
            );
            Object.assign(campaignData, ids);

            // Assign badge
            const badge = assignBadge(campaignData);
            campaignData.badge_text = badge.text;
            campaignData.badge_color = badge.color;
            markGenericBrand(campaignData);

            // Save

            // ID-BASED SLUG SYSTEM
            const { data: existing } = await supabase
                .from('campaigns')
                .select('id')
                .eq('reference_url', url)
                .single();

            if (existing) {
                const finalSlug = generateCampaignSlug(campaignData.title, existing.id);
                const { error } = await supabase
                    .from('campaigns')
                    .update({ ...campaignData, slug: finalSlug })
                    .eq('id', existing.id);
                if (error) {
                    console.error(`      ❌ Update Error: ${error.message}`);
                } else {
                    console.log(`      ✅ Updated: ${campaignData.title} (${finalSlug})`);
                }
            } else {
                const { data: inserted, error: insertError } = await supabase
                    .from('campaigns')
                    .insert(campaignData)
                    .select('id')
                    .single();
                if (insertError) {
                    console.error(`      ❌ Insert Error: ${insertError.message}`);
                } else if (inserted) {
                    const finalSlug = generateCampaignSlug(campaignData.title, inserted.id);
                    await supabase
                        .from('campaigns')
                        .update({ slug: finalSlug })
                        .eq('id', inserted.id);
                    console.log(`      ✅ Inserted: ${campaignData.title} (${finalSlug})`);
                }
            }


        } catch (error: any) {
            console.error(`      ❌ Error: ${error.message}`);
        }

        await sleep(1500);
    }

    console.log(`\n✅ Maximum import completed!`);
}

importMaximumCampaigns();
