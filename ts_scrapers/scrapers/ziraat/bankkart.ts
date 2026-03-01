/// <reference lib="dom" />

import puppeteer, { Page } from 'puppeteer';
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
    name: 'Bankkart',
    cardName: 'Bankkart',
    bankName: 'Ziraat',
    baseUrl: 'https://www.bankkart.com.tr',
    listUrl: 'https://www.bankkart.com.tr/kampanyalar',
};

const sleep = (ms: number) => new Promise(resolve => setTimeout(resolve, ms));


// Selector priorities for content
const CONTENT_SELECTORS = [
    '.subpage-detail',
    '.accordion',
    '.tab-content',
    '#tab-details',
    '.campaign-detail-content'
];

export async function scrapeCampaignDetail(page: Page, fullUrl: string) {
    await page.goto(fullUrl, { waitUntil: 'domcontentloaded', timeout: 60000 });

    // 1. Handle Accordions/Tabs (Expand all)
    try {
        const triggers = await page.$$('.accordion-toggle, .nav-tabs a, details summary, .panel-heading a');
        for (const trigger of triggers) {
            try {
                await trigger.evaluate(el => (el as HTMLElement).click());
                await sleep(100);
            } catch (e) { }
        }
        await sleep(1000); // Wait for animations
    } catch (e) {
        console.log('   ⚠️  Interaction error (ignoring):', e);
    }

    // 2. Extract Clean HTML
    const content = await page.evaluate(() => {
        const doc = document as any;

        // Remove known junk
        doc.querySelectorAll('script, style, iframe, footer, nav, header, .campaign-boxes, .other-campaigns, .cookie-banner, .popup').forEach((el: any) => el.remove());

        // Try to find the specific content container first
        let mainContent = '';
        const specificContent = doc.querySelector('.subpage-detail, .accordion, .detail-content');

        if (specificContent) {
            mainContent = specificContent.innerHTML;
        } else {
            // Fallback to body but cleaned
            mainContent = doc.body.innerHTML;
        }

        // Specific Cleaners for Ziraat
        // Remove "Hemen Katıl" forms which confuse AI with phone inputs
        const tempDiv = document.createElement('div');
        tempDiv.innerHTML = mainContent;
        tempDiv.querySelectorAll('.form-area, .tabs-form, .modal, #sendNewPhoneAndCardData').forEach((el: any) => el.remove());
        mainContent = tempDiv.innerHTML;

        const detailJson = doc.querySelector('script[type="application/ld+json"]')?.textContent;
        let ldTitle = '';
        try { if (detailJson) ldTitle = JSON.parse(detailJson).name; } catch (e) { }

        return {
            html: mainContent,
            title: ldTitle ||
                doc.querySelector('.subpage-detail h1')?.innerText?.trim() ||
                doc.querySelector('h1')?.innerText?.trim() ||
                doc.querySelector('.campaign-detail-title')?.innerText?.trim() ||
                'Başlıksız Kampanya',
            image: doc.querySelector('#firstImg')?.getAttribute('src') ||
                doc.querySelector('.subpage-detail figure img')?.getAttribute('src') ||
                doc.querySelector('.campaign-detail-img img')?.getAttribute('src')
        };
    });

    if (content.image && !content.image.startsWith('http')) {
        content.image = new URL(content.image, CARD_CONFIG.baseUrl).toString();
    }

    return content;
}

async function runBankkartScraper() {
    const normalizedBank = await normalizeBankName(CARD_CONFIG.bankName);
    const normalizedCard = await normalizeCardName(normalizedBank, CARD_CONFIG.cardName);

    console.log(`\n💳 Starting ${CARD_CONFIG.name} Card Scraper...`);
    console.log(`   Bank: ${normalizedBank}`);
    console.log(`   Card: ${normalizedCard}`);
    console.log(`   Source: ${CARD_CONFIG.baseUrl}\n`);

    const isAIEnabled = process.argv.includes('--ai');
    const limitArg = process.argv.find(arg => arg.startsWith('--limit='));
    const limit = limitArg ? parseInt(limitArg.split('=')[1]) : Infinity;

    const browser = await puppeteer.launch({
        headless: true,
        args: ['--no-sandbox', '--disable-setuid-sandbox', '--disable-notifications']
    });
    const page = await browser.newPage();
    await page.setViewport({ width: 1920, height: 1080 });

    // Performance Optimization: Block unnecessary requests
    let blockAssets = true;
    await page.setRequestInterception(true);
    page.on('request', (request) => {
        if (!blockAssets) {
            request.continue();
            return;
        }
        const resourceType = request.resourceType();
        if (['image', 'stylesheet', 'font', 'media'].includes(resourceType)) {
            request.abort();
        } else {
            request.continue();
        }
    });

    try {
        console.log(`   🔍 Navigating to ${CARD_CONFIG.listUrl}...`);
        await page.goto(CARD_CONFIG.listUrl, { waitUntil: 'domcontentloaded', timeout: 60000 });

        // Infinite Scroll Logic
        console.log('   📜 Scrolling to load more campaigns...');
        let lastHeight = await page.evaluate('document.body.scrollHeight') as number;
        let noChangeCount = 0;

        while (noChangeCount < 3) {
            await page.evaluate('window.scrollTo(0, document.body.scrollHeight)');
            await sleep(2000);
            let newHeight = await page.evaluate('document.body.scrollHeight') as number;
            if (newHeight === lastHeight) {
                noChangeCount++;
            } else {
                noChangeCount = 0;
                lastHeight = newHeight;
                process.stdout.write('.');
            }
        }
        console.log('\n   ✅ Scroll completed.');

        // Extract Links
        const campaignLinks = await page.evaluate(() => {
            const links: string[] = [];
            // @ts-ignore
            document.querySelectorAll('a.campaign-box').forEach((a: any) => {
                const href = a.getAttribute('href');
                if (href && href.includes('/kampanyalar/')) {
                    links.push(href);
                }
            });
            return [...new Set(links)];
        });

        console.log(`\n   🎉 Found ${campaignLinks.length} potential campaigns.`);

        // 2. Optimize: Check DB for existing campaigns
        const allUrls = campaignLinks.map(link =>
            link.startsWith('http') ? link : `${CARD_CONFIG.baseUrl}${link}`
        );

        console.log(`   🔍 Optimizing list via database check...`);
        const { urlsToProcess: optimizedParams } = await optimizeCampaigns(allUrls, normalizedCard);

        const urlsToProcess = optimizedParams;

        const finalItems = urlsToProcess.slice(0, limit);
        console.log(`   🚀 Processing details for ${finalItems.length} campaigns (Limit: ${limit})...\n`);

        // Enable images/styles for detail pages to ensure better content extraction if needed
        blockAssets = false;

        for (const fullUrl of finalItems) {
            console.log(`\n   🔍 Fetching: ${fullUrl.substring(0, 70)}...`);

            try {
                const content = await scrapeCampaignDetail(page, fullUrl);

                // AI Parsing
                let campaignData;
                if (isAIEnabled) {
                    const metadata = {
                        title: content.title,
                        image: content.image,
                        bank: normalizedBank,
                        card: normalizedCard
                    };
                    campaignData = await parseWithGemini(content.html, fullUrl, normalizedBank, normalizedCard, metadata);
                } else {
                    campaignData = {
                        title: content.title,
                        description: content.title,
                        card_name: normalizedCard,
                        url: fullUrl,
                        reference_url: fullUrl,
                        image: content.image,
                        category: 'Diğer',
                        sector_slug: 'diger',
                        is_active: true,
                        tags: []
                    } as any;
                }

                if (campaignData) {
                    // Force fields for consistency
                    campaignData.title = content.title; // Always use site title as authority
                    campaignData.slug = generateCampaignSlug(campaignData.title); // Generate initial slug
                    campaignData.card_name = normalizedCard;
                    campaignData.bank = normalizedBank;
                    campaignData.url = fullUrl;
                    campaignData.reference_url = fullUrl;
                    campaignData.image = content.image;
                    campaignData.category = campaignData.category || 'Diğer';
                    campaignData.sector_slug = generateSectorSlug(campaignData.category);

                    syncEarningAndDiscount(campaignData);
                    campaignData.is_active = true;

                    // Expiration Check
                    if (campaignData.valid_until) {
                        const today = new Date();
                        today.setHours(0, 0, 0, 0);
                        const expiry = new Date(campaignData.valid_until);
                        if (expiry < today) {
                            console.log(`      ⚠️  Expired (${campaignData.valid_until}), skipping...`);
                            continue;
                        }
                    }

                    // ID Lookup
                    const ids = await lookupIDs(
                        campaignData.bank,
                        campaignData.card_name,
                        campaignData.brand,
                        campaignData.sector_slug,
                        campaignData.category
                    );
                    Object.assign(campaignData, ids);

                    // UX Enhancement
                    const badge = assignBadge(campaignData);
                    campaignData.badge_text = badge.text;
                    campaignData.badge_color = badge.color;
                    markGenericBrand(campaignData);

                    campaignData.tags = campaignData.tags || [];


                    // ID-BASED SLUG SYSTEM
                    const { data: existing } = await supabase
                        .from('campaigns')
                        .select('id')
                        .eq('reference_url', fullUrl)
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
                            console.log(`      ✅ Updated: ${content.title.substring(0, 30)}... (${finalSlug})`);
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
                            console.log(`      ✅ Inserted: ${content.title.substring(0, 30)}... (${finalSlug})`);
                        }
                    }


                }
            } catch (err: any) {
                console.error(`      ❌ Detail Error: ${err.message}`);
            }

            await sleep(2000); // Respect Ziraat's server
        }

    } catch (error: any) {
        console.error(`❌ Global Error: ${error.message}`);
    } finally {
        await browser.close();
        console.log(`\n✅ ${CARD_CONFIG.name} scraper completed!`);
    }
}

if (require.main === module) {
    runBankkartScraper();
}
