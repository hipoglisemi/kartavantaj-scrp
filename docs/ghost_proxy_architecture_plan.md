# Ghost Proxy Architecture (Future Implementation Plan)

This plan was designed to completely isolate the Kartavantaj scraping infrastructure from the Google Cloud / Gemini AI processing limits by utilizing a "Bridge Database" approach. It allows scaling the free tier of Gemini across multiple accounts without risking the main Hetzner database.

**Decision (Mar 12, 2026):** Deferred to observe the stability of the current 2-3 `GEMINI_API_KEY` rotation system. If rate limits or ban risks become an issue, this architecture will be implemented.

## 🌉 The Supabase Bridge Concept
The system uses the 500MB free tier of a separate, isolated Supabase project as a temporary "queue". Data flows through it and is deleted upon successful processing, ensuring the free tier is never exhausted.

## 🏗️ Architecture Flow

```mermaid
graph TD
    A[Bank Websites] -->|Scrape| B[Scraper Worker (Current Repo)]
    B -->|Writes Raw HTML/Text| C[(Supabase Bridge DB)]
    
    C -->|Reads Raw Text| D[AI Cleaner Worker (New Isolated Repo)]
    D <-->|API Calls using multiple anonymous GEMINI_KEYs| E[Google Gemini AI]
    D -->|Writes Cleaned JSON| C
    
    C -->|Reads Parsed JSON| F[Hetzner Syncer (Main App)]
    F -->|Saves to Live System| G[(Hetzner Main DB)]
    F -.->|Deletes row from Bridge| C
```

## 🚀 Implementation Phases

### Phase 1: Bridge DB Setup
1. Create a brand new, randomly named Supabase project.
2. Create a single table `campaign_queue`:
    - `id` (UUID)
    - `bank_slug`
    - `original_url`
    - `raw_text`
    - `ai_json_result`
    - `status` ('PENDING_AI', 'PARSED', 'ERROR')

### Phase 2: Decoupling Workers
1. **Modify Scraper Worker:** Strip all AI / Vertex code. Make it dump `raw_text` into the Bridge DB with `status='PENDING_AI'`.
2. **Create AI Cleaner Worker:** Create a new Private Repo running on a separate 3-hour cron. It reads `PENDING_AI` rows, processes them with Gemini 3.1 Flash Lite using the rotated keys, and updates rows to `status='PARSED'` with the `ai_json_result`.
3. **Create Hetzner Syncer:** A fast script on the main Kartavantaj server/repo that pulls `PARSED` rows, inserts them into the live Hetzner PostgreSQL DB, and deletes the row from the Bridge DB.

### Security Highlights
- Google only sees the "AI Cleaner Worker" (Repo 2) communicating with its API. It has no knowledge of Hetzner (Repo 3) or the original Scraper (Repo 1).
- Allows adding $N$ number of AI Cleaner forks on separate GitHub accounts/emails to literally infinitely scale the RPM/RPD limits.
