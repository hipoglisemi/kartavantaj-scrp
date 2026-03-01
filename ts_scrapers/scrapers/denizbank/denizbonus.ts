/**
 * @file denizbonus.ts
 * @status 🟢 STABLE / PROTECTED
 * @last_verified 2026-01-04
 * @description This scraper is verified to correctly extract campaign banners and full details.
 * ⚠️ CRITICAL: DO NOT modify the image selection or manual detail extraction logic unless 
 * the Denizbank website structure specifically changes.
 * 
 * Key Protections:
 * 1. IMAGE_BLACKLIST: Prevents the generic 'somestir' fallback image.
 * 2. Banner Priority: Focuses on '.campaign-banner' and excludes '.campaign-card' to avoid thumbnails.
 * 3. Manual Fallbacks: Explicitly captures 'KATILIM KOŞULLARI' and 'NASIL KAZANIRIM' to 
 *    ensure DB fields are never empty even if AI returns partial data.
 */

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

const BASE_URL = 'https://www.denizbonus.com';
const CAMPAIGNS_URL = 'https://www.denizbonus.com/bonus-kampanyalari';
const IMAGE_BLACKLIST = [
    'somestir_kampanya_140423.jpg',
    'popup-logo.png',
    'app-logo.png',
    'denizbank_logo.png'
];

const sleep = (ms: number) => new Promise(resolve => setTimeout(resolve, ms));

async function runDenizBonusScraper() {
    console.log('🚀 Starting Denizbank (DenizBonus) Scraper...');
    const normalizedBank = await normalizeBankName('Denizbank');
    const normalizedCard = await normalizeCardName(normalizedBank, 'DenizBonus');
    console.log(`   Bank: ${normalizedBank}, Card: ${normalizedCard}`);
    const isAIEnabled = process.argv.includes('--ai');

    // Parse limit argument
    const limitArg = process.argv.find(arg => arg.startsWith('--limit='));
    const limit = limitArg ? parseInt(limitArg.split('=')[1]) : 1000;

    try {
        // 1. Fetch Campaign List
        console.log(`   📄 Fetching campaign list from ${CAMPAIGNS_URL}...`);
        const response = await axios.get(CAMPAIGNS_URL, {
            headers: {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
            },
            timeout: 60000
        });

        const $ = cheerio.load(response.data);
        const campaignLinks: string[] = [];

        $('a').each((_, el) => {
            const href = $(el).attr('href');
            // Match both '/kampanyalar/' and 'kampanyalar/' (relative paths)
            if (href && (href.includes('/kampanyalar/') || href.startsWith('kampanyalar/')) && href.split('/').length > 1) {
                // Filter out irrelevant links
                if (!['sektor', 'kategori', 'marka', '#', 'javascript', 'bonus-kampanyalari', 'biten-kampanyalar'].some(x => href.includes(x))) {
                    let fullUrl = href.startsWith('http') ? href : `${BASE_URL}/${href.replace(/^\//, '')}`;

                    // Fix malformed URLs
                    fullUrl = fullUrl.replace('com//', 'com/').replace('com../', 'com/');

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

        // Apply limit
        const limitedLinks = limit > 0 ? campaignLinks.slice(0, limit) : campaignLinks;
        console.log(`   🎯 Processing ${limitedLinks.length} campaigns (limit: ${limit})...`);

        // 2. Optimize
        console.log(`   🔍 Optimizing campaign list via database check...`);
        const { urlsToProcess } = await optimizeCampaigns(limitedLinks, normalizedCard);

        console.log(`   🚀 Processing details for ${urlsToProcess.length} campaigns (skipping ${limitedLinks.length - urlsToProcess.length} complete/existing)...\n`);

        // 3. Process Details
        let processedCount = 0;
        for (const fullUrl of urlsToProcess) {
            console.log(`\n   🔍 Processing [${++processedCount}/${urlsToProcess.length}]: ${fullUrl}`);

            try {
                const detailResponse = await axios.get(fullUrl, {
                    headers: {
                        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
                    },
                    timeout: 40000 // Increased from 20000ms
                });

                const html = detailResponse.data;
                const $detail = cheerio.load(html);

                // Extract basic info for fallback
                const title = $detail('h1').first().text().trim() ||
                    $detail('title').text().replace('- DenizBonus', '').trim() ||
                    'Başlıksız Kampanya';

                // Manual extraction for fallback/basic data
                const howToWin = $detail('.campaign-detail-info h5:contains("NASIL KAZANIRIM")').next('p').text().trim();
                const participationTxt = $detail('.campaign-startend-date h5:contains("KATILMAK İÇİN")').next('p').text().trim();
                const conditionsList: string[] = [];
                $detail('.campaign-detail-text ul li').each((_, li) => {
                    conditionsList.push($detail(li).text().trim());
                });

                // Manual brand extraction for fallback
                let manualBrand: string | null = null;
                const pageText = $detail.text().toLowerCase();
                const titleLower = title.toLowerCase();

                // Common brand patterns in Denizbank campaigns
                const brandPatterns = [
                    { pattern: /migros/i, brand: 'Migros' },
                    { pattern: /carrefour/i, brand: 'Carrefoursa' },
                    { pattern: /a101/i, brand: 'A101' },
                    { pattern: /bim/i, brand: 'BİM' },
                    { pattern: /şok/i, brand: 'ŞOK' },
                    { pattern: /teknosa/i, brand: 'Teknosa' },
                    { pattern: /mediamarkt|media markt/i, brand: 'MediaMarkt' },
                    { pattern: /vatan/i, brand: 'Vatan Bilgisayar' },
                    { pattern: /etstur|ets tur/i, brand: 'ETS Tur' },
                    { pattern: /jolly/i, brand: 'Jolly Tur' },
                    { pattern: /tatilbudur/i, brand: 'Tatilbudur' },
                    { pattern: /hepsiburada/i, brand: 'Hepsiburada' },
                    { pattern: /trendyol/i, brand: 'Trendyol' },
                    { pattern: /n11/i, brand: 'N11' },
                    { pattern: /gittigidiyor/i, brand: 'GittiGidiyor' },
                    { pattern: /zara/i, brand: 'Zara' },
                    { pattern: /h&m|h\u0026m/i, brand: 'H&M' },
                    { pattern: /lcwaikiki|lc waikiki/i, brand: 'LC Waikiki' },
                    { pattern: /defacto/i, brand: 'DeFacto' },
                    { pattern: /koton/i, brand: 'Koton' },
                    { pattern: /mavi/i, brand: 'Mavi' },
                    { pattern: /starbucks/i, brand: 'Starbucks' },
                    { pattern: /mcdonald/i, brand: 'McDonald\'s' },
                    { pattern: /burger king/i, brand: 'Burger King' },
                    { pattern: /domino/i, brand: 'Domino\'s Pizza' },
                    { pattern: /pizza hut/i, brand: 'Pizza Hut' },
                    { pattern: /shell/i, brand: 'Shell' },
                    { pattern: /opet/i, brand: 'Opet' },
                    { pattern: /bp/i, brand: 'BP' },
                    { pattern: /total/i, brand: 'Total' },
                    { pattern: /petrol ofisi/i, brand: 'Petrol Ofisi' }
                ];

                for (const { pattern, brand } of brandPatterns) {
                    if (pattern.test(titleLower) || pattern.test(pageText)) {
                        manualBrand = brand;
                        break;
                    }
                }

                let imageUrl = $detail('.campaign-banner img').first().attr('src') ||
                    $detail('.campaign-image img').first().attr('src') ||
                    $detail('.content img').first().attr('src');

                // If not found in primary areas, look for images that are NOT in suggest/other sections
                if (!imageUrl) {
                    $detail('img').each((_, el) => {
                        const src = $detail(el).attr('src');
                        const isBlacklisted = IMAGE_BLACKLIST.some(b => src?.includes(b));
                        const isOtherCampaign = $detail(el).closest('.campaign-card, #px-other-campaigns, .campaing-card-image').length > 0;

                        if (src && !isBlacklisted && !isOtherCampaign && !imageUrl) {
                            imageUrl = src;
                        }
                    });
                }

                if (imageUrl && !imageUrl.startsWith('http')) {
                    // Normalize relative path: remove leading dots and ensure single leading slash
                    const cleanPath = imageUrl.replace(/^(\.+)*/, '').replace(/^\/+/, '/');
                    imageUrl = `${BASE_URL}${cleanPath}`;

                    // Final cleanup for common malformations
                    imageUrl = imageUrl.replace('com//', 'com/').replace('com../', 'com/');
                }

                // AI Parsing
                let campaignData;
                if (isAIEnabled) {
                    campaignData = await parseWithGemini(html, fullUrl, normalizedBank, normalizedCard);
                } else {
                    campaignData = {
                        title: title,
                        description: howToWin || title,
                        card_name: normalizedCard,
                        url: fullUrl,
                        reference_url: fullUrl,
                        image: imageUrl || '',
                        category: 'Diğer',
                        sector_slug: 'diger',
                        is_active: true,
                        conditions: conditionsList.length > 0 ? conditionsList : null,
                        participation_method: participationTxt || null
                    };
                }

                if (campaignData) {
                    // Force fields
                    campaignData.title = title;
                    campaignData.slug = generateCampaignSlug(title); // Regenerate slug after title override
                    campaignData.card_name = normalizedCard;
                    campaignData.bank = normalizedBank;

                    // MAP FIELDS TO DB SCHEMA
                    campaignData.url = fullUrl;
                    campaignData.reference_url = fullUrl;
                    campaignData.image = imageUrl;

                    // Support manual fallbacks if AI misses them
                    campaignData.description = campaignData.description || howToWin || title;
                    campaignData.conditions = (campaignData.conditions && campaignData.conditions.length > 0) ? campaignData.conditions : (conditionsList.length > 0 ? conditionsList : null);
                    campaignData.participation_method = campaignData.participation_method || participationTxt || null;
                    campaignData.category = campaignData.category || 'Diğer';

                    // Brand fallback - use manual extraction if AI missed it
                    if (!campaignData.brand && manualBrand) {
                        campaignData.brand = manualBrand;
                        console.log(`      🔧 Used fallback brand: ${manualBrand}`);
                    }
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

        console.log(`\n✅ DenizBonus Scraper Finished. Processed ${processedCount} campaigns.`);

    } catch (error: any) {
        console.error(`❌ Global Error: ${error.message}`);
    }
}

runDenizBonusScraper();
