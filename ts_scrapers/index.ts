/**
 * Main entry point
 * Runs all scrapers
 */

import { runWorldScraper } from './scrapers/yapikredi/world';

async function main() {
    console.log('🚀 KartAvantaj Scraper Starting...\n');
    console.log('='.repeat(50) + '\n');

    try {
        // WorldCard
        console.log('📍 WORLDCARD\n');
        await runWorldScraper();

        console.log('\n' + '='.repeat(50));
        console.log('✅ All scrapers completed!\n');

    } catch (error) {
        console.error('\n💥 Scraper failed:', error);
        process.exit(1);
    }
}

main();
