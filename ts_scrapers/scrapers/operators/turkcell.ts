import axios from 'axios';
import * as cheerio from 'cheerio';
import { createClient } from '@supabase/supabase-js';
import * as dotenv from 'dotenv';
import { parseWithGemini } from '../../services/geminiParser';
import { generateCampaignSlug, generateSectorSlug } from '../../utils/slugify';

dotenv.config();

const supabase = createClient(process.env.SUPABASE_URL!, process.env.SUPABASE_ANON_KEY!);

const CAMPAIGNS_URL = 'https://www.turkcell.com.tr/kampanyalar/marka-kampanyalari/marka-kampanyalari';
const BASE_URL = 'https://www.turkcell.com.tr';
const BANK_NAME = 'Operatör';
const CARD_NAME = 'Turkcell';

// Helper to determine sector
const getSectorId = (title: string): string => {
    const t = title.toLowerCase();
    if (t.includes('giyim') || t.includes('moda') || t.includes('flo') || t.includes('defacto') || t.includes('instreet') || t.includes('madame')) return '20455e96-2917-4fa5-8b38-e60da42c8d28'; // Giyim
    if (t.includes('market') || t.includes('gıda') || t.includes('carrefour') || t.includes('yemek') || t.includes('içecek')) return '065a6390-5a32-4e4b-8f35-d22731871d3d'; // Market
    if (t.includes('seyahat') || t.includes('otel') || t.includes('uçak') || t.includes('bilet') || t.includes('garenta') || t.includes('tiktak') || t.includes('uber')) return '6e045437-0870-4389-a292-25fb5ba1f70d'; // Seyahat
    if (t.includes('elektronik') || t.includes('teknoloji') || t.includes('mediamarkt') || t.includes('pronet') || t.includes('telekom')) return 'ae59795d-7538-4b71-b663-d3ca8bc3af01'; // Elektronik
    if (t.includes('eğlence') || t.includes('sinema') || t.includes('oyun') || t.includes('pasaj')) return '89cd7dc0-1436-4076-928d-19543be8f3c7'; // Eğlence/Restoran
    return '01cc30db-e74f-426b-968b-592d3df24ef6'; // Diğer
};

const processedUrls = new Set<string>();

// Cloudflare Images Configuration
const CF_ACCOUNT_ID = process.env.CLOUDFLARE_ACCOUNT_ID;
const CF_API_TOKEN = process.env.CLOUDFLARE_API_TOKEN;
const CF_ACCOUNT_HASH = process.env.NEXT_PUBLIC_CLOUDFLARE_ACCOUNT_HASH;

// Internal Image Downloader -> Cloudflare Upload
const downloadAndUploadToCloudflare = async (imageUrl: string, title: string): Promise<string> => {
    if (!imageUrl) return '';
    if (!CF_ACCOUNT_ID || !CF_API_TOKEN || !CF_ACCOUNT_HASH) {
        console.log('   ⚠️  Cloudflare credentials missing in .env. Falling back to original URL.');
        return imageUrl;
    }

    const imageId = `campaign-operator-${generateCampaignSlug(title).substring(0, 40)}-${Date.now()}`;

    try {
        console.log(`   🖼️  Downloading image for Cloudflare upload: ${imageUrl}`);
        const { data } = await axios.get(imageUrl, {
            responseType: 'arraybuffer',
            headers: {
                'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                'Referer': BASE_URL
            }
        });

        const formData = new FormData();
        // Construct a File-like object or Blob from the buffer for the upload
        const blob = new Blob([data], { type: 'image/jpeg' });
        formData.append('file', blob, 'image.jpg');
        formData.append('id', imageId);

        const response = await fetch(
            `https://api.cloudflare.com/client/v4/accounts/${CF_ACCOUNT_ID}/images/v1`,
            {
                method: 'POST',
                headers: {
                    Authorization: `Bearer ${CF_API_TOKEN}`,
                },
                body: formData,
            }
        );

        const cfData = await response.json();

        if (cfData.success) {
            const variant = 'public';
            return `https://imagedelivery.net/${CF_ACCOUNT_HASH}/${cfData.result.id}/${variant}`;
        } else {
            throw new Error(cfData.errors?.[0]?.message || 'Cloudflare upload failed');
        }

    } catch (e: any) {
        console.log(`   ⚠️  Cloudflare upload failed: ${e.message}. Using original URL.`);
        return imageUrl;
    }
};

export const scrapeTurkcell = async () => {
    console.log(`🚀 Starting Turkcell (Operatör) Scraper...`);
    console.log(`⚠️  Using reliable DOM parsing + JSON Extraction + Image Download.`);

    try {
        // 1. Fetch Listing Page
        const { data: listingHtml } = await axios.get(CAMPAIGNS_URL, {
            headers: {
                'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
            }
        });
        const $ = cheerio.load(listingHtml);

        const campaigns: any[] = [];

        // Find all campaign cards (.DynamicList_list__rxT5V a or similar)
        // We use a broader selector just in case classes changed (though unlikely in 1 hour)
        // Best selector from previous runs: .DynamicList_list__rxT5V a
        $('.DynamicList_list__rxT5V a').each((i, el) => {
            const href = $(el).attr('href');
            if (!href) return;

            const fullUrl = href.startsWith('http') ? href : `${BASE_URL}${href.startsWith('/') ? '' : '/'}${href}`;
            if (processedUrls.has(fullUrl)) return;
            processedUrls.add(fullUrl);

            // Extract Title
            let title = $(el).find('h3, h4').text().trim();
            if (!title) title = $(el).find('img').attr('alt') || '';

            // Extract Image (Listing)
            let image = $(el).find('img').attr('src');
            // Lazy load check
            if (!image) image = $(el).find('img').attr('data-src');

            // Fix relative image
            if (image && !image.startsWith('http')) {
                image = `${BASE_URL}${image.startsWith('/') ? '' : '/'}${image}`;
            }

            campaigns.push({
                title,
                url: fullUrl,
                image
            });
        });

        console.log(`✅ Found ${campaigns.length} listing items.`);

        for (const item of campaigns) {
            const { title, url } = item;
            console.log(`🔍 Processing: ${title}`);

            // Fetch Detail Page
            let detailHtml = '';
            let validUntil = new Date(new Date().setMonth(new Date().getMonth() + 1)).toISOString().split('T')[0];
            let imageUrl = item.image || '';
            let extendedContent = '';

            try {
                // Robust Headers (mimic browser)
                const { data } = await axios.get(url, {
                    headers: {
                        'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8',
                        'Referer': CAMPAIGNS_URL
                    }
                });
                detailHtml = data;

                // 1. Try __NEXT_DATA__ (Preferred)
                const $d = cheerio.load(detailHtml);
                const scriptContent = $d('#__NEXT_DATA__').html();

                if (scriptContent) {
                    try {
                        const json = JSON.parse(scriptContent);
                        // Traverse to find campaign object
                        let campaignObj: any = null;

                        const traverse = (o: any) => {
                            if (!o || typeof o !== 'object') return;
                            if (campaignObj) return;

                            // Check signature
                            if (o.campaignInfoText && o.criteriaHTML) {
                                campaignObj = o;
                                return;
                            }
                            // Fallback signature
                            if (o.title && o.image && o.image.image) {
                                campaignObj = o;
                                return;
                            }

                            for (const k in o) traverse(o[k]);
                        };
                        traverse(json.props?.pageProps);

                        if (campaignObj) {
                            // Extract Image
                            if (campaignObj.image?.image) {
                                const imgPath = campaignObj.image.image;
                                if (imgPath.startsWith('http')) {
                                    imageUrl = imgPath;
                                } else if (imgPath.startsWith('/SiteAssets') || imgPath.includes('merlincdn')) {
                                    imageUrl = `https://ffo3gv1cf3ir.merlincdn.net${imgPath.startsWith('/') ? '' : '/'}${imgPath}`;
                                } else {
                                    imageUrl = `https://www.turkcell.com.tr${imgPath.startsWith('/') ? '' : '/'}${imgPath}`;
                                }
                            }

                            // Extract Date
                            if (campaignObj.validityDate) {
                                validUntil = new Date(campaignObj.validityDate).toISOString().split('T')[0];
                            }

                            // Extract Rich Content
                            let parts = [];
                            if (campaignObj.campaignInfoText) parts.push(`<h3>${campaignObj.campaignInfoText}</h3>`);
                            if (campaignObj.criteriaHTML) parts.push(campaignObj.criteriaHTML);
                            if (campaignObj.faqList && Array.isArray(campaignObj.faqList)) {
                                parts.push('<h3>Sıkça Sorulan Sorular</h3>');
                                campaignObj.faqList.forEach((q: any) => {
                                    parts.push(`<h4>${q.question}</h4><div>${q.answer}</div>`);
                                });
                            }
                            if (campaignObj.buyThisConfigurationList) {
                                parts.push('<h3>Katılım Kanalları</h3>');
                                campaignObj.buyThisConfigurationList.forEach((opt: any) => {
                                    if (opt.buyingOptionHtml) parts.push(opt.buyingOptionHtml);
                                });
                            }

                            extendedContent = parts.join('\n');
                            detailHtml = `<html><body><h1>${title}</h1>${extendedContent}</body></html>`;
                            console.log('      ✅ Extracted rich data from JSON (Image + Dropdowns)');
                        }
                    } catch (jsonErr) {
                        console.log('      ⚠️  JSON Parse Error, falling back to DOM.');
                    }
                }

                if (!extendedContent) {
                    const content = $d('.campaign-detail-content').html() || '';
                    detailHtml = `<html><body><h1>${title}</h1>${content}</body></html>`;
                }

            } catch (e: any) {
                console.log(`   ⚠️  Detail fetch failed. Skipping campaign.`);
                continue;
            }

            if (!imageUrl) {
                console.log(`   ⚠️  No image found for ${title} even in JSON. Skipping.`);
                continue;
            }

            // --- IMAGE DOWNLOAD STEP ---
            const uploadedUrl = await downloadAndUploadToCloudflare(imageUrl, title);
            if (uploadedUrl) {
                imageUrl = uploadedUrl;
            } else {
                console.log(`   ⚠️  Image download failed. Skipping to avoid broken image.`);
                continue;
            }
            // ---------------------------

            // AI Parse
            // Ensure we pass bank: 'Operatör' and card: 'Turkcell' to parseWithGemini context?
            // Yes, standard args.
            const campaignData = await parseWithGemini(detailHtml, url, BANK_NAME, CARD_NAME);

            // Insert
            const slug = generateCampaignSlug(title);
            const { error: insertError } = await supabase.from('campaigns').upsert({
                bank_id: 'operator',
                bank: 'Operatör',
                card_name: CARD_NAME,
                card_id: 'turkcell',
                title: campaignData.title,
                description: campaignData.description,
                image_url: imageUrl,
                valid_from: new Date(),
                valid_until: campaignData.end_date ? new Date(campaignData.end_date) : new Date(validUntil),
                url: url,
                sector_id: getSectorId(title),
                slug: slug,
                brand: campaignData.brand,
                conditions: [campaignData.terms || extendedContent],
                keywords: campaignData.keywords,
                discount_percentage: campaignData.discount_percentage,
                max_discount_amount: campaignData.max_discount_amount,
                min_spend_amount: campaignData.min_spend_amount,
                earning_summary: campaignData.earning_summary,
                ai_enhanced: true
            }, { onConflict: 'slug' });

            if (insertError) console.log(`   ❌ Insert Error: ${insertError.message}`);
            else console.log(`      ✅ Inserted: ${slug}`);
        }
    } catch (e: any) {
        console.error('Büyük Hata:', e);
    }
};

if (require.main === module) {
    scrapeTurkcell();
}
