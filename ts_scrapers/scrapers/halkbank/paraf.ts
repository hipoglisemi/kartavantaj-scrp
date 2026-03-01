
import puppeteer from 'puppeteer-extra';
import StealthPlugin from 'puppeteer-extra-plugin-stealth';
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

puppeteer.use(StealthPlugin());

dotenv.config();

const supabase = createClient(
    process.env.SUPABASE_URL!,
    process.env.SUPABASE_ANON_KEY!
);

const BASE_URL = 'https://www.paraf.com.tr';
const CAMPAIGNS_URL = 'https://www.paraf.com.tr/tr/kampanyalar.html';

const sleep = (ms: number) => new Promise(resolve => setTimeout(resolve, ms));

async function runParafScraper() {
    console.log('\n💳 Paraf (Halkbank)');
    const normalizedBank = await normalizeBankName('Halkbank');
    const normalizedCard = await normalizeCardName(normalizedBank, 'Paraf');
    console.log(`   Bank: ${normalizedBank}, Card: ${normalizedCard}`);

    const args = process.argv.slice(2);
    const limitArg = args.find(arg => arg.startsWith('--limit='));
    const limit = limitArg ? parseInt(limitArg.split('=')[1]) : 999;
    const isAIEnabled = args.includes('--ai');

    const browser = await puppeteer.launch({
        headless: true,
        args: [
            '--no-sandbox',
            '--disable-setuid-sandbox',
            '--disable-dev-shm-usage',
            '--disable-blink-features=AutomationControlled'
        ]
    });

    try {
        const page = await browser.newPage();
        await page.setViewport({ width: 1920, height: 1080 });
        await page.setUserAgent('Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36');

        // 1. Load campaign list and click "Load More"
        console.log(`   📄 Loading ${CAMPAIGNS_URL}...`);
        await page.goto(CAMPAIGNS_URL, { waitUntil: 'networkidle2', timeout: 60000 });

        // Wait for campaign list
        try {
            await page.waitForSelector('.cmp-list--campaigns', { timeout: 20000 });
        } catch (e) {
            console.log('   ⚠️ Warning: .cmp-list--campaigns not found within timeout. Page might have loaded differently.');
        }

        // Click "Load More" button multiple times
        let clickCount = 0;
        const maxClicks = 30;

        while (clickCount < maxClicks) {
            try {
                // Selector update: some buttons might be nested or have slightly different classes
                // Verified current: .button--more-campaign a
                const loadMoreBtn = await page.$('.button--more-campaign a, .button--more-campaign .cmp-button');
                if (!loadMoreBtn) break;

                const isVisible = await loadMoreBtn.evaluate(el => {
                    const style = window.getComputedStyle(el);
                    return style && style.display !== 'none' && style.visibility !== 'hidden' && el.offsetHeight > 0;
                });

                if (!isVisible) break;

                await loadMoreBtn.scrollIntoView();
                await sleep(1000);
                await loadMoreBtn.click();
                await sleep(3000); // Increased wait for dynamic content
                clickCount++;
                console.log(`   -> Clicked 'Load More' (${clickCount})`);
            } catch (e) {
                console.log(`   -> Stopping 'Load More' after ${clickCount} clicks due to error or missing button.`);
                break;
            }
        }

        // Extract campaign links
        const campaignLinks = await page.evaluate((baseUrl) => {
            const links: string[] = [];
            // Primary selector from reference
            const anchors = document.querySelectorAll('.cmp-list--campaigns .cmp-teaser__title a, .cmp-teaser__title a');
            anchors.forEach(a => {
                const href = (a as HTMLAnchorElement).getAttribute('href');
                if (href && (href.includes('/kampanyalar/') || href.includes('/content/parafcard/'))) {
                    if (href.endsWith('kampanyalar.html')) return;
                    const fullUrl = href.startsWith('http') ? href : baseUrl + (href.startsWith('/') ? '' : '/') + href;
                    if (!links.includes(fullUrl)) {
                        links.push(fullUrl);
                    }
                }
            });
            return links;
        }, BASE_URL);

        console.log(`\n   🎉 Found ${campaignLinks.length} campaigns.`);

        if (campaignLinks.length === 0) {
            console.log('   ❌ No campaign links found. Check selectors!');
            return;
        }

        // Apply limit
        const limitedLinks = limit ? campaignLinks.slice(0, limit) : campaignLinks;
        console.log(`   Processing first ${limitedLinks.length}...`);

        // 2. Optimize
        const { urlsToProcess } = await optimizeCampaigns(limitedLinks, normalizedCard);

        // 3. Process each campaign
        for (const fullUrl of urlsToProcess) {
            console.log(`\n   🔍 Processing: ${fullUrl}`);

            try {
                await page.goto(fullUrl, { waitUntil: 'networkidle2', timeout: 30000 });
                await sleep(1000);

                // Wait for title
                try {
                    await page.waitForSelector('h1', { timeout: 10000 });
                } catch { }

                const html = await page.content();

                // Extract fallback data
                const fallbackData = await page.evaluate((baseUrl) => {
                    const titleEl = document.querySelector('.master-banner__content h1') || document.querySelector('h1');
                    const title = titleEl ? titleEl.textContent?.trim() : 'Başlıksız Kampanya';

                    // Extract image
                    let image: string | null = null;
                    const bannerDiv = document.querySelector('.master-banner__image') as HTMLElement;
                    if (bannerDiv && bannerDiv.style.backgroundImage) {
                        const match = bannerDiv.style.backgroundImage.match(/url\(['"]?(.*?)['"]?\)/);
                        if (match && !match[1].includes('logo.svg')) {
                            const imgPath = match[1];
                            image = imgPath.startsWith('http') ? imgPath : baseUrl + (imgPath.startsWith('/') ? '' : '/') + imgPath;
                        }
                    }

                    if (!image) {
                        const imgs = Array.from(document.querySelectorAll('img'));
                        for (const img of imgs) {
                            const src = img.getAttribute('src') || img.getAttribute('data-src');
                            if (src && !src.includes('logo') && !src.includes('icon') && !src.includes('.svg')) {
                                if (src.includes('/content/') || src.includes('kampanyalar')) {
                                    image = src.startsWith('http') ? src : baseUrl + (src.startsWith('/') ? '' : '/') + src;
                                    break;
                                }
                            }
                        }
                    }

                    if (!image) {
                        image = 'https://www.paraf.com.tr/content/dam/parafcard/paraf-logos/paraf-logo-yeni.png';
                    }

                    return { title, image };
                }, BASE_URL);

                let campaignData: any;

                if (isAIEnabled) {
                    campaignData = await parseWithGemini(html, fullUrl, normalizedBank, normalizedCard);

                    // Fallback title if AI missed it
                    if (!campaignData.title && fallbackData.title) {
                        campaignData.title = fallbackData.title;
                    }

                    // Fallback image if AI missed it
                    if (!campaignData.image && fallbackData.image) {
                        campaignData.image = fallbackData.image;
                        console.log('      🔧 Used fallback image');
                    }

                    // Paraf-specific: Default participation method if missing
                    if (!campaignData.participation_method) {
                        if (html.includes('Paraf Mobil') || html.includes('Halkbank Mobil') || html.includes('HEMEN KATIL')) {
                            campaignData.participation_method = "Paraf Mobil veya Halkbank Mobil uygulamasından 'Hemen Katıl' butonuna tıklayın";
                        } else if (html.includes('3404')) {
                            const smsMatch = html.match(/([A-Z0-9]{3,})\s*yazıp\s*3404/i);
                            if (smsMatch) {
                                campaignData.participation_method = `SMS (${smsMatch[1].toUpperCase()} -> 3404)`;
                            }
                        }
                    }
                } else {
                    // No AI mode
                    campaignData = {
                        title: fallbackData.title,
                        description: fallbackData.title,
                        image: fallbackData.image,
                        category: 'Diğer',
                        sector_slug: 'genel',
                        card_name: normalizedCard,
                        bank: normalizedBank,
                        url: fullUrl,
                        reference_url: fullUrl,
                        is_active: true,
                        tags: []
                    };
                }

                if (campaignData) {
                    // Ensure critical fields
                    campaignData.card_name = normalizedCard;
                    campaignData.bank = normalizedBank;
                    campaignData.url = fullUrl;
                    campaignData.reference_url = fullUrl;
                    if (!campaignData.sector_slug) campaignData.sector_slug = 'genel';

                    syncEarningAndDiscount(campaignData);
                    campaignData.publish_status = 'processing';
                    campaignData.publish_updated_at = new Date().toISOString();

                    // Set default min_spend
                    if (campaignData.min_spend === undefined || campaignData.min_spend === null) {
                        campaignData.min_spend = 0;
                    }

                    // Lookup IDs
                    const idsResult = await lookupIDs(
                        campaignData.bank,
                        campaignData.card_name,
                        campaignData.brand,
                        campaignData.sector_slug,
                        campaignData.category
                    );
                    Object.assign(campaignData, idsResult);

                    // Assign badge
                    const badgeResult = assignBadge(campaignData);
                    campaignData.badge_text = badgeResult.text;
                    campaignData.badge_color = badgeResult.color;

                    // Mark generic brand
                    markGenericBrand(campaignData);

                    campaignData.tags = campaignData.tags || [];


                    // Upsert to database

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
                            console.log(`      ✅ Updated: ${campaignData.title} (${finalSlug})`);
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
                            console.log(`      ✅ Inserted: ${campaignData.title} (${finalSlug})`);
                        }
                    }
                }

            } catch (error: any) {
                console.error(`      ❌ Error processing: ${error.message}`);
                continue;
            }
        }

        console.log('\n✅ Scraper finished.');

    } catch (error: any) {
        console.error(`❌ Fatal error: ${error.message}`);
        process.exit(1);
    } finally {
        await browser.close();
    }
}

runParafScraper();
