
import * as fs from 'fs';
import * as path from 'path';
import * as dotenv from 'dotenv';
import { createClient } from '@supabase/supabase-js';
import { parseWithGemini } from '../services/geminiParser';
import { lookupIDs } from '../utils/idMapper';
import { syncEarningAndDiscount } from '../utils/dataFixer';
import { assignBadge } from '../services/badgeAssigner';
import { markGenericBrand } from '../utils/genericDetector';
import { optimizeCampaigns } from '../utils/campaignOptimizer';

dotenv.config();

const supabase = createClient(
    process.env.SUPABASE_URL!,
    process.env.SUPABASE_ANON_KEY!
);

const sleep = (ms: number) => new Promise(resolve => setTimeout(resolve, ms));

async function main() {
    const args = process.argv.slice(2);
    const fileArg = args.find(arg => arg.endsWith('.json') || !arg.startsWith('--'));
    const limitArg = args.find(arg => arg.startsWith('--limit='));

    if (!fileArg) {
        console.error("❌ Usage: npx tsx src/scripts/process_raw_json.ts <file.json> [--limit=N]");
        process.exit(1);
    }

    const filePath = path.resolve(process.cwd(), fileArg);
    if (!fs.existsSync(filePath)) {
        console.error(`❌ File not found: ${filePath}`);
        process.exit(1);
    }

    console.log(`📂 Reading ${path.basename(filePath)}...`);
    const rawData = JSON.parse(fs.readFileSync(filePath, 'utf-8'));

    if (!Array.isArray(rawData) || rawData.length === 0) {
        console.log("⚠️ JSON is empty or not an array. Exiting.");
        return;
    }

    // Assumptions: rawData items have { url, bank, card, title, detail_html (or description/html) }
    const sample = rawData[0];
    const bankName = sample.bank || sample.provider || 'Unknown Bank';
    const cardName = sample.card || sample.card_name || 'Unknown Card';

    console.log(`💳 Bank: ${bankName}, Card: ${cardName}`);

    // 1. Optimize: Filter out existing/complete campaigns
    const allUrls = rawData.map((d: any) => d.url).filter(u => !!u);
    const { urlsToProcess } = await optimizeCampaigns(allUrls, cardName);

    // Limits
    let limit = limitArg ? parseInt(limitArg.split('=')[1]) : 9999;

    // Filter rawData to keep only those in urlsToProcess
    const toProcess = rawData.filter((d: any) => urlsToProcess.includes(d.url)).slice(0, limit);

    if (toProcess.length === 0) {
        console.log("✅ All campaigns are already up-to-date. No action needed.");
        return;
    }

    console.log(`🚀 Starting AI processing for ${toProcess.length} campaigns...`);

    for (const [index, item] of toProcess.entries()) {
        const { url, title, detail_html, image } = item;
        console.log(`\n[${index + 1}/${toProcess.length}] Processing: ${title}`);

        try {
            // AI Parsing
            // Combine title + description/html for best context
            const contentToParse = `${title}\n${detail_html || item.description || ''}`;

            const aiResult = await parseWithGemini(contentToParse, url, bankName, cardName);

            // Merge AI result with existing basic info (priority to AI, but keep raw URL/Image if AI missed it)
            const campaignData = {
                ...aiResult,
                url: url, // Ensure URL is correct
                reference_url: url,
                image: aiResult.image || image, // Use AI image if found (rare), else scraper image
                is_active: true,
                publish_status: 'processing'
            };

            // Post-Processing
            campaignData.bank = bankName;
            campaignData.card_name = cardName;

            syncEarningAndDiscount(campaignData);

            if (campaignData.min_spend === undefined || campaignData.min_spend === null) {
                campaignData.min_spend = 0;
            }

            // ID Lookup
            const ids = await lookupIDs(
                campaignData.bank,
                campaignData.card_name,
                Array.isArray(campaignData.brand) ? campaignData.brand.join(',') : campaignData.brand,
                campaignData.sector_slug
            );
            Object.assign(campaignData, ids);

            // Badge
            const badge = assignBadge(campaignData);
            campaignData.badge_text = badge.text;
            campaignData.badge_color = badge.color;

            // Generic Brand Check
            markGenericBrand(campaignData);

            // Upsert
            const { error } = await supabase
                .from('campaigns')
                .upsert(campaignData, { onConflict: 'reference_url' });

            if (error) {
                console.error(`   ❌ DB Error: ${error.message}`);
            } else {
                console.log(`   ✅ Saved: ${campaignData.title}`);
                if (campaignData.ai_enhanced) {
                    console.log(`      ✨ AI Enhanced (Tokens: ${campaignData.ai_tokens || '?'})`);
                }
            }

            // Rate Limit for Gemini (though service handles it, being safe here)
            await sleep(1000);

        } catch (err: any) {
            console.error(`   ❌ Error processing item: ${err.message}`);
        }
    }

    console.log("\n🏁 Import completed.");
}

main().catch(console.error);
