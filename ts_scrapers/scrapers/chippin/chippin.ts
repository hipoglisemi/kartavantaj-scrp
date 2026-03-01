
/**
 * @file chippin.ts
 * @description Scraper for Chippin campaigns using Puppeteer to extract Next.js hydration data.
 */

import puppeteer from 'puppeteer-extra';
// import StealthPlugin from 'puppeteer-extra-plugin-stealth'; // DISABLED - Detected as bot with stealth in this env
import { supabase } from '../../utils/supabase';
import * as dotenv from 'dotenv';
import { parseWithGemini } from '../../services/geminiParser';
import { generateSectorSlug, generateCampaignSlug } from '../../utils/slugify';
import { normalizeBankName, normalizeCardName } from '../../utils/bankMapper';
import { syncEarningAndDiscount } from '../../utils/dataFixer';
import { lookupIDs } from '../../utils/idMapper';
import { assignBadge } from '../../services/badgeAssigner';
import { markGenericBrand } from '../../utils/genericDetector';
import { optimizeCampaigns } from '../../utils/campaignOptimizer';
import { downloadImageDirectly } from '../../services/imageService';

dotenv.config();

// puppeteer.use(StealthPlugin());

// Supabase client is now imported from ../../utils/supabase

const BASE_URL = 'https://www.chippin.com';
const CAMPAIGNS_URL = 'https://www.chippin.com/kampanyalar';

interface ChippinCampaign {
    id: string;
    webBanner: string;
    webName: string;
    webDescription: string;
    // other props might exist but these are what we saw in inspection
}

async function runChippinScraper() {
    console.log('🚀 Starting Chippin Scraper (Production - Legacy Mode)...');

    // Normalize names first
    const normalizedBank = await normalizeBankName('Chippin');
    const normalizedCard = await normalizeCardName(normalizedBank, 'Chippin');

    // Parse limit argument
    const limitArg = process.argv.find(arg => arg.startsWith('--limit='));
    const limit = limitArg ? parseInt(limitArg.split('=')[1]) : 1000;

    console.log(`   Bank: ${normalizedBank}, Card: ${normalizedCard}`);

    // Verify Bank/Card existence and get SLUGS (not IDs)
    const { data: bankData, error: bankErr } = await supabase
        .from('master_banks')
        .select('slug')
        .eq('slug', 'chippin')
        .single();

    if (bankErr || !bankData) {
        throw new Error('Chippin bank not found in master_banks.');
    }

    const { data: cardData, error: cardErr } = await supabase
        .from('cards')
        .select('slug')
        .eq('slug', 'chippin')
        .single();

    if (cardErr || !cardData) {
        throw new Error('Chippin card not found in cards table. Run seed script.');
    }

    console.log(`   ✅ IDs Found - Bank: ${bankData.slug}, Card: ${cardData.slug}`);

    const browser = await puppeteer.launch({
        headless: true, // Legacy headless - Proven to work
        args: [
            '--no-sandbox',
            '--disable-setuid-sandbox',
            // '--disable-blink-features=AutomationControlled' // standard args
        ]
    });

    try {
        const page = await browser.newPage();

        // Use standard UA for debug
        await page.setUserAgent('Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36');

        console.log(`   📄 Navigating to ${CAMPAIGNS_URL}...`);
        await page.goto(CAMPAIGNS_URL, { waitUntil: 'domcontentloaded', timeout: 60000 });

        // Moderate wait time
        await new Promise(r => setTimeout(r, 6000));

        // Extract __NEXT_DATA__
        const rawData = await page.evaluate(() => {
            const script = document.getElementById('__NEXT_DATA__');
            return script ? script.innerHTML : null;
        });

        if (!rawData) {
            throw new Error('__NEXT_DATA__ script not found. Page might have changed or is blocked.');
        }

        const nextData = JSON.parse(rawData);
        const campaigns: ChippinCampaign[] = nextData?.props?.pageProps?.campaigns || [];

        console.log(`   🎉 Found ${campaigns.length} campaigns in Next.js data.`);

        // Extract reference URLs for optimization
        const allReferenceUrls = campaigns.map(item => `${CAMPAIGNS_URL}/${item.id}`);
        const uniqueLinks = [...new Set(allReferenceUrls)];
        console.log(`\n   🎉 Found ${uniqueLinks.length} unique campaign links.`);

        const cardNameForOptimization = 'Chippin';
        const { urlsToProcess } = await optimizeCampaigns(uniqueLinks, cardNameForOptimization);

        // Filter campaigns based on optimization results
        const finalCampaigns = campaigns.filter(item => urlsToProcess.includes(`${CAMPAIGNS_URL}/${item.id}`)).slice(0, limit);

        console.log(`\n   🚀 Processing details for ${finalCampaigns.length} campaigns (skipping ${uniqueLinks.length - finalCampaigns.length} complete/existing) (Limit: ${limit})...\n`);

        let processedCount = 0;
        for (const item of finalCampaigns) {
            if (processedCount >= limit) break; // Safety break, though finalCampaigns is already filtered
            const title = item.webName.trim();

            // FIX: Images are hosted on asset.chippin.com, not www.chippin.com
            let imageUrl = item.webBanner;
            if (!imageUrl.startsWith('http')) {
                // Replace www.chippin.com with asset.chippin.com for images
                imageUrl = `https://asset.chippin.com${imageUrl}`;
            } else if (imageUrl.includes('www.chippin.com')) {
                // Fix existing full URLs that use www instead of asset
                imageUrl = imageUrl.replace('www.chippin.com', 'asset.chippin.com');
            }

            // PROXY IMAGE: Download from Chippin -> Upload to Supabase
            // Use direct HTTP download since Chippin images are publicly accessible
            imageUrl = await downloadImageDirectly(imageUrl, title, 'chippin');

            const descriptionOriginal = item.webDescription;
            const referenceUrl = `${CAMPAIGNS_URL}/${item.id}`; // Correct format: /kampanyalar/{id}

            console.log(`\n   🔍 Processing [${++processedCount}/${campaigns.length}]: ${title}`);

            // VISIT DETAIL PAGE to get "Nasıl Katılırım?" section
            console.log(`   📄 Visiting detail page: ${referenceUrl}`);
            let participationInfo = '';
            try {
                await page.goto(referenceUrl, { waitUntil: 'domcontentloaded', timeout: 30000 });
                await new Promise(r => setTimeout(r, 2000));

                // Extract "Nasıl Katılırım?" section
                participationInfo = await page.evaluate(() => {
                    // Look for the participation section
                    const headings = Array.from(document.querySelectorAll('h1, h2, h3, h4, h5, h6'));
                    const participationHeading = headings.find(h =>
                        h.textContent?.includes('Nasıl Katılırım') ||
                        h.textContent?.includes('Nasıl Katılır')
                    );

                    if (participationHeading) {
                        // Get next sibling elements until next heading
                        let content = '';
                        let element = participationHeading.nextElementSibling;
                        while (element && !['H1', 'H2', 'H3', 'H4', 'H5', 'H6'].includes(element.tagName)) {
                            content += element.textContent + '\n';
                            element = element.nextElementSibling;
                        }
                        return content.trim();
                    }
                    return '';
                });

                if (participationInfo) {
                    console.log(`   ✅ Found participation info (${participationInfo.length} chars)`);
                }
            } catch (e: any) {
                console.warn(`   ⚠️ Could not load detail page: ${e.message}`);
            }

            const campaignHtml = `
                <h1>${title}</h1>
                <div class="description">${descriptionOriginal}</div>
                ${participationInfo ? `<div class="participation"><h3>Nasıl Katılırım?</h3>${participationInfo}</div>` : ''}
                <img src="${imageUrl}" />
            `;

            const campaignData = await parseWithGemini(campaignHtml, referenceUrl, normalizedBank, normalizedCard);

            if (campaignData) {
                // Enforce critical fields from source
                campaignData.title = title;
                campaignData.slug = generateCampaignSlug(title); // Regenerate slug

                // IMPORTANT: Use ai_marketing_text as description (like other scrapers)
                // Keep original long description in a backup field if needed
                let finalDescription = '';

                if (campaignData.ai_marketing_text) {
                    finalDescription = campaignData.ai_marketing_text;
                } else if (campaignData.earning) {
                    finalDescription = campaignData.earning;
                } else {
                    finalDescription = descriptionOriginal;
                }

                // CHIPPIN-SPECIFIC: Aggressively truncate to max 10 words
                const words = finalDescription.trim().split(/\s+/);
                if (words.length > 10) {
                    console.log(`   ✂️ Truncating description from ${words.length} to 10 words`);
                    finalDescription = words.slice(0, 10).join(' ');
                }

                campaignData.description = finalDescription;

                campaignData.image = imageUrl;
                campaignData.bank = normalizedBank;
                campaignData.card_name = normalizedCard;

                // FORCE VALID SLUGS
                campaignData.bank_id = bankData.slug;
                campaignData.card_id = cardData.slug;

                campaignData.url = referenceUrl;
                campaignData.reference_url = referenceUrl;
                campaignData.is_active = true;
                campaignData.image_url = imageUrl; // Populate standard image_url field

                // Defaults / fallbacks
                campaignData.category = campaignData.category || 'Diğer';
                campaignData.sector_slug = generateSectorSlug(campaignData.category);

                // Fixes
                syncEarningAndDiscount(campaignData);
                campaignData.publish_status = 'processing';
                campaignData.publish_updated_at = new Date().toISOString();
                campaignData.image_migrated = false; // Bridge flag for Cloudflare migration

                // IDs - We do lookup only for brand/sector, preserving our bank/card IDs
                const ids = await lookupIDs(
                    campaignData.bank,
                    campaignData.card_name,
                    campaignData.brand,
                    campaignData.sector_slug
                );
                // Only merge sector/brand IDs, check existing assignments
                if (ids.brand_id) campaignData.brand_id = ids.brand_id;

                // Badges
                const badge = assignBadge(campaignData);
                campaignData.badge_text = badge.text;
                campaignData.badge_color = badge.color;

                markGenericBrand(campaignData);

                campaignData.tags = campaignData.tags || [];


                // Save
                console.log(`      💾 Processing: ${title.substring(0, 30)}...`);

                const { data: existing } = await supabase
                    .from('campaigns')
                    .select('id')
                    .eq('reference_url', referenceUrl)
                    .single();

                if (existing) {
                    // Mevcut kampanya - güncelle
                    const finalSlug = generateCampaignSlug(title, existing.id);
                    const { error } = await supabase
                        .from('campaigns')
                        .update({ ...campaignData, slug: finalSlug })
                        .eq('id', existing.id);
                    if (error) {
                        console.error(`      ❌ Update Error for "${title}": ${error.message}`);
                    } else {
                        console.log(`      ✅ Updated: ${title.substring(0, 30)}... (${finalSlug})`);
                    }
                } else {
                    // Yeni kampanya - ekle
                    const { data: inserted, error: insertError } = await supabase
                        .from('campaigns')
                        .insert(campaignData)
                        .select('id')
                        .single();
                    if (insertError) {
                        console.error(`      ❌ Insert Error for "${title}": ${insertError.message}`);
                    } else if (inserted) {
                        const finalSlug = generateCampaignSlug(title, inserted.id);
                        await supabase
                            .from('campaigns')
                            .update({ slug: finalSlug })
                            .eq('id', inserted.id);
                        console.log(`      ✅ Inserted: ${title.substring(0, 30)}... (${finalSlug})`);
                    }
                }
            } else {
                console.error(`      ⚠️ Failed to parse with AI`);
            }
        }

    } catch (error: any) {
        console.error(`❌ Error in Chippin Scraper: ${error.message}`);
    } finally {
        await browser.close();
    }
}

runChippinScraper();
