// src/services/brandValidator.test.ts
import { validateBrand } from './brandValidator';

async function testBrandValidator() {
    console.log('🧪 Testing Brand Validator Service\n');

    const testCases = [
        {
            brand: 'Migros',
            context: 'Migros mağazalarında 1000 TL ve üzeri alışverişlerinizde 150 TL puan kazanın!',
            expected: 'AUTO_ADD'
        },
        {
            brand: 'Worldpuan',
            context: 'Yapı Kredi Worldpuan ile alışverişlerinizde taksit fırsatı',
            expected: 'REJECT'
        },
        {
            brand: 'ZorTech Bilişim',
            context: 'ZorTech Bilişim mağazalarında özel indirim kampanyası başladı',
            expected: 'AUTO_ADD'
        },
        {
            brand: 'Taksit',
            context: '9 taksit fırsatı ile ödeme kolaylığı',
            expected: 'REJECT'
        },
        {
            brand: 'Starbucks',
            context: 'Starbucks kahve alışverişlerinizde %20 chip-para',
            expected: 'AUTO_ADD'
        }
    ];

    for (const testCase of testCases) {
        console.log(`\n📋 Testing: "${testCase.brand}"`);
        console.log(`   Context: "${testCase.context.substring(0, 60)}..."`);

        const result = await validateBrand(testCase.brand, testCase.context);

        const isMatch = result.decision === testCase.expected ? '✅' : '❌';
        console.log(`   ${isMatch} Decision: ${result.decision} (Expected: ${testCase.expected})`);
        console.log(`   Confidence: ${result.confidence}`);
        console.log(`   Reason: ${result.reason}`);

        // Rate limit
        await new Promise(resolve => setTimeout(resolve, 1500));
    }
}

// Run if executed directly
if (require.main === module) {
    testBrandValidator().catch(console.error);
}
