/**
 * Generic Campaign Detector
 * Detects campaigns that are not brand-specific (e.g., statement, lottery, donation)
 */

const GENERIC_KEYWORDS = [
    'ekstre',
    'çekiliş',
    'bağış',
    'kart başvuru',
    'başvuru',
    'talimat',
    'otomatik ödeme',
    'fatura',
    'havale',
    'eft',
    'pos',
    'atm',
    'şifre',
    'kart bloke',
    'limit artırım',
    'kredi başvuru',
    'hesap açılış',
    'dijital kart',
    'sanal kart',
    'mobil ödeme',
    'qr kod',
    'temassız',
    'apple pay',
    'google pay',
    'samsung pay',
    'garanti pay',
    'paycell',
    'bkm express',
    'troy',
    'masterpass'
];

/**
 * Checks if a campaign is generic (not brand-specific)
 */
export function isGenericCampaign(campaign: {
    title?: string;
    description?: string;
    brand?: string | string[];
}): boolean {
    const title = (campaign.title || '').toLowerCase();
    const description = (campaign.description || '').toLowerCase();
    const combinedText = `${title} ${description}`;

    // Check if any generic keyword exists
    return GENERIC_KEYWORDS.some(keyword => combinedText.includes(keyword));
}

/**
 * Marks campaign as generic if it matches generic keywords
 */
export function markGenericBrand(campaign: any): any {
    if (isGenericCampaign(campaign)) {
        console.log(`      🏷️  Generic campaign detected: "${campaign.title}"`);
        campaign.brand = 'Genel';
    }
    return campaign;
}
