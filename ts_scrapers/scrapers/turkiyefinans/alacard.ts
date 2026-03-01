/**
 * @file alacard.ts
 * @status 🟢 ACTIVE
 * @last_verified 2026-01-19
 * @description Scraper for Türkiye Finans ALA Card campaigns - processes all campaigns from extracted URLs
 */

import axios from 'axios';
import * as cheerio from 'cheerio';
import { createClient } from '@supabase/supabase-js';
import * as dotenv from 'dotenv';
import https from 'https';
import * as fs from 'fs';
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

const BASE_URL = 'https://www.turkiyefinansala.com';
const CAMPAIGN_URLS_FILE = '/tmp/ala_card_campaign_links.txt';

const sleep = (ms: number) => new Promise(resolve => setTimeout(resolve, ms));

async function runAlaCardScraper() {
    console.log('🚀 Starting Türkiye Finans ALA Card Scraper...');
    const normalizedBank = await normalizeBankName('Türkiye Finans');
    const normalizedCard = await normalizeCardName(normalizedBank, 'Ala Card');
    console.log(`   Bank: ${normalizedBank}, Card: ${normalizedCard}`);
    const isAIEnabled = process.argv.includes('--ai');

    // Parse limit argument
    const limitArg = process.argv.find(arg => arg.startsWith('--limit='));
    const limit = limitArg ? parseInt(limitArg.split('=')[1]) : 1000;

    try {
        // 1. Load Campaign URLs from file
        console.log(`   📄 Loading campaign URLs from ${CAMPAIGN_URLS_FILE}...`);
        if (!fs.existsSync(CAMPAIGN_URLS_FILE)) {
            console.error(`❌ Campaign URLs file not found: ${CAMPAIGN_URLS_FILE}`);
            return;
        }

        const campaignLinks = fs.readFileSync(CAMPAIGN_URLS_FILE, 'utf-8')
            .split('\n')
            .map(line => line.trim())
            .filter(line => line.length > 0);

        console.log(`\n   🎉 Loaded ${campaignLinks.length} campaigns from file.`);

        // Apply limit
        const limitedLinks = limit > 0 ? campaignLinks.slice(0, limit) : campaignLinks;
        console.log(`   🎯 Processing ${limitedLinks.length} campaigns (limit: ${limit})...`);

        // 2. Optimize (Check existing)
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

                // Extract title
                // Try h2 first (similar to Happy Card), then title tag key
                let title = $detail('.campaign-detail h2').first().text().trim();

                if (!title) {
                    title = $detail('h1').first().text().trim();
                }

                // If empty or generic, try title tag
                if (!title || title === 'Kampanyalar') {
                    const titleTag = $detail('title').text().trim();
                    title = titleTag
                        .replace('- Âlâ Kart', '')
                        .replace('Türkiye Finans Âlâ Kart Kampanyalar', '')
                        .replace('Türkiye Finans Âlâ Kart', '')
                        .replace(/\s+-\s+Âlâ Kart$/i, '')
                        .trim();
                }

                if (!title || title === 'Kampanyalar') {
                    title = 'Başlıksız Kampanya';
                }

                console.log(`      📝 Başlık: ${title}`);

                // Extract image
                let imageUrl = $detail('.campaign-image img').first().attr('src') ||
                    $detail('.ms-rteImage-4').first().attr('src') ||
                    $detail('img[src*="kampanya"]').first().attr('src') ||
                    $detail('.ms-rtestate-field img').first().attr('src');

                if (imageUrl && !imageUrl.startsWith('http')) {
                    imageUrl = `${BASE_URL}${imageUrl.startsWith('/') ? '' : '/'}${imageUrl}`;
                }

                // Extract description conditions
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
                    // Force fields override
                    campaignData.title = title;
                    campaignData.slug = generateCampaignSlug(title);
                    campaignData.card_name = normalizedCard;
                    campaignData.bank = normalizedBank;
                    campaignData.url = fullUrl;
                    campaignData.reference_url = fullUrl;
                    if (imageUrl) campaignData.image = imageUrl;

                    // Manual fallbacks
                    campaignData.description = campaignData.description || description || title;
                    campaignData.conditions = (campaignData.conditions && campaignData.conditions.length > 0) ? campaignData.conditions : (conditionsList.length > 0 ? conditionsList : null);
                    campaignData.category = campaignData.category || 'Diğer';
                    campaignData.sector_slug = generateSectorSlug(campaignData.category);
                    syncEarningAndDiscount(campaignData);
                    campaignData.publish_status = 'processing';
                    campaignData.publish_updated_at = new Date().toISOString();
                    campaignData.is_active = true;

                    // Expiration check
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

                    // ID Lookup
                    const ids = await lookupIDs(
                        campaignData.bank,
                        campaignData.card_name,
                        campaignData.brand,
                        campaignData.sector_slug,
                        campaignData.category
                    );
                    Object.assign(campaignData, ids);

                    // If bank_id lookup fails (e.g. if normalize didn't match perfectly with slug), enforce consistency
                    // We know for Türkiye Finans we want 'turkiye-finans'
                    if (normalizedBank === 'Türkiye Finans' && (!campaignData.bank_id || campaignData.bank_id === 'trkiyefinans')) {
                        campaignData.bank_id = 'turkiye-finans';
                    }

                    // Badge
                    const badge = assignBadge(campaignData);
                    campaignData.badge_text = badge.text;
                    campaignData.badge_color = badge.color;

                    markGenericBrand(campaignData);
                    campaignData.tags = campaignData.tags || [];

                    // Save to DB
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

            await sleep(1500);
        }

        console.log(`\n✅ ALA Card Scraper Finished. Processed ${processedCount} campaigns.`);

    } catch (error: any) {
        console.error(`❌ Global Error: ${error.message}`);
    }
}

runAlaCardScraper();
