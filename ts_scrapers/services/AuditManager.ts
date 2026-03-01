import { createClient } from '@supabase/supabase-js';
import { parseSurgical } from './geminiParser';
import { generateSectorSlug } from '../utils/slugify';
import { lookupIDs } from '../utils/idMapper';

// Types for audit results
export interface AuditNeeds {
    dates: boolean;
    math: boolean;
    cards: boolean;
    brand: boolean;
    participation: boolean;
}

export class AuditManager {
    private supabase;

    constructor() {
        this.supabase = createClient(
            process.env.SUPABASE_URL!,
            process.env.SUPABASE_ANON_KEY!
        );
    }

    /**
     * Identifies what fields need auditing for a given campaign
     */
    getAuditNeeds(c: any): AuditNeeds {
        const needs: AuditNeeds = {
            dates: false,
            math: false,
            cards: false,
            brand: false,
            participation: false
        };

        // 📅 Date Logic: null, far future, or 2026-12-31 placeholder
        if (!c.valid_until || c.valid_until.startsWith('2026-12-31') || new Date(c.valid_until) > new Date('2026-11-01')) {
            needs.dates = true;
        }

        // 🔢 Math Logic: High reward/spend ratio or specific keywords (excluding already fixed ones)
        const isSuspiciousMath = c.min_spend > 0 && c.max_discount > 0 && c.max_discount > c.min_spend * 0.7;
        const hasTieredKeywords = /her .*tl/i.test(c.description + (c.conditions?.join(' ') || ''));
        if ((isSuspiciousMath || hasTieredKeywords) && !c.math_flags?.includes('fixed_cumulative_v2')) {
            needs.math = true;
        }

        // 💳 Card Logic: Empty or generic
        if (!c.eligible_customers || (Array.isArray(c.eligible_customers) && c.eligible_customers.length === 0)) {
            needs.cards = true;
        }

        // 🏷️ Brand/Sector Logic: null brand, "Diğer" category, or MISSING IDs
        if (!c.brand || c.category === 'Diğer' || !c.category || !c.brand_id || !c.sector_id) {
            needs.brand = true;
        }

        // 📣 Participation Logic: Empty or unclear
        if (!c.participation_method || c.participation_method.length < 5) {
            needs.participation = true;
        }

        return needs;
    }

    /**
     * Performs a unified surgical parse for all needy fields
     */
    async repairCampaign(c: any, needs: AuditNeeds): Promise<boolean> {
        const fieldsToFix: string[] = [];
        if (needs.dates) fieldsToFix.push('valid_until', 'valid_from');
        if (needs.math) fieldsToFix.push('min_spend', 'max_discount', 'earning');
        if (needs.cards) fieldsToFix.push('eligible_customers');
        if (needs.brand) fieldsToFix.push('brand', 'category');
        if (needs.participation) fieldsToFix.push('participation_method');

        if (fieldsToFix.length === 0) return false;

        console.log(`   🛠️  Repairing fields: [${fieldsToFix.join(', ')}]`);

        try {
            const surgicalResult = await parseSurgical(
                c.description + '\n' + (c.conditions?.join('\n') || ''),
                c,
                fieldsToFix,
                c.url,
                c.bank
            );

            if (!surgicalResult) return false;

            const updates: any = {
                auto_corrected: true,
                math_flags: [...(c.math_flags || []), 'master_audit_v1']
            };

            // Map surgical results to updates
            fieldsToFix.forEach(field => {
                if (surgicalResult[field] !== undefined) {
                    updates[field] = surgicalResult[field];
                    // Special case for category/sector_slug
                    if (field === 'category') {
                        updates.sector_slug = generateSectorSlug(surgicalResult.category);
                    }
                }
            });

            // CRITICAL: Lookup and assign IDs for consistency
            const finalBrand = updates.brand || c.brand;
            const finalSectorSlug = updates.sector_slug || c.sector_slug;
            const ids = await lookupIDs(c.bank, c.card_name, finalBrand, finalSectorSlug);
            Object.assign(updates, ids);

            const { error } = await this.supabase
                .from('campaigns')
                .update(updates)
                .eq('id', c.id);

            if (error) {
                console.error(`   ❌ Update Error for ID ${c.id}: ${error.message}`);
                return false;
            }

            return true;
        } catch (err) {
            console.error(`   ❌ Surgical parse failed for ID ${c.id}:`, err);
            return false;
        }
    }

    /**
     * Runs mass audit on all active campaigns
     */
    async runMasterAudit(limit: number = 50) {
        console.log(`\n🚀 Starting Master Audit (Limit: ${limit})...`);

        const { data, error } = await this.supabase
            .from('campaigns')
            .select('*')
            .eq('is_active', true)
            .order('id', { ascending: false })
            .limit(limit);

        if (error) {
            console.error('❌ Error fetching campaigns:', error.message);
            return;
        }

        let fixedCount = 0;
        let checkedCount = 0;

        for (const c of data) {
            const needs = this.getAuditNeeds(c);
            const needsCheck = Object.values(needs).some(v => v === true);

            if (needsCheck) {
                console.log(`\n🩺 Auditing ID ${c.id}: "${c.title.substring(0, 40)}..."`);
                const fixed = await this.repairCampaign(c, needs);
                if (fixed) fixedCount++;
            }
            checkedCount++;
        }

        console.log(`\n✅ Master Audit Finished.`);
        console.log(`📊 Checked: ${checkedCount} | Fixed: ${fixedCount}\n`);
    }
}
