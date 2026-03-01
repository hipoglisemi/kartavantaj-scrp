// Bank Campaign Detector
// Detects bank-specific campaigns (avans, çekiliş, bağış, etc.) to skip AI processing

const BANK_SERVICE_KEYWORDS = [
    'kart başvuru', 'kart üyelik', 'yeni kart', 'kart başvurusu',
    'sanal kart', 'dijital kart', 'ödeme talimatı', 'otomatik ödeme',
    'fatura ödeme', 'ekstre bölme', 'ekstre erteleme', 'taksit erteleme',
    'ödeme erteleme', 'taksit öteleme', 'borç transferi', 'borç yapılandırma',
    'faiz', 'komisyon', 'masraf', 'ek hesap', 'nakit avans', 'limit artırım',
    'kredi kartı limiti', 'limit artış', 'bağış', 'çekiliş', 'piyango'
];

const SHOPPING_SIGNALS = [
    'alışveriş', 'harcama', 'üzeri', 'en az', 'sepette', 'pos',
    'üye işyeri', 'mağaza', 'market', 'online', 'kazan', 'indirim',
    'puan', 'chip', 'bonus', 'worldpuan', 'axesspuan'
];

/**
 * Detects if a campaign is a pure bank service campaign (no merchant involvement)
 * Strictly follows "ANTIGRAVITY TEK DÜZELTME KOMUTU" Point 1.
 */
export function isBankCampaign(title: string, content: string): boolean {
    const text = (title + ' ' + content).toLowerCase();

    // 1. NEGATIVE SIGNALS (Shopping/Merchant)
    // If there is ANY shopping signal, it is NOT a bank service campaign
    for (const signal of SHOPPING_SIGNALS) {
        if (text.includes(signal)) {
            return false;
        }
    }

    // 2. POSITIVE SIGNALS (Bank Service Only)
    for (const keyword of BANK_SERVICE_KEYWORDS) {
        if (text.includes(keyword)) {
            return true;
        }
    }

    return false;
}

/**
 * Get the reason why a campaign was flagged as bank campaign
 */
export function getBankCampaignReason(title: string, content: string): string {
    const text = (title + ' ' + content).toLowerCase();

    // Negative check first here too for consistency
    for (const signal of SHOPPING_SIGNALS) {
        if (text.includes(signal)) return 'Shopping signal detected (Overriding bank detection)';
    }

    for (const keyword of BANK_SERVICE_KEYWORDS) {
        if (text.includes(keyword)) {
            return `Bank Service: "${keyword}"`;
        }
    }

    return 'Regular merchant campaign';
}
