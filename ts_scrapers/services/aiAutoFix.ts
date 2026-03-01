// src/services/aiAutoFix.ts
// AI Auto-Fix Engine: Generates patches for quality issues with confidence scores

import { createClient } from '@supabase/supabase-js';
import * as crypto from 'crypto';
import * as dotenv from 'dotenv';

dotenv.config();

const supabase = createClient(
    process.env.SUPABASE_URL!,
    process.env.SUPABASE_ANON_KEY!
);

const GEMINI_API_KEY = process.env.GOOGLE_GEMINI_KEY!;
const EXTRACTOR_VERSION = '2.0'; // Increment when extraction logic changes

interface AiFixResult {
    patch: Record<string, any>;
    confidence: number;
    notes: string;
}

interface CampaignData {
    id: number;
    title: string;
    description: string;
    valid_from?: string;
    valid_until?: string;
    min_spend?: number;
    earning?: string;
    discount?: string;
    max_discount?: number;
    discount_percentage?: number;
    eligible_cards?: string[];
    participation_method?: string;
    spend_channel?: string;
}

interface AuditIssue {
    type: string;
    severity: string;
    message: string;
}

// Cache for AI results
const aiCache = new Map<string, AiFixResult>();

function generateCacheKey(snippet: string, issueType: string): string {
    return crypto
        .createHash('sha1')
        .update(snippet + issueType + EXTRACTOR_VERSION)
        .digest('hex');
}

const sleep = (ms: number) => new Promise(resolve => setTimeout(resolve, ms));

async function callGeminiAPI(prompt: string, retryCount: number = 0): Promise<any> {
    const MAX_RETRIES = 4;
    const BASE_DELAYS = [2000, 5000, 12000, 25000]; // Exponential backoff

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

        // Handle rate limit (429)
        if (response.status === 429) {
            if (retryCount >= MAX_RETRIES) {
                throw new Error(`RATE_LIMITED_429: Max retries (${MAX_RETRIES}) exceeded`);
            }

            // Exponential backoff with jitter
            const baseDelay = BASE_DELAYS[retryCount] || 25000;
            const jitter = Math.random() * 500;
            const delay = baseDelay + jitter;

            console.log(`   ⏳ Rate limited (429), retry ${retryCount + 1}/${MAX_RETRIES} after ${Math.round(delay)}ms...`);
            await sleep(delay);

            return callGeminiAPI(prompt, retryCount + 1);
        }

        if (!response.ok) {
            throw new Error(`Gemini API error: ${response.status}`);
        }

        const data: any = await response.json();
        const text = data.candidates?.[0]?.content?.parts?.[0]?.text;
        if (!text) throw new Error('No response from Gemini');

        // Extract JSON from response
        const jsonMatch = text.match(/\{[\s\S]*\}/);
        if (!jsonMatch) {
            throw new Error('No JSON found in AI response');
        }

        // Add pacing between successful calls (1.5s)
        await sleep(1500);

        return JSON.parse(jsonMatch[0]);

    } catch (error: any) {
        // If it's a rate limit error and we've exhausted retries, throw with special marker
        if (error.message?.includes('RATE_LIMITED_429')) {
            throw error;
        }

        // For other errors, throw immediately
        throw error;
    }
}

function buildPromptForIssue(
    issueType: string,
    campaign: CampaignData,
    snippet: string
): string {
    const baseContext = `
Campaign Title: ${campaign.title}
Current Values:
- valid_from: ${campaign.valid_from || 'null'}
- valid_until: ${campaign.valid_until || 'null'}
- min_spend: ${campaign.min_spend || 0}
- earning: ${campaign.earning || 'null'}
- discount: ${campaign.discount || 'null'}
- max_discount: ${campaign.max_discount || 'null'}
- discount_percentage: ${campaign.discount_percentage || 'null'}
- eligible_cards: ${JSON.stringify(campaign.eligible_cards || [])}
- participation_method: ${campaign.participation_method || 'null'}

Campaign Text Snippet (first 900 chars):
${snippet.substring(0, 900)}
`;

    switch (issueType) {
        case 'discount_missing_taksit':
            return `${baseContext}

TASK: Extract the installment (taksit) information from the text.

RULES:
1. Look for patterns like: "3 taksit", "faizsiz 6 taksit", "peşin fiyatına 9 taksit", "+3 taksit", "6 aya varan taksit"
2. Extract the NUMBER of installments
3. Format as "N Taksit" (e.g., "3 Taksit", "6 Taksit")
4. If multiple installment options, use the HIGHEST number
5. If no clear installment info, return null

CONFIDENCE SCORING:
- Exact match (e.g., "6 taksit"): 0.95
- Pattern match (e.g., "peşin fiyatına 6 taksit"): 0.90
- Contextual (e.g., "6 aya varan"): 0.75
- Ambiguous: 0.50
- Not found: 0.30

OUTPUT (JSON only):
{
  "patch": {
    "discount": "N Taksit" or null
  },
  "confidence": 0.0-1.0,
  "notes": "Brief explanation of what was found and why"
}`;

        case 'date_year_mismatch':
            return `${baseContext}

TASK: Determine the correct year for the campaign end date.

CONTEXT: Today is ${new Date().toISOString().split('T')[0]}

RULES:
1. Look for explicit year mentions (e.g., "2025", "2026")
2. If month is mentioned without year:
   - If month >= current month: use current year
   - If month < current month: use next year
3. Common patterns: "31 Aralık", "Aralık sonuna kadar", "31.12.2025"
4. If text says "Aralık" and we're in December 2025, it's 2025 not 2026

CONFIDENCE SCORING:
- Explicit year in text: 0.95
- Month-based inference (clear context): 0.85
- Ambiguous but logical: 0.70
- Uncertain: 0.50

OUTPUT (JSON only):
{
  "patch": {
    "valid_until": "YYYY-MM-DD"
  },
  "confidence": 0.0-1.0,
  "notes": "Explanation of year determination"
}`;

        case 'date_range_parse_bug':
            return `${baseContext}

TASK: Parse the date range correctly from text like "1-31 Aralık".

RULES:
1. Look for patterns: "DD-DD Month", "DD Month - DD Month"
2. Extract start day and end day
3. Determine month and year
4. Format as YYYY-MM-DD for both valid_from and valid_until
5. If "31-31 Aralık", treat as single date (valid_until only)

CONFIDENCE SCORING:
- Clear range pattern: 0.90
- Single date: 0.85
- Ambiguous: 0.60

OUTPUT (JSON only):
{
  "patch": {
    "valid_from": "YYYY-MM-DD" or null,
    "valid_until": "YYYY-MM-DD"
  },
  "confidence": 0.0-1.0,
  "notes": "Explanation of date parsing"
}`;

        case 'eligible_cards_missing':
            return `${baseContext}

TASK: Extract eligible card names from the text.

RULES:
1. Look for card names: Axess, Wings, Free, Akbank Kart, Neo, Ticari Kart
2. Check for negative context: "dahil değil", "geçerli değil", "hariç"
3. Only include cards that are EXPLICITLY mentioned as eligible
4. Return as array of strings

CONFIDENCE SCORING:
- Explicit mention without negatives: 0.90
- Contextual mention: 0.75
- Ambiguous: 0.55
- Not found: 0.40

OUTPUT (JSON only):
{
  "patch": {
    "eligible_cards": ["Card1", "Card2"] or []
  },
  "confidence": 0.0-1.0,
  "notes": "Which cards were found and context"
}`;

        case 'participation_sms_missed':
            return `${baseContext}

TASK: Determine if SMS participation is required.

RULES:
1. Look for SMS signals: "SMS", "kayıt", "katıl", "gönder", "mesaj", "XXXX'e gönder"
2. Check if participation is automatic: "otomatik", "başvuru gerekmez"
3. If SMS signals found, set participation_method to "SMS"

CONFIDENCE SCORING:
- Explicit SMS instruction: 0.95
- SMS keyword present: 0.85
- Contextual: 0.70
- Ambiguous: 0.50

OUTPUT (JSON only):
{
  "patch": {
    "participation_method": "SMS" or "AUTO" or null
  },
  "confidence": 0.0-1.0,
  "notes": "Explanation of participation method"
}`;

        case 'spend_zero_with_signals':
            return `${baseContext}

TASK: Extract minimum spend amount from text.

RULES:
1. Look for patterns: "X TL üzeri", "X TL ve üzeri", "en az X TL", "X TL harcamaya"
2. Extract the number
3. Return as integer (no decimals)
4. If multiple amounts, use the MINIMUM required

CONFIDENCE SCORING:
- Explicit "X TL üzeri": 0.95
- Pattern match: 0.85
- Contextual: 0.70
- Ambiguous: 0.50

OUTPUT (JSON only):
{
  "patch": {
    "min_spend": number or 0
  },
  "confidence": 0.0-1.0,
  "notes": "Explanation of min spend extraction"
}`;

        case 'cap_missing':
            return `${baseContext}

TASK: Extract maximum discount/reward cap from text.

RULES:
1. Look for: "en fazla X TL", "max X TL", "toplam X TL", "X TL'ye varan", "X TL'ye kadar"
2. Extract the number
3. Return as integer

CONFIDENCE SCORING:
- Explicit cap mention: 0.90
- Pattern match: 0.80
- Contextual: 0.65
- Ambiguous: 0.45

OUTPUT (JSON only):
{
  "patch": {
    "max_discount": number or null
  },
  "confidence": 0.0-1.0,
  "notes": "Explanation of cap extraction"
}`;

        case 'percent_missing':
            return `${baseContext}

TASK: Extract percentage discount from text.

RULES:
1. Look for: "%X", "yüzde X", "X%"
2. Extract the number (not the symbol)
3. Return as integer (e.g., 10 for 10%)

CONFIDENCE SCORING:
- Explicit percentage: 0.95
- Pattern match: 0.85
- Ambiguous: 0.55

OUTPUT (JSON only):
{
  "patch": {
    "discount_percentage": number or null
  },
  "confidence": 0.0-1.0,
  "notes": "Explanation of percentage extraction"
}`;

        default:
            return `${baseContext}

TASK: Analyze the issue and suggest a fix.

Issue Type: ${issueType}

Provide a patch and confidence score based on the text.

OUTPUT (JSON only):
{
  "patch": {},
  "confidence": 0.0-1.0,
  "notes": "Explanation"
}`;
    }
}

export async function runAiFixForCampaign(
    campaignId: number,
    issues: AuditIssue[]
): Promise<AiFixResult> {
    // Fetch campaign data
    const { data: campaign, error } = await supabase
        .from('campaigns')
        .select('id, title, description, valid_from, valid_until, min_spend, earning, discount, max_discount, discount_percentage, eligible_cards, participation_method, spend_channel')
        .eq('id', campaignId)
        .single();

    if (error || !campaign) {
        throw new Error(`Campaign ${campaignId} not found`);
    }

    const snippet = campaign.description || '';

    // Process each issue and collect patches
    const allPatches: Record<string, any> = {};
    let totalConfidence = 0;
    const notes: string[] = [];

    for (const issue of issues) {
        const cacheKey = generateCacheKey(snippet, issue.type);

        // Check cache
        if (aiCache.has(cacheKey)) {
            const cached = aiCache.get(cacheKey)!;
            Object.assign(allPatches, cached.patch);
            totalConfidence += cached.confidence;
            notes.push(`[CACHED] ${issue.type}: ${cached.notes}`);
            continue;
        }

        // Generate prompt
        const prompt = buildPromptForIssue(issue.type, campaign, snippet);

        try {
            // Call AI
            const aiResult = await callGeminiAPI(prompt);

            // Validate response
            if (!aiResult.patch || typeof aiResult.confidence !== 'number') {
                throw new Error('Invalid AI response format');
            }

            // Store in cache
            aiCache.set(cacheKey, aiResult);

            // Merge patches
            Object.assign(allPatches, aiResult.patch);
            totalConfidence += aiResult.confidence;
            notes.push(`${issue.type}: ${aiResult.notes}`);
        } catch (err: any) {
            // Special handling for rate limit errors
            if (err.message?.includes('RATE_LIMITED_429')) {
                console.error(`AI fix rate limited for ${issue.type}`);
                notes.push(`${issue.type}: RATE_LIMITED (will retry later) - ${new Date().toISOString()}`);
                // Don't add to confidence - will be retried
                throw new Error(`RATE_LIMITED: ${issue.type}`);
            } else {
                console.error(`AI fix failed for ${issue.type}:`, err.message);
                notes.push(`${issue.type}: FAILED - ${err.message}`);
                totalConfidence += 0.3; // Low confidence for failures
            }
        }
    }

    // Calculate average confidence
    const avgConfidence = issues.length > 0 ? totalConfidence / issues.length : 0;

    return {
        patch: allPatches,
        confidence: Math.min(1.0, Math.max(0.0, avgConfidence)),
        notes: notes.join('\n')
    };
}

export async function applyPatchToCampaign(
    campaignId: number,
    patch: Record<string, any>
): Promise<{ success: boolean; reason?: string }> {
    // Validation guards
    const validationErrors: string[] = [];

    // 1. Date validation
    if (patch.valid_from && patch.valid_until) {
        if (patch.valid_from >= patch.valid_until) {
            validationErrors.push('valid_from must be before valid_until');
        }
    }

    // 2. Installments validation
    if (patch.discount && typeof patch.discount === 'string') {
        const installmentMatch = patch.discount.match(/(\d+)\s*Taksit/i);
        if (installmentMatch) {
            const installments = parseInt(installmentMatch[1]);
            if (installments <= 0) {
                validationErrors.push('installments must be > 0');
            }
        }
    }

    // 3. Eligible cards validation
    if (patch.eligible_cards !== undefined) {
        if (!Array.isArray(patch.eligible_cards) || patch.eligible_cards.length === 0) {
            validationErrors.push('eligible_cards must be non-empty array');
        }
    }

    // If validation fails, return error
    if (validationErrors.length > 0) {
        return {
            success: false,
            reason: `Validation failed: ${validationErrors.join(', ')}`
        };
    }

    const { error } = await supabase
        .from('campaigns')
        .update(patch)
        .eq('id', campaignId);

    if (error) {
        return {
            success: false,
            reason: `Database error: ${error.message}`
        };
    }

    return { success: true };
}

export async function saveAiFixResult(
    auditId: number,
    result: AiFixResult,
    status: 'auto_applied' | 'needs_review' | 'failed'
): Promise<void> {
    const updateData: any = {
        ai_patch: result.patch,
        ai_confidence: result.confidence,
        ai_notes: result.notes,
        ai_status: status,
        ai_model: 'gemini-2.0-flash'
    };

    if (status === 'auto_applied') {
        updateData.ai_applied_at = new Date().toISOString();
    }

    const { error } = await supabase
        .from('campaign_quality_audits')
        .update(updateData)
        .eq('id', auditId);

    if (error) {
        throw new Error(`Failed to save AI fix result: ${error.message}`);
    }
}
