
import puppeteer from 'puppeteer-extra';
import StealthPlugin from 'puppeteer-extra-plugin-stealth';
import { supabase } from '../../utils/supabase';
import * as dotenv from 'dotenv';
import * as cheerio from 'cheerio'; // Cheerio for fast parsing like BeautifulSoup
import {
    getCategory,
    extractMerchant,
    cleanText,
    formatDateIso,
    extractFinancialsV8,
    extractParticipation,
    extractCardsPrecise,
    trLower
} from '../../utils/MaximumHelpers';
import { normalizeBankName, normalizeCardName } from '../../utils/bankMapper';
import { lookupIDs } from '../../utils/idMapper';
import { generateSectorSlug, generateCampaignSlug } from '../../utils/slugify';
import { optimizeCampaigns } from '../../utils/campaignOptimizer';
import { downloadImageDirectly } from '../../services/imageService';
import { parseWithGemini } from '../../services/geminiParser';
import { syncEarningAndDiscount } from '../../utils/dataFixer';
import { assignBadge } from '../../services/badgeAssigner';
import { markGenericBrand } from '../../utils/genericDetector';

dotenv.config();

// Use Stealth Plugin
puppeteer.use(StealthPlugin());

// Supabase client is now imported from ../../utils/supabase

const BASE_URL = 'https://www.maximum.com.tr';
const CAMPAIGNS_URL = 'https://www.maximum.com.tr/kampanyalar';

const sleep = (ms: number) => new Promise(resolve => setTimeout(resolve, ms));

async function runMaximumScraperTS() {
    console.log('🚀 Starting İş Bankası (Maximum) Scraper (TS Stealth + V8 Engine)...');

    // Parse limit argument
    const limitArg = process.argv.find(arg => arg.startsWith('--limit='));
    const limit = limitArg ? parseInt(limitArg.split('=')[1]) : 1000;

    // Connect to existing Chrome instance running in debug mode (Local only)
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
    // Simulate typical desktop view
    await page.setViewport({ width: 1400, height: 900 });

    // --- STEALTH HEADERS ---
    const userAgents = [
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36',
        'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36',
        'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36',
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36',
        'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36'
    ];
    const randomUA = userAgents[Math.floor(Math.random() * userAgents.length)];

    await page.setUserAgent(randomUA);
    await page.setExtraHTTPHeaders({
        'Accept-Language': 'tr-TR,tr;q=0.9,en-US;q=0.8,en;q=0.7',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7',
        'Accept-Encoding': 'gzip, deflate, br',
        'Cache-Control': 'max-age=0',
        'Connection': 'keep-alive',
        'Upgrade-Insecure-Requests': '1',
        'Sec-Fetch-Dest': 'document',
        'Sec-Fetch-Mode': 'navigate',
        'Sec-Fetch-Site': 'none',
        'Sec-Fetch-User': '?1',
        'sec-ch-ua': '"Chromium";v="131", "Not_A Brand";v="24"',
        'sec-ch-ua-mobile': '?0',
        'sec-ch-ua-platform': '"Windows"'
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
                // Using networkidle2 for more stability, and a shorter timeout per attempt to fail fast and retry
                await page.goto(CAMPAIGNS_URL, { waitUntil: 'networkidle2', timeout: 45000 });
                listLoaded = true;
            } catch (e: any) {
                listRetries++;
                const backoff = Math.min(listRetries * 5000, 30000);
                console.log(`      ⚠️  List load attempt ${listRetries}/${maxListRetries} failed: ${e.message}. Retrying in ${backoff / 1000}s...`);
                await sleep(backoff);
                // Rotate UA on retry
                await page.setUserAgent(userAgents[listRetries % userAgents.length]);
            }
        }

        if (!listLoaded) throw new Error(`Could not load campaign list after ${maxListRetries} attempts`);

        await sleep(3000); // Small wait after networkidle2 

        // 🔥 GEÇMİŞ KAMPANYALAR BÖLÜMÜNÜ GİZLE
        try {
            await page.evaluate(() => {
                const pastSections = document.querySelectorAll('[class*="past"], [class*="gecmis"], [class*="arsiv"], [id*="past"], [id*="gecmis"]');
                pastSections.forEach(section => (section as HTMLElement).style.display = 'none');
            });
            console.log('   -> Geçmiş kampanyalar bölümü gizlendi');
        } catch (e) {
            console.log('   -> Geçmiş kampanyalar bölümü bulunamadı (normal)');
        }

        // --- INFINITE SCROLL LOGIC ---
        let hasMore = true;
        while (hasMore) {
            try {
                // Find and click "Daha Fazla" button
                const btnFound = await page.evaluate(() => {
                    const btns = Array.from(document.querySelectorAll('button'));
                    const loadMore = btns.find(b => b.innerText.includes('Daha Fazla'));
                    if (loadMore) {
                        loadMore.scrollIntoView({ behavior: 'smooth', block: 'center' });
                        return true;
                    }
                    return false;
                });

                if (btnFound) {
                    await sleep(1000);
                    // Re-implement robust click
                    const clicked = await page.evaluate(() => {
                        const btns = Array.from(document.querySelectorAll('button'));
                        const loadMore = btns.find(b => b.innerText.includes('Daha Fazla'));
                        if (loadMore) {
                            (loadMore as HTMLElement).click();
                            return true;
                        }
                        return false;
                    });

                    if (clicked) {
                        process.stdout.write('.');
                        await sleep(3000); // Wait for content load
                    } else {
                        hasMore = false;
                    }
                } else {
                    console.log('\n      ✅ All list loaded.');
                    hasMore = false;
                }
            } catch (e) {
                hasMore = false;
            }
        }

        // --- EXTRACT LINKS ---
        const content = await page.content();
        const $ = cheerio.load(content);
        let allLinks: string[] = [];

        // Category keywords to exclude (pages that end with these are category lists)
        const categorySuffixes = [
            '-kampanyalari',
            '-kampanyalar',
            'premium-kampanyalar',
            'tum-kampanyalar',
        ];

        // Specific category paths that are NOT real campaigns
        const categoryPaths = [
            '/kampanyalar/seyahat',
            '/kampanyalar/turizm',
            '/kampanyalar/akaryakit',
            '/kampanyalar/giyim-aksesuar',
            '/kampanyalar/market',
            '/kampanyalar/elektronik',
            '/kampanyalar/beyaz-esya',
            '/kampanyalar/mobilya-dekorasyon',
            '/kampanyalar/egitim-kirtasiye',
            '/kampanyalar/online-alisveris',
            '/kampanyalar/otomotiv',
            '/kampanyalar/vergi-odemeleri',
            '/kampanyalar/maximum-mobil',
            '/kampanyalar/diger',
            '/kampanyalar/yeme-icme',
            '/kampanyalar/maximum-pati-kart',
            '/kampanyalar/arac-kiralama',
            '/kampanyalar/bankamatik'
        ];

        $('a').each((_, el) => {
            const href = $(el).attr('href');
            // 🔥 GELİŞTİRİLMİŞ LİNK FİLTRESİ
            if (href && (href.includes('/kampanyalar/') || href.includes('kampanyalar/')) &&
                !href.toLowerCase().includes('arsiv') &&
                !href.toLowerCase().includes('gecmis') &&
                !href.toLowerCase().includes('past')) {
                const lowerHref = href.toLowerCase();

                // Skip if it's a known category path
                const isExactCategory = categoryPaths.some(path => lowerHref.endsWith(path));

                // Skip if it ends with category suffix
                const isCategorySuffix = categorySuffixes.some(suffix => lowerHref.endsWith(suffix));

                // Skip common non-campaign pages
                const isCommonPage = lowerHref.includes('ozellikler') || lowerHref.includes('basvuru') || lowerHref.endsWith('/kampanyalar');

                if (!isExactCategory && !isCategorySuffix && !isCommonPage && href.length > 25) {
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

        const cardNameForOptimization = 'Maximum';

        console.log(`   🔍 Optimizing campaign list via database check...`);
        const { urlsToProcess } = await optimizeCampaigns(uniqueLinks, cardNameForOptimization);

        const finalLinks = uniqueLinks.filter(url => urlsToProcess.includes(url)).slice(0, limit);
        console.log(`   🚀 Processing details for ${finalLinks.length} campaigns (skipping ${uniqueLinks.length - finalLinks.length} complete/existing)...\n`);

        let count = 0;
        for (const url of finalLinks) {
            console.log(`   🔍 Processing [${count + 1}/${Math.min(uniqueLinks.length, limit)}]: ${url}`);
            if (count >= limit) break;

            try {
                await sleep(5000 + Math.random() * 3000); // Increased delay between campaigns (5-8s)

                // Improved retry logic for detail page
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

                // 🔥 VISUAL V7 TACTIC: SCROLL
                await page.evaluate(() => window.scrollTo(0, 600));
                await sleep(500);

                // Wait for description (optional presence check like in Python)
                try {
                    await page.waitForSelector("span[id$='CampaignDescription']", { timeout: 5000 });
                } catch { }

                const detailContent = await page.content();
                const $d = cheerio.load(detailContent);

                const titleEl = $d('h1.gradient-title-text').first() || $d('h1').first();
                const title = cleanText(titleEl.text() || "Başlık Yok");

                if (trLower(title).includes('geçmiş') || title.length < 10) continue;

                // Dates
                const dateEl = $d("span[id$='KampanyaTarihleri']");
                const dateText = cleanText(dateEl.text());
                const validUntil = formatDateIso(dateText, true);

                if (validUntil && new Date(validUntil) < new Date()) continue; // Expired

                // Description & Conditions
                const descEl = $d("span[id$='CampaignDescription']");
                let conditions: string[] = [];
                let fullText = "";

                if (descEl.length > 0) {
                    // Mimic Python's br -> newline replacement
                    descEl.find('br').replaceWith('\n');
                    descEl.find('p').prepend('\n');
                    const rawText = descEl.text();
                    conditions = rawText.split('\n').map(line => cleanText(line)).filter(l => l.length > 15);
                    fullText = conditions.join(' ');
                } else {
                    fullText = cleanText($d.text());
                    conditions = fullText.split('\n').filter(t => t.length > 20);
                }

                // 🔥 VISUAL V7 TACTIC: ID SELECTOR FOR IMAGE
                let image = "";
                const imgEl = $d("img[id$='CampaignImage']");
                if (imgEl.length > 0) {
                    const src = imgEl.attr('src');
                    if (src) {
                        const imageUrl = src.startsWith('http') ? src : `${BASE_URL}${src}`;
                        // 🔥 AXIOS + CLEAN CAPTURE FALLBACK
                        image = await downloadImageDirectly(imageUrl, title, 'maximum', page);
                    }
                }

                // ID and Card Normalization
                // We always want these to be under 'Maximum' brand for the site filter
                const normalizedCardNameVal = 'Maximum';

                // 🔥 AI PARSING (Gemini Engine)
                // Sending FULL page text to ensure Brand/Sector context (often in breadcrumbs/header)
                const fullPageText = cleanText($d.text());

                const campaignHtml = `
                    <h1>${title}</h1>
                    <div class="dates">${dateText}</div>
                    <div class="full-text-context">${fullPageText}</div>
                    <img src="${image}" />
                `;

                const campaignData = await parseWithGemini(campaignHtml, url, bankName, normalizedCardNameVal);

                if (campaignData) {
                    // Force critical original fields
                    campaignData.title = title;
                    campaignData.slug = generateCampaignSlug(title); // Regenerate slug
                    campaignData.image = image;
                    campaignData.image_url = image;
                    campaignData.bank = bankName;
                    campaignData.card_name = normalizedCardNameVal; // Always 'Maximum' for site filter
                    campaignData.url = url;
                    campaignData.reference_url = url;
                    campaignData.is_active = true;

                    // Standard Post-Processing
                    syncEarningAndDiscount(campaignData);
                    campaignData.publish_status = 'processing';
                    campaignData.publish_updated_at = new Date().toISOString();
                    campaignData.image_migrated = false; // Bridge flag for Cloudflare migration

                    // Lookup IDs for normalized bank/card
                    const ids = await lookupIDs(
                        campaignData.bank,
                        campaignData.card_name,
                        campaignData.brand,
                        campaignData.sector_slug,
                        campaignData.category
                    );

                    // Force specific IDs for İş Bankası Maximum to ensure they appear in the right filter
                    campaignData.bank_id = ids.bank_id || 'is-bankasi';
                    campaignData.card_id = ids.card_id || 'maximum';
                    if (ids.brand_id) campaignData.brand_id = ids.brand_id;
                    if (ids.sector_id) campaignData.sector_id = ids.sector_id;

                    // Badges
                    const badge = assignBadge(campaignData);
                    campaignData.badge_text = badge.text;
                    campaignData.badge_color = badge.color;

                    markGenericBrand(campaignData);

                    campaignData.tags = campaignData.tags || [];


                    count++;
                    console.log(`      [${count}] ${title.substring(0, 35)}... (Img: ${image ? '✅' : '❌'})`);

                    // ID-BASED SLUG SYSTEM
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

        console.log(`\n✅ TS Scraper Finished. Processed ${count} campaigns.`);

    } catch (e: any) {
        console.error('❌ Critical Error:', e);
    } finally {
        await browser.close();
    }
}

if (require.main === module) {
    runMaximumScraperTS();
}
