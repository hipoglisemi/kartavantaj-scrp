import { supabase } from './supabase';

/**
 * AI Optimization Helper: Filters campaigns to classify as New, Incomplete, or Complete
 * 
 * @param urls - Array of campaign URLs to check
 * @param cardName - Card name to filter by (e.g., 'Paraf', 'Wings')
 * @returns Object with lists of new and incomplete URLs to process
 */
export async function optimizeCampaigns(
    urls: string[],
    cardName: string
): Promise<{
    urlsToProcess: string[];
    stats: {
        total: number;
        new: number;
        incomplete: number;
        complete: number;
        blacklisted: number;
    }
}> {
    console.log(`   🔍 Checking for new and incomplete campaigns in database...`);

    // STEP 1: Blacklist Check (filter out blacklisted URLs)
    const { data: blacklistedData } = await supabase
        .from('campaign_blacklist')
        .select('url')
        .in('url', urls);

    const blacklistedUrls = new Set(blacklistedData?.map(b => b.url) || []);
    const cleanUrls = urls.filter(url => !blacklistedUrls.has(url));

    if (blacklistedUrls.size > 0) {
        console.log(`   🚫 Blacklist: ${blacklistedUrls.size} URLs atlandı`);
    }

    // STEP 2: Query database for existing campaigns (only clean URLs)
    const { data: existingCampaigns } = await supabase
        .from('campaigns')
        .select('reference_url, title, image, image_url, brand, sector_slug, participation_method, eligible_customers')
        .eq('card_name', cardName)
        .in('reference_url', cleanUrls);

    const existingMap = new Map(
        existingCampaigns?.map(c => [c.reference_url, c]) || []
    );

    const newUrls: string[] = [];
    const incompleteUrls: string[] = [];

    for (const url of cleanUrls) {
        const campaign = existingMap.get(url);

        if (!campaign) {
            // Case 1: Campaign does not exist -> NEW
            newUrls.push(url);
        } else {
            // Case 2: Campaign exists, check if data is incomplete
            // Check both image and image_url fields (some scrapers use image_url for direct bank URLs)
            // 🔥 ENHANCED: Only count as "hasImage" if it's NOT a raw bank URL (meaning already migrated to Cloudflare)
            let hasImage = campaign.image && campaign.image.trim() !== '' && !campaign.image.includes('placeholder') && !campaign.image.includes('favicon');

            // If it's a raw Maximum or IsBank URL, consider it incomplete so we can migrate to Cloudflare
            if (hasImage && (campaign.image.includes('www.maximum.com.tr') || campaign.image.includes('www.isbank.com.tr'))) {
                hasImage = false;
            }

            const hasImageUrl = campaign.image_url && campaign.image_url.trim() !== '';
            const isImageMissing = !hasImage && !hasImageUrl;
            const isBrandMissing = !campaign.brand;
            const isSectorGeneric = campaign.sector_slug === 'genel';

            // Akbank/Maximum/Yapı Kredi specific checks (usually share campaigns across cards)
            const isParticipationMissing = Array.isArray(campaign.participation_method) && (campaign.participation_method.length === 0 || campaign.participation_method === null);

            const bankLower = (cardName.toLowerCase() === 'wings' || cardName.toLowerCase() === 'axess' || cardName.toLowerCase() === 'free' || cardName.toLowerCase() === 'business') ? 'akbank' : '';
            const isSingleCard = Array.isArray(campaign.eligible_customers) && campaign.eligible_customers.length === 1 &&
                (['Maximum', 'Wings', 'Axess', 'Free', 'World', 'Paraf', 'Bankkart'].includes(campaign.eligible_customers[0]) || bankLower === 'akbank');

            // You can customize this logic based on strictness
            if (isImageMissing || isBrandMissing || isParticipationMissing || isSingleCard) {
                // Log the reason for debugging
                const reason = [];
                if (isImageMissing) reason.push('image_missing');
                if (isBrandMissing) reason.push('brand_missing');
                if (isParticipationMissing) reason.push('participation_missing');
                if (isSingleCard) reason.push('single_card');
                // if (isSectorGeneric) reason.push('sector_generic');

                console.log(`      ⚠️  Incomplete (${reason.join(', ')}): ${url}`);
                incompleteUrls.push(url);
            }
        }
    }

    const urlsToProcess = [...newUrls, ...incompleteUrls];
    const completeCount = cleanUrls.length - urlsToProcess.length;

    const stats = {
        total: urls.length,
        new: newUrls.length,
        incomplete: incompleteUrls.length,
        complete: completeCount,
        blacklisted: blacklistedUrls.size
    };

    console.log(`   📊 Total: ${stats.total}, New: ${stats.new}, Incomplete: ${stats.incomplete}, Complete: ${stats.complete}`);

    if (completeCount > 0) {
        console.log(`   ⚡ Skipping ${stats.complete} complete campaigns...`);
    }

    if (urlsToProcess.length > 0) {
        console.log(`   🚀 Processing ${urlsToProcess.length} campaigns (${stats.new} new + ${stats.incomplete} incomplete)...`);
    }

    return { urlsToProcess, stats };
}
