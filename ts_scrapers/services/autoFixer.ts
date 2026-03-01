/**
 * Auto Fixer Module
 * Automatically fixes campaign data issues
 */

import { generateSectorSlug } from '../utils/slugify';
import { supabase } from '../utils/supabase';
import { syncEarningAndDiscount } from '../utils/dataFixer';
import { parseWithGemini, parseSurgical } from './geminiParser';
import { assignBadge } from './badgeAssigner';
import { CampaignValidation } from './qualityChecker';

export interface FixResult {
    campaignId: number;
    success: boolean;
    method: 'badge_reassign' | 'ai_reparse' | 'ai_correction' | 'skip';
    message: string;
    newScore?: number;
}

export async function autoFixCampaign(
    campaign: any,
    validation: CampaignValidation
): Promise<FixResult> {

    // Determine fix strategy based on issues
    const criticalIssues = validation.issues.filter(i => i.severity === 'critical');
    const hasBadgeIssue = validation.issues.some(i => i.field === 'badge_text');
    const missingFields = validation.issues.filter(i => i.issue.includes('Missing')).length;

    console.log(`\n🔧 Fixing: ${campaign.title}`);
    console.log(`   Score: ${validation.score}/100 | Issues: ${validation.issues.length}`);

    // Strategy 1: Badge Re-Assignment (Quick fix, no API call)
    if (hasBadgeIssue && validation.score >= 60 && criticalIssues.length <= 1) {
        return await fixBadge(campaign);
    }

    // AI strategies check (allow by default unless specifically disabled)
    const AI_DISABLED = process.env.DISABLE_AI_AUTOFIX === 'true';
    if (AI_DISABLED) {
        console.log('   🛑 AI Strategies skipped: AI is disabled.');
        return {
            campaignId: campaign.id,
            success: false,
            method: 'skip',
            message: 'AI correction skipped (AI is disabled)',
            newScore: validation.score
        };
    }

    // Strategy 2: AI Re-Parse (Full re-parse from HTML)
    if (criticalIssues.length >= 3 || validation.score < 50) {
        return await aiReparse(campaign);
    }

    // Strategy 3: Smart AI Correction (Fix specific fields only)
    if (missingFields > 0 && validation.score >= 50) {
        return await aiCorrection(campaign, validation);
    }

    // Nothing to fix or score is acceptable
    return {
        campaignId: campaign.id,
        success: true,
        method: 'skip',
        message: 'No fix needed',
        newScore: validation.score
    };
}

async function fixBadge(campaign: any): Promise<FixResult> {
    try {
        console.log('   Strategy: Badge Re-Assignment (Quick)');

        const badge = assignBadge(campaign);

        const { error } = await supabase
            .from('campaigns')
            .update({
                badge_text: badge.text,
                badge_color: badge.color
            })
            .eq('id', campaign.id);

        if (error) throw error;

        console.log(`   ✅ Badge updated: ${badge.text}`);

        return {
            campaignId: campaign.id,
            success: true,
            method: 'badge_reassign',
            message: `Badge corrected to ${badge.text}`
        };
    } catch (error: any) {
        console.error(`   ❌ Badge fix failed: ${error.message}`);
        return {
            campaignId: campaign.id,
            success: false,
            method: 'badge_reassign',
            message: error.message
        };
    }
}

async function aiReparse(campaign: any): Promise<FixResult> {
    try {
        console.log('   Strategy: AI Re-Parse (Full)');

        // Fetch HTML
        // Fetch HTML
        const targetUrl = campaign.url || campaign.reference_url;
        if (!targetUrl) {
            throw new Error('No URL found for campaign');
        }

        const response = await fetch(targetUrl);
        if (!response.ok) {
            throw new Error(`HTTP ${response.status}`);
        }

        const html = await response.text();

        // Parse with AI
        console.log('   🤖 Parsing with Gemini...');
        const aiData = await parseWithGemini(html, targetUrl, campaign.bank, campaign.card_name);

        // Assign badge
        const badge = assignBadge(aiData);

        // Update campaign
        const updatedData = {
            ...aiData,
            bank: campaign.bank, // Strictly preserve original bank
            image: campaign.image, // Preserve image
            card_name: campaign.card_name, // Preserve card name
            provider: campaign.provider,
            badge_text: badge.text,
            badge_color: badge.color,
            sector_slug: generateSectorSlug(aiData.category || 'Diğer'),
            ai_enhanced: true
        };

        // Final sync check
        syncEarningAndDiscount(updatedData);

        const { error } = await supabase
            .from('campaigns')
            .update(updatedData)
            .eq('id', campaign.id);

        if (error) throw error;

        console.log(`   ✅ Campaign fully re-parsed`);
        console.log(`      Category: ${aiData.category}`);
        console.log(`      Badge: ${badge.text}`);

        return {
            campaignId: campaign.id,
            success: true,
            method: 'ai_reparse',
            message: 'Successfully re-parsed with AI'
        };
    } catch (error: any) {
        console.error(`   ❌ AI re-parse failed: ${error.message}`);
        return {
            campaignId: campaign.id,
            success: false,
            method: 'ai_reparse',
            message: error.message
        };
    }
}

async function aiCorrection(campaign: any, validation: CampaignValidation): Promise<FixResult> {
    try {
        console.log('   Strategy: Smart AI Correction (Surgical)');

        const missingFields = validation.issues
            .filter(i => i.severity === 'critical' || i.severity === 'warning')
            .map(i => i.field);

        // Fetch HTML
        const targetUrl = campaign.url || campaign.reference_url;
        if (!targetUrl) {
            throw new Error('No URL found for campaign');
        }

        const response = await fetch(targetUrl);
        if (!response.ok) {
            throw new Error(`HTTP ${response.status}`);
        }

        const html = await response.text();

        // Surgical Parse
        console.log(`   🤖 Surgical fix for: ${missingFields.join(', ')}`);
        const surgicalResult = await parseSurgical(html, campaign, missingFields, targetUrl, campaign.bank);

        // Assign badge (final check)
        const badge = assignBadge(surgicalResult);

        const updatedData = {
            ...surgicalResult,
            badge_text: badge.text,
            badge_color: badge.color,
            ai_enhanced: true
        };

        // Final sync check
        syncEarningAndDiscount(updatedData);

        const { error } = await supabase
            .from('campaigns')
            .update(updatedData)
            .eq('id', campaign.id);

        if (error) throw error;

        console.log(`   ✅ Campaign surgically fixed`);

        return {
            campaignId: campaign.id,
            success: true,
            method: 'ai_correction',
            message: `Successfully fixed missing fields: ${missingFields.join(', ')}`
        };

    } catch (error: any) {
        console.error(`   ❌ AI correction failed: ${error.message}`);
        return {
            campaignId: campaign.id,
            success: false,
            method: 'ai_correction',
            message: error.message
        };
    }
}

export async function batchFixCampaigns(
    campaigns: any[],
    validations: CampaignValidation[],
    options: {
        maxFixes?: number;
        delayMs?: number;
    } = {}
): Promise<FixResult[]> {
    const { maxFixes = Infinity, delayMs = 3000 } = options;
    const results: FixResult[] = [];

    // Get campaigns that need fixing
    const needsFix = validations
        .filter(v => !v.isValid || v.score < 70)
        .slice(0, maxFixes);

    console.log(`\n🔧 Batch fixing ${needsFix.length} campaigns...`);

    for (const validation of needsFix) {
        const campaign = campaigns.find(c => c.id === validation.campaignId);
        if (!campaign) continue;

        const result = await autoFixCampaign(campaign, validation);
        results.push(result);

        // Rate limiting
        await new Promise(resolve => setTimeout(resolve, delayMs));
    }

    return results;
}
