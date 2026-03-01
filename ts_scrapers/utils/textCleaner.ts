/**
 * Simple text cleaner to remove boilerplate banking legal terms.
 * This helps reduce token usage and provides cleaner input for the AI.
 */
export function cleanCampaignText(rawText: string): string {
    if (!rawText) return '';

    // Regex patterns for more robust boilerplate matching
    const junkPatterns = [
        /yasal mevzuat gereği/gi,
        /taksitlendirme süresi bireysel/gi,
        /operatörlerin kendi tarifeleri/gi,
        /bankamızın kampanyayı durdurma/gi,
        /iptal edilen işlemlerde/gi,
        /yasal mevzuat/gi,
        /kullanılmayan puanlar geri alınacaktır/gi,
        /kampanya koşullarına uygun olmayan işlemler/gi,
        /harcama itirazı durumunda/gi,
        /taksit kısıtı bulunan ürün grupları/gi,
        /ödüller nakde çevrilemez/gi,
        /türkiye iş bankası a\.ş\./gi,
        /yapı ve kredi bankası a\.ş\./gi,
        /akbank t\.a\.ş\./gi,
        /garanti bbva/gi,
        /qnb finansbank/gi,
        /denizbank a\.ş\./gi
    ];

    // Split by newlines to process paragraph by paragraph
    return rawText
        .split('\n')
        .filter(line => {
            const trimmed = line.trim();
            if (!trimmed) return false;
            // If the line matches ANY of the junk patterns, filter it out
            return !junkPatterns.some(pattern => pattern.test(trimmed));
        })
        .join('\n')
        .trim();
}

