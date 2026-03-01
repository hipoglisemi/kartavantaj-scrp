/**
 * Validates AI-parsed campaign data for mathematical consistency
 * Based on Gemini's recommendation for a validation layer
 */
export function validateAIParsing(data: any): { isValid: boolean; errors: string[] } {
    const errors: string[] = [];

    // 1. Yüzde varsa ama min_spend yoksa veya yanlışsa
    if (data.discount_percentage && data.discount_percentage > 0) {
        if (!data.min_spend || data.min_spend === 0) {
            errors.push(`🚨 Matematiksel tutarsızlık! discount_percentage=${data.discount_percentage} ama min_spend=${data.min_spend || 'NULL'}`);
        }

        // 2. Earning formatı kontrolü - yüzde bazlı kampanyalarda '%' olmalı
        if (data.earning && !data.earning.includes('%')) {
            errors.push(`🚨 Earning format hatası! discount_percentage=${data.discount_percentage} ama earning="${data.earning}" (% içermiyor)`);
        }

        // 3. Min_spend hesaplama kontrolü
        if (data.max_discount && data.min_spend) {
            const expectedMinSpend = Math.round(data.max_discount / (data.discount_percentage / 100));
            const tolerance = expectedMinSpend * 0.1; // %10 tolerans
            if (Math.abs(data.min_spend - expectedMinSpend) > tolerance) {
                errors.push(`⚠️  Min_spend hesaplama uyarısı: Beklenen ~${expectedMinSpend} TL, bulunan ${data.min_spend} TL`);
            }
        }
    }

    // 4. Mantıksız değerler - earning > min_spend
    if (data.min_spend && data.max_discount && data.max_discount > data.min_spend) {
        errors.push(`🚨 Mantık hatası! max_discount (${data.max_discount}) > min_spend (${data.min_spend})`);
    }

    // 5. Earning boş olamaz
    if (!data.earning || data.earning.trim() === '') {
        errors.push(`🚨 Earning boş! ASLA boş bırakılmamalı`);
    }

    // 6. Kademeli kampanya kontrolü ("Her X TL'ye Y TL" pattern)
    if (data.description && /her\s+\d+.*?tl.*?(toplam|toplamda)/i.test(data.description)) {
        if (!data.min_spend) {
            errors.push(`⚠️  Kademeli kampanya tespit edildi ama min_spend NULL`);
        }
    }

    return {
        isValid: errors.length === 0,
        errors
    };
}
