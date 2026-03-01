import puppeteer from 'puppeteer-extra';
import StealthPlugin from 'puppeteer-extra-plugin-stealth';
import * as fs from 'fs';

puppeteer.use(StealthPlugin());

const HAPPY_CARD_URL = 'https://www.happycard.com.tr/kampanyalar/Sayfalar/default.aspx';
const ALA_CARD_URL = 'https://www.turkiyefinansala.com/tr-tr/kampanyalar/Sayfalar/default.aspx';

const HAPPY_OUTPUT = '/tmp/happycard_campaign_links.txt';
const ALA_OUTPUT = '/tmp/ala_card_campaign_links.txt';

async function extractLinks(url: string, output: string, name: string) {
    console.log(`🚀 Starting link extraction for ${name}...`);
    const browser = await puppeteer.launch({
        headless: true,
        // @ts-ignore
        ignoreHTTPSErrors: true,
        args: [
            '--no-sandbox',
            '--disable-setuid-sandbox',
            '--ignore-certificate-errors',
            '--ignore-certificate-errors-spki-list'
        ]
    });

    try {
        const page = await browser.newPage();

        // Block images/fonts to speed up
        await page.setRequestInterception(true);
        page.on('request', (req) => {
            if (['image', 'font', 'stylesheet'].includes(req.resourceType())) {
                req.abort();
            } else {
                req.continue();
            }
        });

        console.log(`   Navigating to ${url}...`);
        await page.goto(url, { waitUntil: 'networkidle2', timeout: 60000 });

        // Scroll to load dynamic content (Owl Carousel / Infinite Scroll)
        console.log('   Scrolling to load all campaigns...');
        await page.evaluate(async () => {
            const workspace = document.getElementById('s4-workspace');
            if (workspace) {
                workspace.scrollTop = workspace.scrollHeight;
            } else {
                window.scrollTo(0, document.body.scrollHeight);
            }
            // Wait a bit for lazy loading
            await new Promise(resolve => setTimeout(resolve, 3000));
        });

        // Extract links
        console.log('   Extracting links...');
        const links = await page.evaluate(() => {
            // @ts-ignore
            const anchors = Array.from(document.querySelectorAll('a[href*="/kampanyalar/Sayfalar/"]'));
            return anchors
                .map(a => a.getAttribute('href'))
                .filter(href => href && !href.includes('default.aspx') && !href.includes('KAMPANYALAR.aspx'))
                // @ts-ignore
                .map(href => new URL(href, window.location.origin).href);
        });

        const uniqueLinks = [...new Set(links)];
        console.log(`   ✅ Found ${uniqueLinks.length} unique campaigns.`);

        // Detect if extraction failed to find dynamic content (fallback check)
        if (uniqueLinks.length < 10) {
            console.warn('   ⚠️  Warning: Found fewer campaigns than expected. Dynamic loading might have failed.');
        }

        fs.writeFileSync(output, uniqueLinks.join('\n'));
        console.log(`   💾 Saved to ${output}`);

    } catch (error: any) {
        console.error(`   ❌ Error extracting ${name}:`, error.message);
    } finally {
        await browser.close();
    }
}

async function run() {
    await extractLinks(HAPPY_CARD_URL, HAPPY_OUTPUT, 'Happy Card');
    await extractLinks(ALA_CARD_URL, ALA_OUTPUT, 'ALA Card');
}

run();
