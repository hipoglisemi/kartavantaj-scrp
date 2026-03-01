/**
 * Quality Control Pipeline
 * Orchestrates validation and auto-fixing of campaigns
 */

import { createClient } from '@supabase/supabase-js';
import * as dotenv from 'dotenv';
import { validateCampaign, generateValidationReport, CampaignValidation } from './services/qualityChecker';
import { batchFixCampaigns, FixResult } from './services/autoFixer';

dotenv.config();

const supabase = createClient(
    process.env.SUPABASE_URL!,
    process.env.SUPABASE_ANON_KEY!
);

interface PipelineOptions {
    autoFix?: boolean;
    maxFixes?: number;
    testMode?: boolean;
}

async function runQualityPipeline(options: PipelineOptions = {}) {
    const { autoFix = false, maxFixes = Infinity, testMode = false } = options;

    console.log('\n╔════════════════════════════════════════════════════════════════════════╗');
    console.log('║          CAMPAIGN QUALITY CONTROL PIPELINE                             ║');
    console.log('╚════════════════════════════════════════════════════════════════════════╝\n');

    if (testMode) {
        console.log('🧪 TEST MODE: Limited to first 20 campaigns\n');
    }

    // Step 1: Fetch campaigns
    console.log('📥 Step 1: Fetching campaigns from Supabase...');
    const { data: campaigns, error } = await supabase
        .from('campaigns')
        .select('*')
        .order('created_at', { ascending: false })
        .limit(testMode ? 20 : 1000);

    if (error) {
        console.error('❌ Error fetching campaigns:', error);
        return;
    }

    if (!campaigns || campaigns.length === 0) {
        console.log('⚠️  No campaigns found');
        return;
    }

    console.log(`✅ Fetched ${campaigns.length} campaigns\n`);

    // Step 2: Validate all campaigns
    console.log('🔍 Step 2: Validating campaigns...');
    const validations: CampaignValidation[] = [];

    for (const campaign of campaigns) {
        const validation = validateCampaign(campaign);
        validations.push(validation);
    }

    console.log(`✅ Validation complete\n`);

    // Step 3: Generate report
    console.log('📊 Step 3: Generating quality report...\n');
    const report = generateValidationReport(validations);
    console.log(report);

    // Step 4: Auto-fix if enabled
    if (autoFix) {
        console.log('\n🔧 Step 4: Auto-fixing issues...');
        console.log('─'.repeat(76));

        const fixResults = await batchFixCampaigns(campaigns, validations, {
            maxFixes,
            delayMs: 2500
        });

        // Show fix results
        console.log('\n\n📊 FIX RESULTS');
        console.log('─'.repeat(76));

        const successful = fixResults.filter(r => r.success);
        const failed = fixResults.filter(r => !r.success);

        console.log(`✅ Successfully fixed: ${successful.length}`);
        console.log(`❌ Failed to fix: ${failed.length}`);

        // Group by method
        const byMethod: Record<string, number> = {};
        successful.forEach(r => {
            byMethod[r.method] = (byMethod[r.method] || 0) + 1;
        });

        console.log('\nFix methods used:');
        Object.entries(byMethod).forEach(([method, count]) => {
            const icon = method === 'badge_reassign' ? '🏷️' :
                method === 'ai_reparse' ? '🤖' :
                    method === 'ai_correction' ? '✨' : '⏭️';
            console.log(`  ${icon} ${method}: ${count}`);
        });

        // Re-validate fixed campaigns
        if (successful.length > 0) {
            console.log('\n🔄 Re-validating fixed campaigns...');

            const { data: refetchedCampaigns } = await supabase
                .from('campaigns')
                .select('*')
                .in('id', successful.map(r => r.campaignId));

            if (refetchedCampaigns) {
                const revalidations = refetchedCampaigns.map(validateCampaign);
                const avgScoreAfter = revalidations.reduce((sum, v) => sum + v.score, 0) / revalidations.length;

                const avgScoreBefore = validations
                    .filter(v => successful.some(r => r.campaignId === v.campaignId))
                    .reduce((sum, v) => sum + v.score, 0) / successful.length;

                console.log(`\n📈 Score improvement:`);
                console.log(`   Before: ${Math.round(avgScoreBefore)}/100`);
                console.log(`   After:  ${Math.round(avgScoreAfter)}/100`);
                console.log(`   Change: +${Math.round(avgScoreAfter - avgScoreBefore)} points`);
            }
        }
    } else {
        console.log('\n💡 To automatically fix issues, run with --autofix flag');
    }

    console.log('\n╔════════════════════════════════════════════════════════════════════════╗');
    console.log('║                      PIPELINE COMPLETE                                 ║');
    console.log('╚════════════════════════════════════════════════════════════════════════╝\n');
}

// Parse command line arguments
const args = process.argv.slice(2);
const autoFix = args.includes('--autofix');
const testMode = args.includes('--test');
const maxFixes = args.includes('--max')
    ? parseInt(args[args.indexOf('--max') + 1])
    : Infinity;

runQualityPipeline({ autoFix, maxFixes, testMode }).catch(console.error);
