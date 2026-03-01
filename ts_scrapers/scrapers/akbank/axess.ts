
import axios from 'axios';
import * as cheerio from 'cheerio';
import { createClient } from '@supabase/supabase-js';
import * as dotenv from 'dotenv';
import { parseWithGemini } from '../../services/geminiParser';
import { generateCampaignSlug } from '../../utils/slugify';
import { generateSectorSlug } from '../../utils/slugify';
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
    name: 'Axess',
    cardName: 'Axess',
    bankName: 'Akbank',
    baseUrl: 'https://www.axess.com.tr',
    listApiUrl: 'https://www.axess.com.tr/ajax/kampanya-ajax.aspx',
    refererUrl: 'https://www.axess.com.tr/kampanyalar',
    apiParams: { 'checkBox': '[0]', 'searchWord': '""' },
    detailSelector: '.cmsContent.clearfix'
};

const sleep = (ms: number) => new Promise(resolve => setTimeout(resolve, ms));

async function runAxessScraper() {
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
    let allCampaigns: any[] = [];

    // 1. Fetch List from API
    while (allCampaigns.length < limit) {
        try {
            console.log(`   📄 Fetching page ${page}...`);
            const response = await axios.get(CARD_CONFIG.listApiUrl, {
                headers: {
                    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
                    'Referer': CARD_CONFIG.refererUrl,
                    'X-Requested-With': 'XMLHttpRequest'
                },
                params: {
                    ...CARD_CONFIG.apiParams,
                    'page': page.toString()
                }
            });

            const html = response.data;
            if (!html || html.trim() === '') {
                console.log(`   ✅ Page ${page} is empty. Finished.`);
                break;
            }

            const $ = cheerio.load(html);
            const links = $('.campaingBox a.dLink');

            if (links.length === 0) {
                console.log(`   ✅ No more campaigns. Finished.`);
                break;
            }

            let foundNew = false;
            links.each((_: number, el: any) => {
                const href = $(el).attr('href');
                if (href && allCampaigns.length < limit) {
                    const exists = allCampaigns.some((c: any) => c.href === href);
                    if (!exists) {
                        allCampaigns.push({ href });
                        foundNew = true;
                    }
                }
            });

            console.log(`   ✅ Found ${links.length} campaigns on page ${page}. Total so far: ${allCampaigns.length}`);

            if (!foundNew && page > 1) {
                console.log('   ⚠️ No new campaigns. Stopping.');
                break;
            }

            if (allCampaigns.length >= limit) break;

            page++;
            await sleep(1000);
        } catch (error: any) {
            console.error(`   ❌ Error: ${error.message}`);
            break;
        }
    }

    const campaignsToProcess = allCampaigns.slice(0, limit);
    console.log(`\n🎉 Found ${campaignsToProcess.length} campaigns via scraping.`);

    // 2. Optimize
    const allUrls = campaignsToProcess.map(c => new URL(c.href, CARD_CONFIG.baseUrl).toString());

    console.log(`   🔍 Optimizing campaign list via database check...`);
    const { urlsToProcess } = await optimizeCampaigns(allUrls, normalizedCard);

    // Filter original objects based on optimization
    const finalItems = campaignsToProcess.filter(c => {
        const fullUrl = new URL(c.href, CARD_CONFIG.baseUrl).toString();
        return urlsToProcess.includes(fullUrl);
    });

    console.log(`   🚀 Processing details for ${finalItems.length} campaigns (skipping ${campaignsToProcess.length - finalItems.length} complete/existing)...\n`);

    // 2. Process Details
    for (const item of finalItems) {
        const urlPart = item.href;
        if (!urlPart) continue;

        const fullUrl = new URL(urlPart, CARD_CONFIG.baseUrl).toString();
        console.log(`   🔍 Fetching: ${fullUrl.substring(0, 60)}...`);

        try {
            const detailResponse = await axios.get(fullUrl, {
                headers: {
                    'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36'
                }
            });
            const html = detailResponse.data;
            const $ = cheerio.load(html);

            const title = $('h2.pageTitle').text().trim() || 'Başlıksız Kampanya';

            // Image Extraction
            const imagePath = $('.campaingDetailImage img').attr('src');
            const imageUrl = imagePath ? new URL(imagePath, CARD_CONFIG.baseUrl).toString() : null;

            // AI Parsing
            let campaignData;
            if (isAIEnabled) {
                campaignData = await parseWithGemini(html, fullUrl, normalizedBank, normalizedCard);
            } else {
                campaignData = {
                    title: title,
                    description: title,
                    category: 'Diğer',
                    sector_slug: 'diger',
                    card_name: normalizedCard,
                    bank: normalizedBank,
                    url: fullUrl,
                    reference_url: fullUrl,
                    is_active: true,
                    tags: [] // ✅ Smart Tagging: Empty array for non-AI mode
                };
            }

            if (campaignData) {
                // STRICT ASSIGNMENT
                campaignData.title = title;
                campaignData.slug = generateCampaignSlug(title); // Regenerate slug after title override
                campaignData.image = imageUrl; // Add extracted image
                campaignData.card_name = normalizedCard;
                campaignData.bank = normalizedBank;
                campaignData.url = fullUrl;
                campaignData.reference_url = fullUrl;
                campaignData.category = campaignData.category || 'Diğer';
                campaignData.sector_slug = generateSectorSlug(campaignData.category);
                syncEarningAndDiscount(campaignData);
                campaignData.is_active = true;

                // Check for activity if end_date exists
                if (campaignData.end_date) {
                    const today = new Date();
                    today.setHours(0, 0, 0, 0);
                    const endDate = new Date(campaignData.end_date);
                    if (endDate < today) {
                        console.log(`      ⚠️  Expired (${campaignData.end_date}), skipping...`);
                        continue;
                    }
                }

                campaignData.min_spend = campaignData.min_spend || 0;

                // Lookup and assign IDs from master tables
                const ids = await lookupIDs(
                    campaignData.bank,
                    campaignData.card_name,
                    campaignData.brand,
                    campaignData.sector_slug,
                    campaignData.category
                );
                console.log(`      🆔 Debug IDs:`, JSON.stringify(ids));
                Object.assign(campaignData, ids);

                // Assign badge based on campaign content
                const badge = assignBadge(campaignData);
                campaignData.badge_text = badge.text;
                campaignData.badge_color = badge.color;
                // Mark as generic if it's a non-brand-specific campaign
                markGenericBrand(campaignData);

                // ✅ Ensure tags is never null
                campaignData.tags = campaignData.tags || [];

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

runAxessScraper();
