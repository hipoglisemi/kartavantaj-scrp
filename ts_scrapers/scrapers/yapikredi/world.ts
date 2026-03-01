
import axios from 'axios';
import * as cheerio from 'cheerio';
import { createClient } from '@supabase/supabase-js';
import * as dotenv from 'dotenv';
import { parseWithGemini } from '../../services/geminiParser';
import { generateSectorSlug, generateCampaignSlug } from '../../utils/slugify';
import { syncEarningAndDiscount } from '../../utils/dataFixer';
import { normalizeBankName, normalizeCardName } from '../../utils/bankMapper';
import { optimizeCampaigns } from '../../utils/campaignOptimizer';
import { lookupIDs } from '../../utils/idMapper';
import { assignBadge } from '../../services/badgeAssigner';
import { markGenericBrand } from '../../utils/genericDetector';

dotenv.config();

const supabase = createClient(
    process.env.SUPABASE_URL!,
    process.env.SUPABASE_ANON_KEY!
);

const CARD_CONFIG = {
    name: 'World',
    cardName: 'World',
    bankName: 'Yapı Kredi',
    baseUrl: 'https://www.worldcard.com.tr',
    listApiUrl: 'https://www.worldcard.com.tr/api/campaigns?campaignSectorId=6d897e71-1849-43a3-a64f-62840e8c0442&campaignSectorKey=tum-kampanyalar'
};

const sleep = (ms: number) => new Promise(resolve => setTimeout(resolve, ms));

export async function runWorldScraper() {
    const normalizedBank = await normalizeBankName(CARD_CONFIG.bankName);
    const normalizedCard = await normalizeCardName(normalizedBank, CARD_CONFIG.cardName);
    console.log(`\n💳 Starting ${CARD_CONFIG.name} Card Scraper...`);
    console.log(`   Bank: ${normalizedBank}`);
    console.log(`   Card: ${normalizedCard}`);
    console.log(`   Source: ${CARD_CONFIG.baseUrl}\n`);

    const isAIEnabled = process.argv.includes('--ai');
    const limitArg = process.argv.find(arg => arg.startsWith('--limit='));
    const limit = limitArg ? parseInt(limitArg.split('=')[1]) : Infinity;

    let page = 1;
    let allCampaigns = [];

    // 1. Fetch List from API
    while (allCampaigns.length < limit) {
        let retries = 0;
        const maxRetries = 3;

        while (retries < maxRetries) {
            try {
                console.log(`   📄 Fetching page ${page}${retries > 0 ? ` (retry ${retries})` : ''}...`);
                const response = await axios.get(CARD_CONFIG.listApiUrl, {
                    headers: {
                        'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                        'Referer': `${CARD_CONFIG.baseUrl}/kampanyalar`,
                        'Accept': 'application/json, text/plain, */*',
                        'Accept-Language': 'tr-TR,tr;q=0.9,en-US;q=0.8,en;q=0.7',
                        'page': page.toString()
                    },
                    timeout: 30000 // 30 second timeout
                });

                const items = response.data.Items;
                if (!items || items.length === 0) {
                    console.log(`   ✅ Page ${page} is empty. Finished fetching list.`);
                    page = -1; // Flag to stop outer loop
                    break;
                }

                // Filter active campaigns only
                const today = new Date();
                today.setHours(0, 0, 0, 0);
                const activeItems = items.filter((item: any) => {
                    if (!item.EndDate) return true;
                    const endDate = new Date(item.EndDate);
                    return endDate >= today;
                });

                if (activeItems.length === 0 && items.length > 0) {
                    console.log(`   ⚠️  Page ${page} has ${items.length} campaigns but all are expired. Stopping.`);
                    page = -1; // Stop fetching since Yapı Kredi is sorted
                    break;
                }

                allCampaigns.push(...activeItems);
                console.log(`   ✅ Found ${items.length} campaigns (${activeItems.length} active) on page ${page}. Total so far: ${allCampaigns.length}`);

                if (allCampaigns.length >= limit) {
                    page = -1;
                    break;
                }

                page++;
                await sleep(1000);
                break; // Success, exit retry loop
            } catch (error: any) {
                retries++;
                console.error(`   ⚠️  Error fetching page ${page} (attempt ${retries}/${maxRetries}): ${error.message}`);

                if (retries >= maxRetries) {
                    console.error(`   ❌ Failed after ${maxRetries} attempts. Moving to next step.`);
                    page = -1;
                    break;
                }
                const backoffTime = Math.pow(2, retries) * 1000;
                await sleep(backoffTime);
            }
        }
        if (page === -1) break;
    }

    const campaignsToProcessRaw = allCampaigns.slice(0, limit);
    console.log(`\n🎉 Found ${campaignsToProcessRaw.length} campaigns via API.`);

    // 2. Optimize: Check what needs processing
    console.log(`   🔍 Optimizing campaign list via database check...`);
    const allUrls = campaignsToProcessRaw
        .map(item => item.Url ? new URL(item.Url, CARD_CONFIG.baseUrl).toString() : null)
        .filter(url => url !== null) as string[];

    const { urlsToProcess } = await optimizeCampaigns(allUrls, normalizedCard);

    // Filter campaigns based on optimization result
    const campaignMap = new Map(campaignsToProcessRaw.map(c => [new URL(c.Url, CARD_CONFIG.baseUrl).toString(), c]));

    const finalItems = urlsToProcess
        .map(url => campaignMap.get(url))
        .filter(Boolean);

    console.log(`   🚀 Processing details for ${finalItems.length} campaigns (skipping ${campaignsToProcessRaw.length - finalItems.length} complete/existing)...\n`);

    // 3. Process Details
    for (const item of finalItems) {
        const urlPart = item.Url;
        if (!urlPart) continue;

        const fullUrl = new URL(urlPart, CARD_CONFIG.baseUrl).toString();
        // Fix Image URL: remove query params that might break things or be unnecessary
        let imageUrl = item.ImageUrl ? new URL(item.ImageUrl.split('?')[0], CARD_CONFIG.baseUrl).toString() : '';
        const title = item.SpotTitle || item.PageTitle || item.Title || 'Başlıksız Kampanya';

        console.log(`   🔍 Fetching: ${title.substring(0, 50)}...`);

        try {
            const detailResponse = await axios.get(fullUrl, {
                headers: {
                    'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36'
                }
            });
            const html = detailResponse.data;

            // AI Parsing
            let campaignData;
            if (isAIEnabled) {
                // @ts-ignore
                campaignData = await parseWithGemini(html, fullUrl, normalizedBank, CARD_CONFIG.cardName);
            } else {
                campaignData = {
                    title: title,
                    description: title,
                    category: 'Diğer',
                    sector_slug: 'diger',
                    card_name: CARD_CONFIG.cardName,
                    bank: normalizedBank,
                    url: fullUrl,
                    reference_url: fullUrl,
                    image: imageUrl,
                    is_active: true,
                    tags: []
                };
            }

            if (campaignData) {
                // 1.8 Marketing Text Enhancement (NEW)
                if (isAIEnabled) {
                    console.log(`      🤖 AI Marketing: Generating catchy summary...`);
                    // @ts-ignore
                    const { enhanceDescription } = await import('../../services/descriptionEnhancer');
                    campaignData.ai_marketing_text = await enhanceDescription(campaignData.title);
                }

                // STRICT ASSIGNMENT - Prevent AI misclassification
                campaignData.title = title;
                campaignData.slug = generateCampaignSlug(title); // Regenerate slug after title override // Use API title as authority
                campaignData.card_name = normalizedCard;
                campaignData.bank = normalizedBank;
                campaignData.url = fullUrl;
                campaignData.reference_url = fullUrl;
                if (imageUrl) campaignData.image = imageUrl; // Only override if API provided image

                campaignData.category = campaignData.category || 'Diğer';
                campaignData.sector_slug = generateSectorSlug(campaignData.category);
                syncEarningAndDiscount(campaignData);
                campaignData.publish_status = 'processing';
                campaignData.publish_updated_at = new Date().toISOString();
                campaignData.is_active = true;

                // Set default min_spend
                campaignData.min_spend = campaignData.min_spend || 0;

                // Lookup and assign IDs from master tables
                const ids = await lookupIDs(
                    campaignData.bank,
                    campaignData.card_name,
                    campaignData.brand,
                    campaignData.sector_slug
                );

                if (ids) {
                    console.log(`      🆔 Debug IDs: ${JSON.stringify(ids)}`);
                    Object.assign(campaignData, ids);
                    // TEMP FIX: Force English slug for testing
                    if (campaignData.bank_id === 'yapı-kredi') {
                        campaignData.bank_id = 'yapi-kredi';
                        console.log('      🔧 Override bank_id to yapi-kredi');
                    }
                }

                // Assign badge based on campaign content
                const badge = assignBadge(campaignData);
                campaignData.badge_text = badge.text;
                campaignData.badge_color = badge.color;

                // Mark as generic if it's a non-brand-specific campaign
                const isGeneric = markGenericBrand(campaignData);

                campaignData.tags = campaignData.tags || [];

                if (isGeneric) {
                    console.log(`      🏷️  Generic campaign detected: "${campaignData.title}"`);
                }

                // Upsert to Supabase
                // ID-BASED SLUG SYSTEM
                const { data: existing } = await supabase
                    .from('campaigns')
                    .select('id')
                    .eq('reference_url', fullUrl)
                    .single();

                if (existing) {
                    const finalSlug = generateCampaignSlug(title, existing.id);
                    const { error } = await supabase
                        .from('campaigns')
                        .update({ ...campaignData, slug: finalSlug })
                        .eq('id', existing.id);
                    if (error) {
                        console.error(`      ❌ Update Error: ${error.message}`);
                    } else {
                        console.log(`      ✅ Updated: ${title.substring(0, 30)}... (${finalSlug})`);
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
                        const finalSlug = generateCampaignSlug(title, inserted.id);
                        await supabase
                            .from('campaigns')
                            .update({ slug: finalSlug })
                            .eq('id', inserted.id);
                        console.log(`      ✅ Inserted: ${title.substring(0, 30)}... (${finalSlug})`);
                    }
                }
            }
        } catch (error: any) {
            console.error(`      ❌ Error: ${error.message}`);
        }

        await sleep(1500);
    }

    console.log(`\n✅ ${CARD_CONFIG.name} scraper completed!`);
}

runWorldScraper();
