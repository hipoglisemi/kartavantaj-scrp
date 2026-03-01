import axios from 'axios';
import * as cheerio from 'cheerio';
import puppeteer from 'puppeteer';
import { createClient } from '@supabase/supabase-js';
import * as dotenv from 'dotenv';
import { parseWithGemini } from '../../services/geminiParser';
import { generateSectorSlug, generateCampaignSlug } from '../../utils/slugify';
import { syncEarningAndDiscount } from '../../utils/dataFixer';
import { normalizeBankName, normalizeCardName } from '../../utils/bankMapper';
import { lookupIDs } from '../../utils/idMapper';
import { assignBadge } from '../../services/badgeAssigner';
import { markGenericBrand } from '../../utils/genericDetector';
import { optimizeCampaigns } from '../../utils/campaignOptimizer';

dotenv.config();

const supabase = createClient(process.env.SUPABASE_URL!, process.env.SUPABASE_ANON_KEY!);

const CARD_CONFIG = {
    name: 'Wings',
    cardName: 'Wings',
    bankName: 'Akbank',
    baseUrl: 'https://www.wingscard.com.tr',
    listApiUrl: 'https://www.wingscard.com.tr/api/campaign/list',
    refererUrl: 'https://www.wingscard.com.tr/kampanyalar'
};

const sleep = (ms: number) => new Promise(resolve => setTimeout(resolve, ms));

async function runWingsScraper() {
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
                    'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                    'Referer': CARD_CONFIG.refererUrl,
                    'X-Requested-With': 'XMLHttpRequest'
                },
                params: {
                    keyword: '',
                    sector: '',
                    category: '',
                    page: page.toString()
                }
            });

            const data = response.data;
            if (!data || !data.status || !data.data || !data.data.list || data.data.list.length === 0) {
                console.log('   ✅ No more campaigns. Finished.');
                break;
            }

            const campaigns = data.data.list;
            for (const campaign of campaigns) {
                if (allCampaigns.length >= limit) break;
                const detailPath = campaign.url || `/kampanyalar/kampanya-detay/${campaign.slug}`;
                const fullUrl = new URL(detailPath, CARD_CONFIG.baseUrl).toString();

                if (!allCampaigns.some(c => c.url === fullUrl)) {
                    allCampaigns.push({
                        url: fullUrl,
                        title: campaign.title
                    });
                }
            }

            if (allCampaigns.length >= data.data.totalCount || allCampaigns.length >= limit) break;
            page++;
            await sleep(500);
        } catch (error: any) {
            console.error(`   ❌ Error fetching list: ${error.message}`);
            break;
        }
    }

    console.log(`🎉 Found ${allCampaigns.length} campaigns via scraping.`);

    // 2. Optimize List
    const allUrls = allCampaigns.map(c => c.url);
    const { urlsToProcess } = await optimizeCampaigns(allUrls, normalizedCard);

    if (urlsToProcess.length === 0) {
        console.log('\n✅ All campaigns are already up to date. Finished.');
        return;
    }

    const campaignsToScrape = allCampaigns.filter(c => urlsToProcess.includes(c.url));

    // 3. Launch Browser with Stealth Mode
    const browser = await puppeteer.launch({
        headless: true,
        args: [
            '--no-sandbox',
            '--disable-setuid-sandbox',
            '--disable-blink-features=AutomationControlled',
            '--disable-features=IsolateOrigins,site-per-process',
            '--disable-web-security'
        ]
    });
    const browserPage = await browser.newPage();

    // Set realistic viewport
    await browserPage.setViewport({ width: 1920, height: 1080 });

    // Set comprehensive headers
    await browserPage.setUserAgent('Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36');
    await browserPage.setExtraHTTPHeaders({
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8',
        'Accept-Language': 'tr-TR,tr;q=0.9,en-US;q=0.8,en;q=0.7',
        'Accept-Encoding': 'gzip, deflate, br',
        'Connection': 'keep-alive',
        'Upgrade-Insecure-Requests': '1',
        'Sec-Fetch-Dest': 'document',
        'Sec-Fetch-Mode': 'navigate',
        'Sec-Fetch-Site': 'none',
        'Cache-Control': 'max-age=0'
    });

    // Remove webdriver flag
    await browserPage.evaluateOnNewDocument(() => {
        // @ts-ignore - navigator is available in browser context
        Object.defineProperty(navigator, 'webdriver', {
            get: () => false,
        });
    });

    // 4. Process Details
    for (const item of campaignsToScrape) {
        console.log(`\n   🔍 Fetching: ${item.url}`);

        try {
            // Add random delay before navigation (human-like behavior)
            await sleep(1000 + Math.random() * 1000);

            await browserPage.goto(item.url, { waitUntil: 'domcontentloaded', timeout: 45000 });
            await sleep(3000 + Math.random() * 1000); // Wait for content load with jitter
            const html = await browserPage.content();

            // Extract high-res image via Puppeteer
            const imageUrl = await browserPage.evaluate(() => {
                // @ts-ignore
                const detailImg = document.querySelector('.privileges-detail-image img');
                // @ts-ignore
                if (detailImg && detailImg.src && detailImg.src.includes('/api/uploads/')) {
                    // @ts-ignore
                    return detailImg.src;
                }
                // @ts-ignore
                const imgs = Array.from(document.querySelectorAll('img'));
                // @ts-ignore
                const campaignImg = imgs.find((img: any) =>
                    img.src &&
                    img.src.includes('/api/uploads/') &&
                    !img.src.includes('logo') &&
                    img.naturalWidth > 400
                );
                return (campaignImg as any)?.src || null;
            });

            let campaignData;
            if (isAIEnabled) {
                console.log(`   🤖 Stage 1: Full parse...`);
                campaignData = await parseWithGemini(html, item.url, normalizedBank, normalizedCard);
                console.log(`   ✅ Stage 1: Complete (all fields extracted)`);
            } else {
                const $ = cheerio.load(html);
                const title = $('h1.banner-title').text().trim() || item.title || 'Başlıksız';
                campaignData = {
                    title,
                    description: title,
                    category: 'Diğer',
                    sector_slug: 'diger',
                    card_name: normalizedCard,
                    bank: normalizedBank,
                    url: item.url,
                    reference_url: item.url,
                    is_active: true,
                    tags: [] // ✅ Smart Tagging: Empty array for non-AI mode
                };
            }

            if (campaignData) {
                // Ensure correct identification
                campaignData.card_name = normalizedCard;
                campaignData.bank = normalizedBank;
                campaignData.url = item.url;
                campaignData.reference_url = item.url;
                campaignData.is_active = true;

                if (!campaignData.image && imageUrl) {
                    campaignData.image = imageUrl;
                }

                campaignData.category = campaignData.category || 'Diğer';
                campaignData.sector_slug = generateSectorSlug(campaignData.category);
                syncEarningAndDiscount(campaignData);

                // Auto-expire check
                if (campaignData.end_date) {
                    const today = new Date();
                    today.setHours(0, 0, 0, 0);
                    if (new Date(campaignData.end_date) < today) {
                        console.log(`      ⚠️  Expired (${campaignData.end_date}), skipping...`);
                        continue;
                    }
                }

                // Lookup and assign IDs from master tables
                const ids = await lookupIDs(
                    campaignData.bank,
                    campaignData.card_name,
                    campaignData.brand,
                    campaignData.sector_slug,
                    campaignData.category
                );
                Object.assign(campaignData, ids);

                // Final Polish
                const badge = assignBadge(campaignData);
                campaignData.badge_text = badge.text;
                campaignData.badge_color = badge.color;
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
        } catch (err: any) {
            console.error(`      ❌ Error processing: ${err.message}`);
        }

        await sleep(3000 + Math.random() * 2000); // Longer delay between campaigns
    }

    await browser.close();
    console.log(`\n✅ ${CARD_CONFIG.name} scraper completed!`);
}

runWingsScraper().catch(err => {
    console.error(`\n❌ Error during scraper execution:`, err);
    process.exit(1);
});
