import axios from 'axios';
import * as cheerio from 'cheerio';
import { createClient } from '@supabase/supabase-js';
import * as dotenv from 'dotenv';
import { parseWithGemini } from '../../services/geminiParser';
import { generateSectorSlug, generateCampaignSlug } from '../../utils/slugify';
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
    name: 'Maximum',
    cardName: 'Maximum',
    bankName: 'İş Bankası',
    baseUrl: 'https://www.maximum.com.tr',
    listUrl: 'https://www.maximum.com.tr/kampanyalar'
};

const sleep = (ms: number) => new Promise(resolve => setTimeout(resolve, ms));

async function runMaximumScraper() {
    const normalizedBank = await normalizeBankName(CARD_CONFIG.bankName);
    const normalizedCard = await normalizeCardName(normalizedBank, CARD_CONFIG.cardName);
    console.log(`\n💳 Starting ${CARD_CONFIG.name} Card Scraper (V3 - Axios)...`);
    console.log(`   Bank: ${normalizedBank}`);
    console.log(`   Card: ${normalizedCard}`);
    console.log(`   Source: ${CARD_CONFIG.baseUrl}\n`);

    const isAIEnabled = process.argv.includes('--ai') || true; // Always use AI for Maximum
    const limitArg = process.argv.find(arg => arg.startsWith('--limit='));
    const limit = limitArg ? parseInt(limitArg.split('=')[1]) : Infinity;

    let allCampaigns: any[] = [];

    // 1. Fetch List Page
    try {
        console.log(`   📄 Fetching campaign list...`);
        const response = await axios.get(CARD_CONFIG.listUrl, {
            headers: {
                'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36',
                'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8'
            }
        });

        const $ = cheerio.load(response.data);

        // Extract campaign links (exclude category/menu pages)
        $('a[href*="/kampanyalar/"]').each((_: number, el: any) => {
            const href = $(el).attr('href');
            if (!href || href.includes('arsiv')) return;

            const fullUrl = href.startsWith('http') ? href : new URL(href, CARD_CONFIG.baseUrl).toString();

            // 🔥 FILTER: Exclude category/menu pages
            const urlPath = fullUrl.replace(CARD_CONFIG.baseUrl, '');

            // Skip if URL is too short (likely a category)
            if (urlPath.length < 30) return;

            // Skip common category patterns
            const categoryPatterns = [
                '/kampanyalar/bireysel',
                '/kampanyalar/ticari',
                '/kampanyalar/sektor',
                '/kampanyalar/kategori',
                'kampanyalar$', // Exact match to /kampanyalar
                'kampanyalar/$'  // Exact match to /kampanyalar/
            ];

            if (categoryPatterns.some(pattern => new RegExp(pattern).test(urlPath))) {
                return;
            }

            // Get title from link
            const title = $(el).find('.card-title, h5, h4, .title').text().trim() ||
                $(el).text().trim();

            // Skip if title contains "Kampanyaları" (plural, indicates category)
            if (title.toLowerCase().includes('kampanyaları')) return;

            // Get image from link
            const imgEl = $(el).find('img');
            let image = '';
            if (imgEl.length > 0) {
                image = imgEl.attr('src') || imgEl.attr('data-src') || '';
                if (image && !image.startsWith('http')) {
                    image = new URL(image, CARD_CONFIG.baseUrl).toString();
                }
            }

            const exists = allCampaigns.some((c: any) => c.url === fullUrl);
            if (!exists && title) {
                allCampaigns.push({ url: fullUrl, title, image });
            }
        });

        console.log(`   ✅ Found ${allCampaigns.length} campaigns\n`);

    } catch (error: any) {
        console.error(`   ❌ Error fetching list: ${error.message}`);
        return;
    }

    const campaignsToProcess = allCampaigns.slice(0, limit);
    console.log(`🎉 Processing ${campaignsToProcess.length} campaigns...\n`);

    // 2. Optimize
    const allUrls = campaignsToProcess.map(c => c.url);
    console.log(`   🔍 Optimizing campaign list via database check...`);
    const { urlsToProcess } = await optimizeCampaigns(allUrls, normalizedCard);

    const finalItems = campaignsToProcess.filter(c => urlsToProcess.includes(c.url));
    console.log(`   🚀 Processing details for ${finalItems.length} campaigns (skipping ${campaignsToProcess.length - finalItems.length} complete/existing)...\n`);

    // 3. Process Details
    for (const item of finalItems) {
        console.log(`   🔍 Fetching: ${item.title.substring(0, 50)}...`);

        try {
            const detailResponse = await axios.get(item.url, {
                headers: {
                    'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36'
                }
            });
            const html = detailResponse.data;
            const $ = cheerio.load(html);

            // Extract details
            const title = $('h1.gradient-title-text, h1').first().text().trim() || item.title;

            // Date
            const dateText = $('span[id$="KampanyaTarihleri"]').text().trim();

            // Description & Conditions
            const descEl = $('span[id$="CampaignDescription"]');
            let rawText = descEl.text().trim();
            let conditions: string[] = [];
            if (rawText) {
                conditions = rawText.split('\n').map(l => l.trim()).filter(l => l.length > 20);
            }

            // Image (prefer from list page, fallback to detail page)
            let finalImage = item.image;
            if (!finalImage || finalImage.includes('favicon')) {
                const detailImg = $('img[id$="CampaignImage"]').attr('src');
                if (detailImg) {
                    finalImage = detailImg.startsWith('http') ? detailImg : new URL(detailImg, CARD_CONFIG.baseUrl).toString();
                }
            }

            console.log(`      🖼️  Image: ${finalImage ? '✅' : '❌'}`);
            console.log(`      📅 Date: ${dateText}`);

            // AI Parsing
            const combinedText = `${title}\n${rawText}`;
            let campaignData: any = {};

            if (isAIEnabled) {
                try {
                    console.log(`      🧠 AI analizi...`);
                    campaignData = await parseWithGemini(html, item.url, normalizedBank, normalizedCard);
                } catch (err: any) {
                    console.error(`      ⚠️  AI Error: ${err.message}`);
                    campaignData = {
                        title,
                        description: title,
                        category: 'Diğer'
                    };
                }
            }

            // Prepare final data
            campaignData.title = title;
            campaignData.slug = generateCampaignSlug(title); // Regenerate slug
            campaignData.image = finalImage;
            campaignData.card_name = normalizedCard;
            campaignData.bank = normalizedBank;
            campaignData.url = item.url;
            campaignData.reference_url = item.url;
            campaignData.category = campaignData.category || 'Diğer';
            campaignData.sector_slug = generateSectorSlug(campaignData.category);
            campaignData.conditions = conditions;
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

            // Save to database
            
            // ID-BASED SLUG SYSTEM
            const { data: existing } = await supabase
                .from('campaigns')
                .select('id')
                .eq('reference_url', url)
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
                    console.log(`      ✅ Updated: ${title} (${finalSlug})`);
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
                    console.log(`      ✅ Inserted: ${title} (${finalSlug})`);
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

runMaximumScraper();
