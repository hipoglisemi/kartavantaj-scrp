/**
 * Detects and formats tiered campaigns properly
 * Example: "10K→1K, 30K→3K, 50K→5K, 100K→7.5K" should show as "1.000-7.500 TL (Kademeli)"
 */

export function detectAndFormatTieredCampaign(campaignData: any, originalText: string): any {
    if (!campaignData || !originalText) return campaignData;

    const text = originalText.toLowerCase();
    const title = (campaignData.title || '').toLowerCase();

    // Pattern 1: "10.000 TL'ye 1.000 TL, 30.000 TL'ye 3.000 TL..." format
    const tieredPattern = /(\d{1,3}(?:\.\d{3})*)\s*tl.*?(\d{1,3}(?:\.\d{3})*)\s*tl.*?(\d{1,3}(?:\.\d{3})*)\s*tl.*?(\d{1,3}(?:\.\d{3})*)\s*tl/gi;
    const matches = [...text.matchAll(tieredPattern)];

    // If we find multiple tier mentions (at least 2), it's likely a tiered campaign
    if (matches.length >= 2 || (text.includes('ve üzeri') && text.split('tl').length > 6)) {
        console.log(`   🎯 Kademeli kampanya tespit edildi`);

        // Extract all numbers from the text
        const numbers = text.match(/(\d{1,3}(?:\.\d{3})*)\s*tl/gi);
        if (numbers && numbers.length >= 4) {
            // Parse numbers
            const amounts = numbers
                .map(n => parseInt(n.replace(/\./g, '').replace(/tl/gi, '').trim()))
                .filter(n => !isNaN(n) && n > 0);

            if (amounts.length >= 4) {
                // Find min and max discount amounts (usually the smaller numbers)
                const discounts = amounts.filter(a => a < 10000).sort((a, b) => a - b);
                const spends = amounts.filter(a => a >= 10000).sort((a, b) => a - b);

                if (discounts.length >= 2 && spends.length >= 2) {
                    const minDiscount = discounts[0];
                    const maxDiscount = discounts[discounts.length - 1];
                    const minSpend = spends[0];

                    console.log(`      Min Spend: ${minSpend} TL (en düşük kademe)`);
                    console.log(`      Min Discount: ${minDiscount} TL`);
                    console.log(`      Max Discount: ${maxDiscount} TL`);

                    // Update earning to show range
                    const earningType = campaignData.earning?.includes('Puan') ? 'Puan' :
                        campaignData.earning?.includes('İndirim') ? 'İndirim' :
                            campaignData.earning?.includes('Ekstre') ? 'Ekstre İndirimi' : 'Puan';

                    const formattedMin = minDiscount.toLocaleString('tr-TR');
                    const formattedMax = maxDiscount.toLocaleString('tr-TR');

                    campaignData.earning = `${formattedMin}-${formattedMax} TL ${earningType} (Kademeli)`;
                    campaignData.min_spend = minSpend;
                    campaignData.max_discount = maxDiscount;

                    console.log(`      ✅ Earning güncellendi: ${campaignData.earning}`);

                    // Add tier information to description if not already there
                    if (!campaignData.description?.includes('kademeli') && !campaignData.description?.includes('Kademeli')) {
                        campaignData.description = `${campaignData.description} Kademeli ödül sistemi.`;
                    }
                }
            }
        }
    }

    return campaignData;
}
