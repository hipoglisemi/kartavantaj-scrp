import { supabase } from '../../utils/supabase'; // Shared client
import puppeteer from 'puppeteer-extra';
import StealthPlugin from 'puppeteer-extra-plugin-stealth';
import * as cheerio from 'cheerio';
import {
    cleanText,
    formatDateIso,
    trLower
} from '../../utils/MaximumHelpers';
import { normalizeBankName } from '../../utils/bankMapper';
import { lookupIDs } from '../../utils/idMapper';
import { generateSectorSlug, generateCampaignSlug } from '../../utils/slugify';
import { optimizeCampaigns } from '../../utils/campaignOptimizer';
import { downloadImageDirectly } from '../../services/imageService';
import { parseWithGemini } from '../../services/geminiParser';
import { syncEarningAndDiscount } from '../../utils/dataFixer';
import { assignBadge } from '../../services/badgeAssigner';
import { markGenericBrand } from '../../utils/genericDetector';

// Use Stealth Plugin
puppeteer.use(StealthPlugin());

const BASE_URL = 'https://www.maximiles.com.tr';
const CAMPAIGNS_URL = 'https://www.maximiles.com.tr/kampanyalar';

const sleep = (ms: number) => new Promise(resolve => setTimeout(resolve, ms));

async function runMaximilesScraper() {
    console.log('🚀 Starting İş Bankası (Maximiles) Scraper...');

    const limitArg = process.argv.find(arg => arg.startsWith('--limit='));
    const limit = limitArg ? parseInt(limitArg.split('=')[1]) : 1000;

    let browser;
    const isCI = process.env.GITHUB_ACTIONS === 'true' || process.env.CI === 'true';

    if (!isCI) {
        try {
            console.log('   🔌 Connecting to Chrome debug instance on port 9222...');
            browser = await puppeteer.connect({
                browserURL: 'http://localhost:9222',
                defaultViewport: null
            });
            console.log('   ✅ Connected to existing Chrome instance');
        } catch (error) {
            console.log('   ⚠️  Could not connect to debug Chrome, launching new instance...');
        }
    }

    if (!browser) {
        console.log(`   🚀 Launching new browser instance (Headless: ${isCI})...`);
        browser = await puppeteer.launch({
            headless: isCI,
            args: [
                '--no-sandbox',
                '--disable-setuid-sandbox',
                '--disable-dev-shm-usage',
                '--disable-gpu',
                '--window-position=-10000,0',
                '--disable-blink-features=AutomationControlled'
            ]
        });
    }

    const page = await browser.newPage();
    await page.setViewport({ width: 1400, height: 900 });

    const userAgents = [
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36',
        'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
    ];
    const randomUA = userAgents[Math.floor(Math.random() * userAgents.length)];

    await page.setUserAgent(randomUA);
    await page.setExtraHTTPHeaders({
        'Accept-Language': 'tr-TR,tr;q=0.9',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8',
        'Connection': 'keep-alive',
        'Upgrade-Insecure-Requests': '1',
        'Sec-Fetch-Dest': 'document',
        'Sec-Fetch-Mode': 'navigate',
        'Sec-Fetch-Site': 'none',
        'Sec-Fetch-User': '?1'
    });

    await page.evaluateOnNewDocument(() => {
        // @ts-ignore
        Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
        // @ts-ignore
        navigator.languages = ['tr-TR', 'tr', 'en-US', 'en'];
    });

    try {
        console.log(`   🔍 Loading Campaign List: ${CAMPAIGNS_URL}...`);

        let listLoaded = false;
        let listRetries = 0;
        const maxListRetries = 10;

        while (!listLoaded && listRetries < maxListRetries) {
            try {
                await page.goto(CAMPAIGNS_URL, { waitUntil: 'networkidle2', timeout: 45000 });
                listLoaded = true;
            } catch (e: any) {
                listRetries++;
                const backoff = Math.min(listRetries * 5000, 30000);
                console.log(`      ⚠️  List load attempt ${listRetries}/${maxListRetries} failed: ${e.message}. Retrying in ${backoff / 1000}s...`);
                await sleep(backoff);
                await page.setUserAgent(userAgents[listRetries % userAgents.length]);
            }
        }

        if (!listLoaded) throw new Error(`Could not load campaign list after ${maxListRetries} attempts`);

        console.log('      🍪 Checking for cookie banners...');
        try {
            await page.evaluate(() => {
                const cookieBtns = Array.from(document.querySelectorAll('button, a'));
                const acceptBtn = cookieBtns.find(b =>
                    b.textContent?.includes('Anladım') ||
                    b.textContent?.includes('Kapat') ||
                    b.textContent?.includes('Kabul Et')
                );
                if (acceptBtn) (acceptBtn as HTMLElement).click();
            });
            await sleep(1000);
        } catch (e) { /* ignore */ }

        await sleep(3000);

        // --- INFINITE SCROLL LOGIC ---
        console.log('      🖱️  Starting infinite scroll...');
        let previousHeight = await page.evaluate('document.body.scrollHeight');
        let scrollRetries = 0;
        const maxScrollRetries = 3;
        let totalScrolled = 0;
        let stopDueToExpiry = false;

        while (scrollRetries < maxScrollRetries && !stopDueToExpiry) {
            try {
                await page.evaluate('window.scrollTo(0, document.body.scrollHeight)');
                await sleep(3000); // Wait for content load

                // Check for expired campaign indicators in the current view
                stopDueToExpiry = await page.evaluate(() => {
                    const searchStrings = ['sona ermiştir', 'kampanya sona ermiştir'];
                    const cards = Array.from(document.querySelectorAll('.campaign-card, .card, [class*="campaign"]'));
                    const lastFewCards = cards.slice(-10); // Check the latest loaded cards

                    const expiredCount = lastFewCards.filter(c => {
                        const text = c.textContent?.toLowerCase() || "";
                        const html = c.innerHTML.toLowerCase();
                        return searchStrings.some(s => text.includes(s) || html.includes(s));
                    }).length;

                    // If we see multiple expired cards in the new batch, we've likely reached the archive
                    return expiredCount >= 2;
                });

                if (stopDueToExpiry) {
                    console.log('\n      🛑 Detected "Sona Ermiştir" indicators. Stopping early.');
                    break;
                }

                let currentHeight = await page.evaluate('document.body.scrollHeight') as number;
                if (currentHeight > (previousHeight as number)) {
                    previousHeight = currentHeight;
                    scrollRetries = 0;
                    totalScrolled++;
                    process.stdout.write('.');
                } else {
                    scrollRetries++;
                    // Extra scroll to be safe
                    await page.evaluate('window.scrollBy(0, -300)');
                    await sleep(500);
                    await page.evaluate('window.scrollTo(0, document.body.scrollHeight)');
                    await sleep(2000);
                }
            } catch (evalError) {
                console.log('      ⚠️  Scroll evaluation hiccup, retrying...');
                scrollRetries++;
                await sleep(2000);
            }
        }
        console.log(`\n      ✅ Finished scrolling after ${totalScrolled} iterations.`);

        // --- EXTRACT LINKS ---
        const content = await page.content();
        const $ = cheerio.load(content);
        let allLinks: string[] = [];

        // Category keywords to exclude
        const categorySuffixes = [
            '-kampanyalari',
            '-kampanyalar',
            'premium-kampanyalar',
            'tum-kampanyalar',
        ];

        $('a').each((_, el) => {
            const href = $(el).attr('href');
            if (href && (href.includes('/kampanyalar/') || href.includes('kampanyalar/')) && !href.includes('arsiv')) {
                const lowerHref = href.toLowerCase();
                const isCategorySuffix = categorySuffixes.some(suffix => lowerHref.endsWith(suffix));
                const isCommonPage = lowerHref.includes('ozellikler') || lowerHref.includes('basvuru') || lowerHref.endsWith('/kampanyalar');

                if (!isCategorySuffix && !isCommonPage && href.length > 25) {
                    let fullUrl = href.startsWith('http') ? href : `${BASE_URL}${href.startsWith('/') ? '' : '/'}${href}`;
                    if (!allLinks.includes(fullUrl)) {
                        allLinks.push(fullUrl);
                    }
                }
            }
        });

        const uniqueLinks = [...new Set(allLinks)];
        console.log(`\n   🎉 Found ${uniqueLinks.length} unique campaigns.`);

        console.log(`   🔍 Normalizing bank name...`);
        const bankName = await normalizeBankName('İş Bankası');
        console.log(`   ✅ Normalized bank: ${bankName}`);

        const cardNameForOptimization = 'Maximiles';
        const { urlsToProcess } = await optimizeCampaigns(uniqueLinks, cardNameForOptimization);

        const finalLinks = uniqueLinks.filter(url => urlsToProcess.includes(url)).slice(0, limit);
        console.log(`   🚀 Processing details for ${finalLinks.length} campaigns (skipping ${uniqueLinks.length - finalLinks.length} complete/existing)...\n`);

        let count = 0;
        for (const url of finalLinks) {
            console.log(`   🔍 Processing [${count + 1}/${Math.min(uniqueLinks.length, limit)}]: ${url}`);
            if (count >= limit) break;

            try {
                await sleep(3000 + Math.random() * 2000);

                let detailRetries = 0;
                let success = false;
                while (detailRetries < 5 && !success) {
                    try {
                        await page.goto(url, { waitUntil: 'networkidle2', timeout: 30000 });
                        success = true;
                    } catch (e: any) {
                        detailRetries++;
                        const backoff = 3000 * detailRetries;
                        console.error(`      ⚠️ Detail load attempt ${detailRetries}/5 for ${url}: ${e.message}. Retrying in ${backoff / 1000}s...`);
                        await sleep(backoff);
                        await page.setUserAgent(userAgents[detailRetries % userAgents.length]);
                    }
                }

                if (!success) {
                    console.error(`      ❌ Failed to load ${url} after 5 retries.`);
                    continue;
                }

                await page.evaluate(() => window.scrollTo(0, 600));
                await sleep(500);

                const detailContent = await page.content();
                const $d = cheerio.load(detailContent);

                const titleEl = $d('h1.page-title').first() || $d('h1').first();
                const title = cleanText(titleEl.text() || "Başlık Yok");

                if (trLower(title).includes('geçmiş') || title.length < 10) continue;

                // Dates
                // Maximiles seems to put dates in strong tags or specific divs, but let's try to find it textually if ID fails
                // The dump showed <div class="d-flex flex-column"><strong>Başlangıç - Bitiş Tarihi</strong> <span> 01.01.2026 - 31.01.2026 </span></div>
                let dateText = "";
                const dateEl = $d("div.campaign-detail-box2 span").first();
                if (dateEl.length) {
                    dateText = cleanText(dateEl.text());
                } else {
                    const oldDateEl = $d("span[id$='KampanyaTarihleri']");
                    if (oldDateEl.length) dateText = cleanText(oldDateEl.text());
                }

                const validUntil = formatDateIso(dateText, true);

                if (validUntil && new Date(validUntil) < new Date()) continue;

                // Image
                let image = "";
                const imgEl = $d(".campaign-first-image img").first();
                if (imgEl.length > 0) {
                    const src = imgEl.attr('src');
                    if (src) {
                        const imageUrl = src.startsWith('http') ? src : `${BASE_URL}${src}`;
                        image = await downloadImageDirectly(imageUrl, title, 'maximiles');
                    }
                }

                const normalizedCardNameVal = 'Maximiles';
                const fullPageText = cleanText($d.text());

                const campaignHtml = `
                    <h1>${title}</h1>
                    <div class="dates">${dateText}</div>
                    <div class="full-text-context">${fullPageText}</div>
                    <img src="${image}" />
                `;

                const campaignData = await parseWithGemini(campaignHtml, url, bankName, normalizedCardNameVal);

                if (campaignData) {
                    campaignData.title = title;
                    campaignData.slug = generateCampaignSlug(title); // Regenerate slug
                    campaignData.image = image;
                    campaignData.image_url = image;
                    campaignData.bank = bankName;
                    campaignData.card_name = normalizedCardNameVal;
                    campaignData.url = url;
                    campaignData.reference_url = url;
                    campaignData.is_active = true;

                    syncEarningAndDiscount(campaignData);
                    campaignData.publish_status = 'processing';
                    campaignData.publish_updated_at = new Date().toISOString();
                    campaignData.image_migrated = false; // Bridge flag for Cloudflare migration

                    const ids = await lookupIDs(
                        campaignData.bank,
                        campaignData.card_name,
                        campaignData.brand,
                        campaignData.sector_slug,
                        campaignData.category
                    );

                    campaignData.bank_id = ids.bank_id || 'is-bankasi';
                    campaignData.card_id = ids.card_id || 'maximiles';
                    if (ids.brand_id) campaignData.brand_id = ids.brand_id;
                    if (ids.sector_id) campaignData.sector_id = ids.sector_id;

                    const badge = assignBadge(campaignData);
                    campaignData.badge_text = badge.text;
                    campaignData.badge_color = badge.color;

                    markGenericBrand(campaignData);

                    campaignData.tags = campaignData.tags || [];


                    count++;
                    console.log(`      [${count}] ${title.substring(0, 35)}... (Img: ${image ? '✅' : '❌'})`);

                    console.log(`      💾 Processing: ${title.substring(0, 30)}... [bank_id: ${campaignData.bank_id}, card_id: ${campaignData.card_id}]`);

                    const { data: existing } = await supabase
                        .from('campaigns')
                        .select('id')
                        .eq('reference_url', campaignData.reference_url)
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
                    console.error(`      ❌ AI Parsing failed for ${url}`);
                }

            } catch (e: any) {
                console.error(`      ⚠️ Error processing ${url}:`, e.message);
            }
        }

        console.log(`\n✅ Maximiles Scraper Finished. Processed ${count} campaigns.`);

    } catch (e: any) {
        console.error('❌ Critical Error:', e);
    } finally {
        await browser.close();
    }
}

if (require.main === module) {
    runMaximilesScraper();
}
