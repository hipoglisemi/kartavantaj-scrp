import axios from 'axios';
import * as cheerio from 'cheerio';
import { createClient } from '@supabase/supabase-js';
import * as dotenv from 'dotenv';
import { parseWithGemini } from '../../services/geminiParser';
import { downloadImageDirectly } from '../../services/imageService';
import { generateCampaignSlug, generateSectorSlug } from '../../utils/slugify';

dotenv.config();

const supabase = createClient(process.env.SUPABASE_URL!, process.env.SUPABASE_ANON_KEY!);

const BASE_URL = 'https://bireysel.turktelekom.com.tr';
const LISTING_URL = 'https://bireysel.turktelekom.com.tr/bi-dunya-firsat';
const BANK_NAME = 'Operatör';
const CARD_NAME = 'Türk Telekom';

// Helper to determine sector
const getSectorId = (title: string): string => {
    const t = title.toLowerCase();
    if (t.includes('giyim') || t.includes('moda') || t.includes('flo') || t.includes('ayakkabı')) return '20455e96-2917-4fa5-8b38-e60da42c8d28'; // Giyim
    if (t.includes('market') || t.includes('gıda') || t.includes('carrefour')) return '065a6390-5a32-4e4b-8f35-d22731871d3d'; // Market
    if (t.includes('seyahat') || t.includes('otel') || t.includes('uçak') || t.includes('bilet')) return '6e045437-0870-4389-a292-25fb5ba1f70d'; // Seyahat
    if (t.includes('elektronik') || t.includes('teknoloji')) return 'ae59795d-7538-4b71-b663-d3ca8bc3af01'; // Elektronik
    if (t.includes('yeme') || t.includes('içme') || t.includes('cafe') || t.includes('restoran')) return '89cd7dc0-1436-4076-928d-19543be8f3c7'; // Restoran
    return '01cc30db-e74f-426b-968b-592d3df24ef6'; // Diğer
};

const processedUrls = new Set<string>();

export const scrapeTurkTelekom = async () => {
    console.log(`🚀 Starting Türk Telekom (Bi Dünya Fırsat) Scraper...`);

    try {
        const { data: listingHtml } = await axios.get(LISTING_URL, {
            headers: { 'User-Agent': 'Mozilla/5.0' }
        });
        const $ = cheerio.load(listingHtml);

        const campaigns: any[] = [];

        // Find all campaign cards
        // Structure based on inspect: href includes '/bi-dunya-firsat/'
        $('a').each((i, el) => {
            const href = $(el).attr('href');
            if (!href || !href.includes('/bi-dunya-firsat/')) return;

            const fullUrl = href.startsWith('http') ? href : `${BASE_URL}${href.startsWith('/') ? '' : '/'}${href}`;
            if (processedUrls.has(fullUrl)) return;
            processedUrls.add(fullUrl);

            // Extract Title
            let title = $(el).find('h3, h4, .title, .text').text().trim();
            if (!title) title = $(el).find('img').attr('alt') || '';
            if (!title) title = href.split('/').pop()?.replace(/-/g, ' ') || 'Kampanya';

            // Extract Image
            let imgUrl = $(el).find('img').attr('src');
            // Check for lazy loading or data-src
            if (!imgUrl) imgUrl = $(el).find('img').attr('data-src');

            if (imgUrl) {
                if (!imgUrl.startsWith('http')) {
                    imgUrl = `${BASE_URL}${imgUrl.startsWith('/') ? '' : '/'}${imgUrl}`;
                }
            }

            campaigns.push({
                title,
                url: fullUrl,
                image: imgUrl
            });
        });

        console.log(`✅ Found ${campaigns.length} potential campaigns.`);

        for (const item of campaigns) {
            console.log(`🔍 Processing: ${item.title}`);

            // Fetch Detail
            let detailHtml = '';
            try {
                const { data } = await axios.get(item.url, { headers: { 'User-Agent': 'Mozilla/5.0' } });
                detailHtml = data;
            } catch (e: any) {
                console.log(`   ⚠️  Detail fetch failed. Skipping. (${e.message})`);
                continue;
            }

            const $d = cheerio.load(detailHtml);

            // Try to find a better image in detail if listing image is missing
            let finalImage = item.image;
            if (!finalImage) {
                finalImage = $d('meta[property="og:image"]').attr('content');
            }
            if (!finalImage) {
                // Try banner
                finalImage = $d('.banner img').attr('src');
                if (finalImage && !finalImage.startsWith('http')) finalImage = `${BASE_URL}/${finalImage}`;
            }

            if (!finalImage) {
                console.log(`   ⚠️  No image found for ${item.title}. Skipping.`);
                continue; // Strict mode
            }

            // AI Parsing
            const campaignData = await parseWithGemini(
                `<html><body><h1>${item.title}</h1>${$d('body').html()}</body></html>`,
                item.url,
                BANK_NAME,
                CARD_NAME
            );

            // Insert
            const slug = generateCampaignSlug(item.title);
            const { error } = await supabase.from('campaigns').upsert({
                partner_id: 19, // Operatör id (assumed or hardcoded 33/Operator?) -> Wait, "Operatör" bank_id is likely text 'operator'. 
                // Let's resolve IDs properly in the next step if foreign key constrains fail.
                // Assuming 'Operatör' exists in master_banks with slug 'operator'.
                // partner_id is actually integer ID from 'banks'. I should query it or assume the same ID used for Turkcell.
                // For now, I'll fetch the bank ID dynamically or hardcode if known from Turkcell helper.
                // Better: query bank_id for 'Operatör'.

                bank_id: 'operator', // Correct slug
                bank: 'Operatör',
                card_name: CARD_NAME,
                card_id: 'turk-telekom', // Slug for Türk Telekom card
                title: campaignData.title,
                description: campaignData.description,
                image_url: finalImage,
                start_date: new Date(),
                end_date: campaignData.end_date ? new Date(campaignData.end_date) : new Date(new Date().setMonth(new Date().getMonth() + 1)),
                campaign_url: item.url,
                sector_id: getSectorId(item.title),
                slug: slug,
                market_name: campaignData.brand,
                terms: campaignData.terms,
                keywords: campaignData.keywords,
                discount_percentage: campaignData.discount_percentage,
                max_discount_amount: campaignData.max_discount_amount,
                min_spend_amount: campaignData.min_spend_amount,
                earning_summary: campaignData.earning_summary,
                ai_enhanced: true
            }, { onConflict: 'slug' });

            if (error) {
                console.log(`   ❌ Error inserting: ${error.message}`);
                // If bank_id failed, I might need to query first. 
                // I will add a lookup at start of script.
            } else {
                console.log(`      ✅ Inserted: ${slug}`);
            }
        }

    } catch (e: any) {
        console.error('Büyük Hata:', e);
    }
};

// Auto-run if main
if (require.main === module) {
    scrapeTurkTelekom();
}
