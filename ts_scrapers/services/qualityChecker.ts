/**
 * Quality Checker Module
 * Validates campaign data and identifies issues
 */

export interface ValidationIssue {
    severity: 'critical' | 'warning' | 'info';
    field: string;
    issue: string;
    suggestion?: string;
}

export interface CampaignValidation {
    campaignId: number;
    campaignTitle: string;
    isValid: boolean;
    issues: ValidationIssue[];
    score: number; // 0-100
}

const CRITICAL_FIELDS = ['title', 'category', 'badge_text', 'valid_until', 'bank', 'description', 'earning', 'min_spend'];
const IMPORTANT_FIELDS = ['discount', 'merchant'];
const OPTIONAL_FIELDS = ['max_discount', 'discount_percentage', 'valid_locations', 'brand'];

export function validateCampaign(campaign: any): CampaignValidation {
    const issues: ValidationIssue[] = [];
    let score = 100;

    // 1. Check Critical Fields (40 points)
    CRITICAL_FIELDS.forEach(field => {
        const value = campaign[field];
        if (value === null || value === undefined ||
            (typeof value === 'string' && value.trim() === '') ||
            (Array.isArray(value) && value.length === 0)) {

            issues.push({
                severity: 'critical',
                field,
                issue: `Missing critical field: ${field}`,
                suggestion: 'Re-parse with AI or add missing data'
            });
            score -= 5; // Reduced penalty since we have more critical fields now
        }
    });

    // 2. Check Important Fields (30 points)
    let missingImportant = 0;
    IMPORTANT_FIELDS.forEach(field => {
        if (!campaign[field] ||
            (typeof campaign[field] === 'string' && campaign[field].trim() === '')) {
            missingImportant++;
            issues.push({
                severity: 'warning',
                field,
                issue: `Missing important field: ${field}`,
                suggestion: 'Consider adding this information'
            });
        }
    });
    score -= missingImportant * 7.5; // -7.5 points per important field

    // 3. Badge Consistency Check (15 points)
    const badgeIssues = validateBadge(campaign);
    if (badgeIssues) {
        issues.push(badgeIssues);
        score -= 15;
    }

    // 4. Category Validation (10 points)
    const categoryIssue = validateCategory(campaign);
    if (categoryIssue) {
        issues.push(categoryIssue);
        score -= 10;
    }

    // 5. Date Validation (5 points)
    const dateIssue = validateDates(campaign);
    if (dateIssue) {
        issues.push(dateIssue);
        score -= 5;
    }

    // 6. Math Validation (NEW - Phase 8)
    const mathIssues = validateMath(campaign);
    mathIssues.forEach(issue => {
        issues.push(issue);
        if (issue.severity === 'critical') score -= 15;
        if (issue.severity === 'warning') score -= 10;
        if (issue.severity === 'info') score -= 2;
    });

    // Ensure score doesn't go below 0
    score = Math.max(0, Math.round(score));

    const criticalIssueCount = issues.filter(i => i.severity === 'critical').length;

    return {
        campaignId: campaign.id,
        campaignTitle: campaign.title,
        isValid: score >= 70 && criticalIssueCount === 0, // Must have good score AND no critical issues
        issues,
        score
    };
}

function validateBadge(campaign: any): ValidationIssue | null {
    const { badge_text, title, earning, discount, description } = campaign;

    if (!badge_text) return null;

    const text = `${title} ${earning || ''} ${discount || ''} ${description || ''}`.toLowerCase();

    // Check badge consistency
    const badgeRules: Record<string, string[]> = {
        'TAKSİT': ['taksit', 'peşin fiyatına'],
        'PUAN': ['puan', 'worldpuan', 'hediye puan'],
        'MİL': ['mil'],
        'İNDİRİM': ['indirim', '%', 'tl indirim']
    };

    const expectedBadges: string[] = [];
    Object.entries(badgeRules).forEach(([badge, keywords]) => {
        if (keywords.some(kw => text.includes(kw))) {
            expectedBadges.push(badge);
        }
    });

    // If badge doesn't match any expected, it might be wrong
    if (expectedBadges.length > 0 && !expectedBadges.includes(badge_text)) {
        return {
            severity: 'warning',
            field: 'badge_text',
            issue: `Badge might be incorrect. Current: ${badge_text}, Expected: ${expectedBadges.join(' or ')}`,
            suggestion: 'Re-assign badge based on content'
        };
    }

    return null;
}

function validateCategory(campaign: any): ValidationIssue | null {
    const validCategories = [
        'Market', 'Akaryakıt', 'Yemek', 'Elektronik', 'Giyim',
        'Mobilya', 'E-Ticaret', 'Seyahat', 'Eğlence', 'Sağlık',
        'Spor', 'Kozmetik', 'Eğitim', 'Diğer'
    ];

    if (!campaign.category) {
        return {
            severity: 'critical',
            field: 'category',
            issue: 'Category is missing',
            suggestion: 'Assign appropriate category'
        };
    }

    // Fuzzy match for categories
    const normalizedCategory = campaign.category.trim();
    const found = validCategories.some(cat =>
        cat.toLowerCase().includes(normalizedCategory.toLowerCase()) ||
        normalizedCategory.toLowerCase().includes(cat.toLowerCase())
    );

    if (!found) {
        return {
            severity: 'warning',
            field: 'category',
            issue: `Unknown category: ${campaign.category}`,
            suggestion: `Map to one of: ${validCategories.slice(0, 5).join(', ')}, ...`
        };
    }

    return null;
}

function validateDates(campaign: any): ValidationIssue | null {
    const { valid_from, valid_until } = campaign;

    if (!valid_until) {
        return {
            severity: 'critical',
            field: 'valid_until',
            issue: 'End date is missing',
            suggestion: 'Extract end date from campaign details'
        };
    }

    // Check if date format is valid
    const datePattern = /^\d{4}-\d{2}-\d{2}/;
    if (!datePattern.test(valid_until)) {
        return {
            severity: 'warning',
            field: 'valid_until',
            issue: 'Invalid date format',
            suggestion: 'Use YYYY-MM-DD format'
        };
    }

    // Check if campaign is expired
    const endDate = new Date(valid_until);
    const now = new Date();
    if (endDate < now) {
        return {
            severity: 'info',
            field: 'valid_until',
            issue: 'Campaign has expired',
            suggestion: 'Consider archiving or updating'
        };
    }

    return null;
}

function validateMath(campaign: any): ValidationIssue[] {
    const issues: ValidationIssue[] = [];
    const {
        min_spend,
        earning,
        math_flags = [],
        max_discount,
        discount_percentage,
        required_spend_for_max_benefit,
        ai_suggested_math
    } = campaign;

    // 1. Check math_flags
    if (math_flags.includes('spend_zero_with_signals')) {
        issues.push({
            severity: 'critical',
            field: 'min_spend',
            issue: 'Min spend is 0 but text suggests requirements exist',
            suggestion: 'Review text for "ve üzeri" or "harcamaya" phrases'
        });
    }

    if (math_flags.includes('spend_missing_but_reward_exists')) {
        issues.push({
            severity: 'warning',
            field: 'min_spend',
            issue: 'No min spend found but reward exists',
            suggestion: 'Check if this is a "first spend" or "no-minimum" campaign'
        });
    }

    if (math_flags.includes('reward_le_spend_collision')) {
        issues.push({
            severity: 'warning',
            field: 'earning',
            issue: 'Reward value exceeds or matches min spend',
            suggestion: 'Check if reward and spend values are swapped'
        });
    }

    // 2. Metric Sanity Checks
    if (max_discount && discount_percentage && !required_spend_for_max_benefit) {
        issues.push({
            severity: 'warning',
            field: 'required_spend_for_max_benefit',
            issue: 'Max benefit spend requirement not calculated',
            suggestion: 'Check if % and Max values are correctly extracted'
        });
    }

    // 3. AI Suggestion Info
    if (ai_suggested_math) {
        issues.push({
            severity: 'info',
            field: 'ai_suggested_math',
            issue: 'AI Math Referee provided suggestions',
            suggestion: 'Review and apply AI suggestions if deterministic data is questionable'
        });
    }

    return issues;
}

export function generateValidationReport(validations: CampaignValidation[]): string {
    const critical = validations.filter(v => v.issues.some(i => i.severity === 'critical'));
    const warnings = validations.filter(v => v.issues.some(i => i.severity === 'warning') && !critical.includes(v));
    const valid = validations.filter(v => v.isValid && v.issues.length === 0);

    const avgScore = validations.reduce((sum, v) => sum + v.score, 0) / validations.length;

    let report = `
╔════════════════════════════════════════════════════════════════════════╗
║                    CAMPAIGN QUALITY REPORT                             ║
╚════════════════════════════════════════════════════════════════════════╝

📊 OVERALL STATISTICS
────────────────────────────────────────────────────────────────────────
  Total Campaigns:     ${validations.length}
  ✅ Valid:            ${valid.length} (${Math.round(valid.length / validations.length * 100)}%)
  ⚠️  With Warnings:   ${warnings.length} (${Math.round(warnings.length / validations.length * 100)}%)
  ❌ Critical Issues:  ${critical.length} (${Math.round(critical.length / validations.length * 100)}%)
  
  Average Score:       ${Math.round(avgScore)}/100

`;

    if (critical.length > 0) {
        report += `\n❌ CAMPAIGNS WITH CRITICAL ISSUES (${critical.length})\n`;
        report += '─'.repeat(76) + '\n';
        critical.slice(0, 10).forEach((v, i) => {
            report += `\n${i + 1}. ${v.campaignTitle}\n`;
            report += `   Score: ${v.score}/100\n`;
            const criticalIssues = v.issues.filter(issue => issue.severity === 'critical');
            criticalIssues.forEach(issue => {
                report += `   • ${issue.issue}\n`;
            });
        });
        if (critical.length > 10) {
            report += `\n... and ${critical.length - 10} more\n`;
        }
    }

    return report;
}
