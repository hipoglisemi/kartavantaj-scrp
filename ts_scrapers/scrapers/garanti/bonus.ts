
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

const BASE_URL = 'https://www.bonus.com.tr';
const CAMPAIGNS_URL = 'https://www.bonus.com.tr/kampanyalar';

const sleep = (ms: number) => new Promise(resolve => setTimeout(resolve, ms));

async function runGarantiScraper() {
    console.log('🚀 Starting Garanti BBVA (Bonus) Scraper...');
    const normalizedBank = await normalizeBankName('Garanti BBVA');
    const normalizedCard = await normalizeCardName(normalizedBank, 'Bonus');
    console.log(`   Bank: ${normalizedBank}, Card: ${normalizedCard}`);
    const isAIEnabled = process.argv.includes('--ai');

    try {
        // 1. Fetch Campaign List
        console.log(`   📄 Fetching campaign list from ${CAMPAIGNS_URL}...`);
        const response = await axios.get(CAMPAIGNS_URL, {
            headers: {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
            },
            timeout: 30000
        });

        const $ = cheerio.load(response.data);
        const campaignLinks: string[] = [];

        $('a').each((_, el) => {
            const href = $(el).attr('href');
            if (href && href.includes('/kampanyalar/') && href.split('/').length > 2) {
                // Filter out irrelevant links
                if (!['sektor', 'kategori', 'marka', '#', 'javascript'].some(x => href.includes(x))) {
                    let fullUrl = href.startsWith('http') ? href : `${BASE_URL}${href}`;

                    // Fix malformed URLs (e.g., https://www.bonus.com.tr../kampanyalar/...)
                    fullUrl = fullUrl.replace('com.tr//', 'com.tr/').replace('com.tr../', 'com.tr/');

                    // Normalize
                    try {
                        fullUrl = new URL(fullUrl).href;
                        if (!campaignLinks.includes(fullUrl)) {
                            campaignLinks.push(fullUrl);
                        }
                    } catch (e) {
                        // invalid url, skip
                    }
                }
            }
        });

        console.log(`\n   🎉 Found ${campaignLinks.length} campaigns via scraping.`);

        // 2. Optimize
        console.log(`   🔍 Optimizing campaign list via database check...`);
        const { urlsToProcess } = await optimizeCampaigns(campaignLinks, normalizedCard);

        console.log(`   🚀 Processing details for ${urlsToProcess.length} campaigns (skipping ${campaignLinks.length - urlsToProcess.length} complete/existing)...\n`);

        // 3. Process Details
        for (const fullUrl of urlsToProcess) {
            console.log(`\n   🔍 Processing: ${fullUrl}`);

            try {
                const detailResponse = await axios.get(fullUrl, {
                    headers: {
                        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
                    },
                    timeout: 20000
                });

                const html = detailResponse.data;
                const $detail = cheerio.load(html);

                // Extract basic info for fallback
                const title = $detail('.campaign-detail-title h1').text().trim() ||
                    $detail('h1').first().text().trim() ||
                    $detail('title').text().replace('- Bonus', '').trim() ||
                    'Başlıksız Kampanya';
                let imageUrl = $detail('.campaign-detail__image img').attr('src');

                if (imageUrl && !imageUrl.startsWith('http')) {
                    imageUrl = `${BASE_URL}${imageUrl}`;
                }

                // AI Parsing
                let campaignData;
                if (isAIEnabled) {
                    campaignData = await parseWithGemini(html, fullUrl, normalizedBank, normalizedCard);
                } else {
                    campaignData = {
                        title: title,
                        description: title,
                        card_name: normalizedCard,
                        url: fullUrl,           // Mapped
                        reference_url: fullUrl, // Mapped
                        image: imageUrl || '',  // Mapped
                        category: 'Diğer',
                        sector_slug: 'diger',
                        is_active: true,
                        tags: []
                    };
                }

                if (campaignData) {
                    // Force fields
                    campaignData.title = title;
                    campaignData.slug = generateCampaignSlug(title); // Regenerate slug after title override // Strict Assignment
                    campaignData.card_name = normalizedCard; // Default to Bonus
                    campaignData.bank = normalizedBank; // Enforce strict bank assignment

                    // MAP FIELDS TO DB SCHEMA (SCRAPER_SCHEMA_GUIDE.md)
                    campaignData.url = fullUrl;           // Mapping reference_url -> url
                    campaignData.reference_url = fullUrl; // Keeping for upsert constraint
                    campaignData.image = imageUrl;        // Mapping image_url -> image
                    // campaignData.image_url = imageUrl; // Removing old field

                    if (!campaignData.image && imageUrl) {
                        campaignData.image = imageUrl;
                    }
                    campaignData.category = campaignData.category || 'Diğer';
                    campaignData.sector_slug = generateSectorSlug(campaignData.category);
                    syncEarningAndDiscount(campaignData);
                    campaignData.publish_status = 'processing';
                    campaignData.publish_updated_at = new Date().toISOString();
                    campaignData.is_active = true;

                    // Filter out expired campaigns if end_date exists
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
                    Object.assign(campaignData, ids);
                    // Assign badge based on campaign content
                    const badge = assignBadge(campaignData);
                    campaignData.badge_text = badge.text;
                    campaignData.badge_color = badge.color;
                    // Mark as generic if it's a non-brand-specific campaign
                    markGenericBrand(campaignData);

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

            } catch (err: any) {
                console.error(`      ❌ Error processing detail ${fullUrl}: ${err.message}`);
            }

            await sleep(1500); // Polite delay
        }

    } catch (error: any) {
        console.error(`❌ Global Error: ${error.message}`);
    }
}

runGarantiScraper();
