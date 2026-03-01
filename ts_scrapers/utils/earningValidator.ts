/**
 * Post-processing validation to catch AI parsing errors
 * Specifically designed to prevent "puan" vs "indirim" confusion
 */

export function validateAndFixEarningType(campaignData: any, originalText: string): any {
    if (!campaignData || !campaignData.earning) return campaignData;

    const title = (campaignData.title || '').toLowerCase();
    const earning = campaignData.earning || '';
    const text = originalText.toLowerCase();

    // Rule 1: Title says "indirim" but earning says "Puan"
    if (title.includes('indirim') && earning.includes('Puan')) {
        console.log(`   🔧 AUTO-FIX: Title has "indirim" but earning has "Puan"`);
        console.log(`      Before: ${earning}`);

        // Check if it's "ekstre indirimi"
        if (title.includes('ekstre') || text.includes('ekstre indirimi')) {
            campaignData.earning = earning.replace(/Puan/g, 'Ekstre İndirimi');
            console.log(`      After: ${campaignData.earning} (Ekstre İndirimi detected)`);
        } else {
            campaignData.earning = earning.replace(/Puan/g, 'İndirim');
            console.log(`      After: ${campaignData.earning}`);
        }

        // Also fix badge if needed
        if (campaignData.badge_text === 'PUAN') {
            campaignData.badge_text = 'İNDİRİM';
            console.log(`      Badge fixed: PUAN → İNDİRİM`);
        }
    }

    // Rule 2: Title says "puan" but earning says "İndirim"
    if ((title.includes('puan') || title.includes('worldpuan')) && earning.includes('İndirim')) {
        console.log(`   🔧 AUTO-FIX: Title has "puan" but earning has "İndirim"`);
        console.log(`      Before: ${earning}`);
        campaignData.earning = earning.replace(/İndirim/g, 'Puan');
        console.log(`      After: ${campaignData.earning}`);

        // Also fix badge if needed
        if (campaignData.badge_text === 'İNDİRİM') {
            campaignData.badge_text = 'PUAN';
            console.log(`      Badge fixed: İNDİRİM → PUAN`);
        }
    }

    // Rule 3: Detect "puan kullanımı dahil değildir" false positive
    // This is an exclusion clause, not an earning type
    if (earning.includes('Puan') && text.includes('puan kullanımı') && text.includes('dahil değildir')) {
        // Check if there's actually "puan kazanımı" or "puan hediye" in the text
        const hasPuanEarning = text.includes('puan kazanımı') ||
            text.includes('puan hediye') ||
            text.includes('worldpuan') ||
            text.includes('puan verilecektir') ||
            text.includes('puan kazanabilir');

        if (!hasPuanEarning && (text.includes('indirim') || text.includes('ekstre'))) {
            console.log(`   🔧 AUTO-FIX: "puan kullanımı dahil değildir" false positive detected`);
            console.log(`      Before: ${earning}`);

            if (text.includes('ekstre')) {
                campaignData.earning = earning.replace(/Puan/g, 'Ekstre İndirimi');
            } else {
                campaignData.earning = earning.replace(/Puan/g, 'İndirim');
            }

            console.log(`      After: ${campaignData.earning}`);

            if (campaignData.badge_text === 'PUAN') {
                campaignData.badge_text = 'İNDİRİM';
                console.log(`      Badge fixed: PUAN → İNDİRİM`);
            }
        }
    }

    return campaignData;
}
