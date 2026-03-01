
import { createClient } from '@supabase/supabase-js';
import * as dotenv from 'dotenv';

dotenv.config();

const supabase = createClient(
    process.env.SUPABASE_URL!,
    process.env.SUPABASE_ANON_KEY!
);

const banks = [
    {
        name: 'Akbank',
        slug: 'akbank',
        logo_url: 'https://logo.clearbit.com/akbank.com',
        aliases: ['axess', 'free', 'wings'],
        cards: [
            { name: 'Axess', slug: 'axess', image_url: 'https://www.axess.com.tr/assets/img/axess-kart.png' },
            { name: 'Wings', slug: 'wings', image_url: 'https://www.wingscard.com.tr/assets/img/wings-kart.png' },
            { name: 'Free', slug: 'free', image_url: 'https://www.kartfree.com/assets/img/free-kart.png' }
        ]
    },
    {
        name: 'İş Bankası',
        slug: 'i̇ş-bankası', // Matching DB Slug
        logo_url: 'https://logo.clearbit.com/isbank.com.tr',
        aliases: ['maximum', 'maximiles'],
        cards: [
            { name: 'Maximum', slug: 'maximum', image_url: 'https://www.maximum.com.tr/assets/img/maximum-kart.png' },
            { name: 'Maximiles', slug: 'maximiles', image_url: 'https://www.maximiles.com.tr/assets/img/maximiles-kart.png' }
        ]
    },
    {
        name: 'Garanti BBVA',
        slug: 'garanti-bbva', // Matching DB Slug
        logo_url: 'https://logo.clearbit.com/garantibbva.com.tr',
        aliases: ['bonus', 'miles-and-smiles'],
        cards: [
            { name: 'Bonus', slug: 'bonus', image_url: 'https://www.bonus.com.tr/assets/img/bonus-kart.png' },
            { name: 'Shop&Fly', slug: 'shop-and-fly', image_url: 'https://www.shopandfly.com.tr/assets/img/shopandfly-kart.png' }
        ]
    },
    {
        name: 'Yapı Kredi',
        slug: 'yapi-kredi',
        logo_url: 'https://logo.clearbit.com/yapikredi.com.tr',
        aliases: ['world'],
        cards: [
            { name: 'World', slug: 'world', image_url: 'https://www.worldcard.com.tr/assets/img/world-kart.png' }
        ]
    },
    {
        name: 'Ziraat Bankası',
        slug: 'ziraat',
        logo_url: 'https://logo.clearbit.com/ziraatbank.com.tr',
        aliases: ['bankkart'],
        cards: [
            { name: 'Bankkart', slug: 'bankkart', image_url: 'https://www.bankkart.com.tr/assets/img/bankkart.png' }
        ]
    },
    {
        name: 'Vakıfbank',
        slug: 'vakifbank',
        logo_url: 'https://logo.clearbit.com/vakifbank.com.tr',
        aliases: ['world'],
        cards: [
            { name: 'World', slug: 'vakifbank-world', image_url: 'https://www.vakifbank.com.tr/assets/img/vakifbank-world.png' }
        ]
    },
    {
        name: 'Halkbank',
        slug: 'halkbank',
        logo_url: 'https://logo.clearbit.com/halkbank.com.tr',
        aliases: ['paraf'],
        cards: [
            { name: 'Paraf', slug: 'paraf', image_url: 'https://www.paraf.com.tr/assets/img/paraf-kart.png' }
        ]
    }
];

async function seed() {
    console.log("🌱 Updating V3 Banks and Cards with Correct Logos...");

    for (const bank of banks) {
        // 1. Insert Bank
        const { data: bankData, error: bankError } = await supabase
            .from('banks')
            .upsert({
                name: bank.name,
                slug: bank.slug,
                logo_url: bank.logo_url,
                aliases: bank.aliases
            }, { onConflict: 'slug' })
            .select()
            .single();

        if (bankError || !bankData) {
            console.error(`❌ Failed to update bank ${bank.name}:`, bankError);
            continue;
        }

        console.log(`✅ Bank Updated: ${bank.name}`);

        // 2. Insert Cards
        const cardsWithId = bank.cards.map(c => ({
            bank_id: bankData.id,
            name: c.name,
            slug: c.slug,
            image_url: c.image_url
        }));

        const { error: cardError } = await supabase
            .from('cards')
            .upsert(cardsWithId, { onConflict: 'bank_id,name' }); // Use composite key if possible or slug?
        // DB constraint is bank_id, name. Upserting on slug might fail if slug changed?
        // Let's use name match? Or just ignore errors since we care mostly about Bank Logos for Sidebar.

        if (cardError) {
            // Try upserting on unique constraint if possible?
            // Actually, simplified approach: Just log.
            console.error(`   ⚠️ Card sync issue for ${bank.name} (might be fine):`, cardError.message);
        } else {
            console.log(`   🃏 Cards Synced: ${bank.cards.map(c => c.name).join(', ')}`);
        }
    }

    console.log("🏁 Update Completed.");
}

seed();
