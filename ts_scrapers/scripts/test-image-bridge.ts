import { downloadImageDirectly } from '../services/imageService';
import * as dotenv from 'dotenv';
dotenv.config();

async function testBridge() {
    console.log('🧪 Testing Image Bridge (Supabase Storage)...');

    // Using a reliable public image for testing
    const testImageUrl = 'https://www.google.com/images/branding/googlelogo/1x/googlelogo_color_272x92dp.png';
    const title = 'Bridge Test Campaign';
    const bankName = 'test-cloudflare';

    try {
        const resultUrl = await downloadImageDirectly(testImageUrl, title, bankName);

        console.log('\n--- TEST RESULT ---');
        console.log(`Input: ${testImageUrl}`);
        console.log(`Output: ${resultUrl}`);

        if (resultUrl.includes('supabase.co/storage')) {
            console.log('\n✅ SUCCESS: Image bridged to Supabase Storage!');
        } else {
            console.error('\n❌ FAILED: URL did not point to Supabase.');
        }
    } catch (e: any) {
        console.error('\n❌ TEST ERROR:', e.message);
    }
}

testBridge();
