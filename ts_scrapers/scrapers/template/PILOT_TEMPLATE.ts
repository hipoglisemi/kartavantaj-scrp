
import puppeteer from 'puppeteer-extra';
import StealthPlugin from 'puppeteer-extra-plugin-stealth';
import { createClient } from '@supabase/supabase-js';
import * as dotenv from 'dotenv';
import { parseWithGemini } from '../../services/geminiParser';
import { generateCampaignSlug } from '../../utils/slugify';
import { syncEarningAndDiscount } from '../../utils/dataFixer';
import { normalizeBankName, normalizeCardName } from '../../utils/bankMapper';
import { optimizeCampaigns } from '../../utils/campaignOptimizer';
import { lookupIDs } from '../../utils/idMapper';
import { downloadImageDirectly } from '../../services/imageService';
import { assignBadge } from '../../services/badgeAssigner';
import { markGenericBrand } from '../../utils/genericDetector';

// --- CONFIGURATION ---
puppeteer.use(StealthPlugin());
dotenv.config();

const supabase = createClient(
    process.env.SUPABASE_URL!,
    process.env.SUPABASE_ANON_KEY!
);

// TODO: Update these for your specific bank/card
const CONFIG = {
    bankName: 'İş Bankası', // Must match master list
    cardName: 'Maximum',    // Must match master list
    baseUrl: 'https://www.maximum.com.tr',
    listUrl: 'https://www.maximum.com.tr/kampanyalar',
    selectors: {
        campaignCard: '.campaign-card, .card', // Selector for items in listing
        title: 'h1.page-title, h1',            // Selector for title in detail page
        image: '.campaign-first-image img',    // Selector for image in detail page
    }
};

const sleep = (ms: number) => new Promise(resolve => setTimeout(resolve, ms));

async function runPilotScraper() {
    console.log(`🚀 Starting Pilot Scraper Template [${CONFIG.bankName} - ${CONFIG.cardName}]...`);

    const limitArg = process.argv.find(arg => arg.startsWith('--limit='));
    const limit = limitArg ? parseInt(limitArg.split('=')[1]) : 1000;
    const isAIEnabled = process.argv.includes('--ai');

    // 1. Launch Browser (Stealth)
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
        await page.setViewport({ width: 1400, height: 900 });
        await page.setUserAgent('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36');

        // 2. Load Campaign List
        console.log(`   🔍 Loading Campaign List: ${CONFIG.listUrl}...`);
        await page.goto(CONFIG.listUrl, { waitUntil: 'networkidle2', timeout: 60000 });

        // --- OPTIONAL: INFINITE SCROLL ---
        console.log('   🖱️  Starting infinite scroll...');
        let previousHeight = await page.evaluate('document.body.scrollHeight');
        for (let i = 0; i < 10; i++) { // Limit scroll attempts
            await page.evaluate('window.scrollTo(0, document.body.scrollHeight)');
            await sleep(3000);
            let currentHeight = await page.evaluate('document.body.scrollHeight');
            if (currentHeight === previousHeight) break;
            previousHeight = currentHeight;
            process.stdout.write('.');
        }
        console.log('\n   ✅ Scroll finished.');

        // 3. Extract Links
        const rawLinks = await page.evaluate((config) => {
            const anchors = Array.from(document.querySelectorAll('a'));
            return anchors
                .map(a => a.href)
                .filter(href => href && (href.includes('/kampanyalar/') || href.includes('kampanya-detay')));
        }, CONFIG);

        const uniqueLinks = [...new Set(rawLinks)];
        console.log(`   🎉 Found ${uniqueLinks.length} total potential campaigns.`);

        // 4. Normalization & Optimization
        const normalizedBank = await normalizeBankName(CONFIG.bankName);
        const normalizedCard = await normalizeCardName(normalizedBank, CONFIG.cardName);

        // Skip campaigns already in DB (to save Gemini costs)
        const { urlsToProcess } = await optimizeCampaigns(uniqueLinks, normalizedCard);
        const finalLinks = uniqueLinks.filter(url => urlsToProcess.includes(url)).slice(0, limit);

        console.log(`   🚀 Processing ${finalLinks.length} campaigns (skipping ${uniqueLinks.length - finalLinks.length} existing)...\n`);

        // 5. Process Detail Pages
        let count = 0;
        for (const url of finalLinks) {
            console.log(`   🔍 [${count + 1}/${finalLinks.length}] Processing: ${url}`);

            try {
                await page.goto(url, { waitUntil: 'networkidle2', timeout: 30000 });
                await sleep(2000);

                const html = await page.content();

                // Extraction via Evaluation (Fallback/Base data)
                const baseInfo = await page.evaluate((config) => {
                    const title = document.querySelector(config.selectors.title)?.textContent?.trim() || "Başlık Yok";
                    const imgEl = document.querySelector(config.selectors.image) as HTMLImageElement;
                    const rawImg = imgEl?.src || "";
                    return { title, rawImg };
                }, CONFIG);

                // --- AI PARSING ---
                let campaignData: any;
                if (isAIEnabled) {
                    campaignData = await parseWithGemini(html, url, normalizedBank, normalizedCard);
                } else {
                    campaignData = { title: baseInfo.title, description: baseInfo.title, category: 'Genel' };
                }

                if (!campaignData) continue;

                // --- IMAGE HANDLING (CLOUDFLARE) ---
                // Always use imageService for Cloudflare upload to avoid Supabase egress!
                let image = "";
                if (baseInfo.rawImg) {
                    const imageUrl = baseInfo.rawImg.startsWith('http') ? baseInfo.rawImg : `${CONFIG.baseUrl}${baseInfo.rawImg}`;
                    image = await downloadImageDirectly(imageUrl, baseInfo.title, normalizedCard.toLowerCase());
                }

                // --- DATA STANDARDIZATION ---
                campaignData.title = baseInfo.title;
                campaignData.image = image;
                campaignData.image_url = image;
                campaignData.bank = normalizedBank;
                campaignData.card_name = normalizedCard;
                campaignData.url = url;
                campaignData.reference_url = url;
                campaignData.is_active = true;

                syncEarningAndDiscount(campaignData);
                campaignData.publish_status = 'processing';
                campaignData.publish_updated_at = new Date().toISOString();

                // Lookup master IDs
                const ids = await lookupIDs(
                    campaignData.bank,
                    campaignData.card_name,
                    campaignData.brand,
                    campaignData.sector_slug,
                    campaignData.category
                );
                Object.assign(campaignData, ids);

                // Badges & Generic Brand
                const badge = assignBadge(campaignData);
                campaignData.badge_text = badge.text;
                campaignData.badge_color = badge.color;
                markGenericBrand(campaignData);

                // --- DATABASE UPSERT ---
                const { data: existing } = await supabase
                    .from('campaigns')
                    .select('id')
                    .eq('reference_url', url)
                    .single();

                if (existing) {
                    const finalSlug = generateCampaignSlug(campaignData.title, existing.id);
                    await supabase.from('campaigns').update({ ...campaignData, slug: finalSlug }).eq('id', existing.id);
                    console.log(`      ✅ Updated: ${finalSlug}`);
                } else {
                    const { data: inserted } = await supabase.from('campaigns').insert(campaignData).select('id').single();
                    if (inserted) {
                        const finalSlug = generateCampaignSlug(campaignData.title, inserted.id);
                        await supabase.from('campaigns').update({ slug: finalSlug }).eq('id', inserted.id);
                        console.log(`      ✅ Inserted: ${finalSlug}`);
                    }
                }

                count++;
                await sleep(2000); // Respect the server
            } catch (e: any) {
                console.error(`      ❌ Error: ${e.message}`);
            }
        }

        console.log(`\n✅ Pilot Scraper Finished. ${count} campaigns processed.`);

    } catch (e: any) {
        console.error('❌ Critical Error:', e);
    } finally {
        await browser.close();
    }
}

if (require.main === module) {
    runPilotScraper();
}
