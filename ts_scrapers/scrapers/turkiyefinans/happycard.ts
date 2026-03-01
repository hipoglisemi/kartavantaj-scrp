/**
 * @file happycard.ts
 * @status 🟢 ACTIVE
 * @last_verified 2026-01-19
 * @description Scraper for Türkiye Finans Happy Card campaigns
 */

import axios from 'axios';
import * as cheerio from 'cheerio';
import { createClient } from '@supabase/supabase-js';
import * as dotenv from 'dotenv';
import https from 'https';
import { parseWithGemini } from '../../services/geminiParser';
import { generateSectorSlug, generateCampaignSlug } from '../../utils/slugify';
import { syncEarningAndDiscount } from '../../utils/dataFixer';
import { normalizeBankName, normalizeCardName } from '../../utils/bankMapper';
import { optimizeCampaigns } from '../../utils/campaignOptimizer';
import { lookupIDs } from '../../utils/idMapper';
import { assignBadge } from '../../services/badgeAssigner';
import { markGenericBrand } from '../../utils/genericDetector';

dotenv.config();

// HTTPS agent to bypass SSL certificate verification
const httpsAgent = new https.Agent({
    rejectUnauthorized: false
});

const supabase = createClient(
    process.env.SUPABASE_URL!,
    process.env.SUPABASE_ANON_KEY!
);

const BASE_URL = 'https://www.happycard.com.tr';
const CAMPAIGNS_URL = 'https://www.happycard.com.tr/kampanyalar/Sayfalar/default.aspx';

const sleep = (ms: number) => new Promise(resolve => setTimeout(resolve, ms));

async function runHappyCardScraper() {
    console.log('🚀 Starting Türkiye Finans Happy Card Scraper...');
    const normalizedBank = await normalizeBankName('Türkiye Finans');
    const normalizedCard = await normalizeCardName(normalizedBank, 'Happy Card');
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
            timeout: 60000,
            httpsAgent
        });

        const $ = cheerio.load(response.data);
        const campaignLinks: string[] = [];

        // Extract campaign links - Happy Card uses specific link patterns
        $('a').each((_, el) => {
            const href = $(el).attr('href');
            // Match campaign detail pages
            if (href && href.includes('/kampanyalar/Sayfalar/') && !href.includes('default.aspx')) {
                let fullUrl = href.startsWith('http') ? href : `${BASE_URL}${href.startsWith('/') ? '' : '/'}${href}`;

                // Normalize URL
                try {
                    fullUrl = new URL(fullUrl).href;
                    if (!campaignLinks.includes(fullUrl)) {
                        campaignLinks.push(fullUrl);
                    }
                } catch (e) {
                    // invalid url, skip
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
                    timeout: 40000,
                    httpsAgent
                });

                const html = detailResponse.data;
                const $detail = cheerio.load(html);

                // Extract basic info for fallback
                const title = $detail('h1').first().text().trim() ||
                    $detail('title').text().replace('- Happy Card', '').replace('Türkiye Finans Happy Kredi Kartları Kampanyalar', '').trim() ||
                    'Başlıksız Kampanya';

                // Extract campaign image
                let imageUrl = $detail('.campaign-image img').first().attr('src') ||
                    $detail('.ms-rteImage-4').first().attr('src') ||
                    $detail('img[src*="kampanya"]').first().attr('src') ||
                    $detail('.ms-rtestate-field img').first().attr('src');

                if (imageUrl && !imageUrl.startsWith('http')) {
                    imageUrl = `${BASE_URL}${imageUrl.startsWith('/') ? '' : '/'}${imageUrl}`;
                }

                // Extract description and conditions
                const description = $detail('.campaign-description').text().trim() ||
                    $detail('.ms-rtestate-field').first().text().trim() ||
                    title;

                const conditionsList: string[] = [];
                $detail('ul li').each((_, li) => {
                    const text = $detail(li).text().trim();
                    if (text && text.length > 10) {
                        conditionsList.push(text);
                    }
                });

                // Extract dates if available
                const dateText = $detail('.campaign-date, .date-info').text().trim();

                // AI Parsing
                let campaignData;
                if (isAIEnabled) {
                    campaignData = await parseWithGemini(html, fullUrl, normalizedBank, normalizedCard);
                } else {
                    campaignData = {
                        title: title,
                        description: description || title,
                        card_name: normalizedCard,
                        url: fullUrl,
                        reference_url: fullUrl,
                        image: imageUrl || '',
                        category: 'Diğer',
                        sector_slug: 'diger',
                        is_active: true,
                        conditions: conditionsList.length > 0 ? conditionsList : null,
                    };
                }

                if (campaignData) {
                    // Force fields
                    campaignData.title = title;
                    campaignData.slug = generateCampaignSlug(title);
                    campaignData.card_name = normalizedCard;
                    campaignData.bank = normalizedBank;

                    // MAP FIELDS TO DB SCHEMA
                    campaignData.url = fullUrl;
                    campaignData.reference_url = fullUrl;
                    campaignData.image = imageUrl;

                    // Support manual fallbacks if AI misses them
                    campaignData.description = campaignData.description || description || title;
                    campaignData.conditions = (campaignData.conditions && campaignData.conditions.length > 0) ? campaignData.conditions : (conditionsList.length > 0 ? conditionsList : null);
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

        console.log(`\n✅ Happy Card Scraper Finished. Processed ${processedCount} campaigns.`);

    } catch (error: any) {
        console.error(`❌ Global Error: ${error.message}`);
    }
}

runHappyCardScraper();
