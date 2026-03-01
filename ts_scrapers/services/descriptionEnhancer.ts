import * as dotenv from 'dotenv';
dotenv.config();

const GEMINI_API_KEY = process.env.GOOGLE_GEMINI_KEY!;

/**
 * Enhances a campaign description using AI to make it more marketing-oriented.
 * Uses minimal tokens (~275) for cost efficiency.
 * 
 * @param rawDescription - The original description (usually just title)
 * @returns Enhanced marketing-style description with emojis (2 sentences max)
 */
export async function enhanceDescription(rawDescription: string, retryCount = 0): Promise<string> {
    const MAX_RETRIES = 3;
    const BASE_DELAY_MS = 2000;

    if (!rawDescription || rawDescription.length < 10) {
        return rawDescription;
    }

    if (/[\u{1F300}-\u{1F9FF}]/u.test(rawDescription)) {
        return rawDescription;
    }

    const prompt = `
You are a creative banking marketing expert.
Convert this raw campaign into a 1-sentence catchy summary.
Language: TURKISH.
- Use 1 emoji. 
- Focus on the PRIMARY benefit.
- NO extra words, NO prefix.

Input: "${rawDescription}"
    `.trim();

    try {
        const response = await fetch(
            `https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent?key=${GEMINI_API_KEY}`,
            {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    contents: [{ parts: [{ text: prompt }] }]
                })
            }
        );

        if (response.status === 429 || response.status >= 500) {
            if (retryCount < MAX_RETRIES) {
                const delay = BASE_DELAY_MS * Math.pow(2, retryCount);
                console.log(`   ⏳ Marketing enhancement rate limit/error (${response.status}). Retrying in ${delay}ms...`);
                await new Promise(r => setTimeout(r, delay));
                return enhanceDescription(rawDescription, retryCount + 1);
            }
        }

        if (!response.ok) {
            console.warn(`   ⚠️ Description enhancement failed (${response.status}), using original`);
            return rawDescription;
        }

        const data: any = await response.json();
        const enhanced = data.candidates?.[0]?.content?.parts?.[0]?.text?.trim();

        if (enhanced && enhanced.length > 0) {
            console.log(`   ✨ Enhanced: ${enhanced.substring(0, 80)}...`);
            return enhanced;
        }

        return rawDescription;

    } catch (error: any) {
        if (retryCount < MAX_RETRIES) {
            const delay = BASE_DELAY_MS * Math.pow(2, retryCount);
            await new Promise(r => setTimeout(r, delay));
            return enhanceDescription(rawDescription, retryCount + 1);
        }
        console.error('   ❌ Description enhancement error:', error.message);
        return rawDescription;
    }
}

/**
 * Batch enhance descriptions (for future optimization)
 */
export async function enhanceDescriptionsBatch(descriptions: string[]): Promise<string[]> {
    const enhanced: string[] = [];

    for (const desc of descriptions) {
        const result = await enhanceDescription(desc);
        enhanced.push(result);
        // Small delay to avoid rate limits
        await new Promise(resolve => setTimeout(resolve, 100));
    }

    return enhanced;
}
