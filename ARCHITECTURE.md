# Architecture: Free Now → Commercial Later

## Principle
Build with the same framework and patterns you'd use at scale, but deploy free.
The switch from "free static hosting" to "production SaaS" should be a deployment change, not a rewrite.

## Recommendation: SvelteKit (full-stack) + Supabase

### Why SvelteKit Full-Stack (not static)

The DASHBOARD_PLAN.md originally proposed SvelteKit with adapter-static → GitHub Pages.
That's wrong for where this is heading.

SvelteKit is a **full-stack framework**. It has:
- Server-side routes (API endpoints) → `+server.ts` files
- Server-side rendering → fast, SEO-friendly
- Form actions → handle uploads, mutations without separate API
- Middleware → auth checks, rate limiting
- But **also** works as a static site during development

**Today:** Deploy on Vercel or Cloudflare Pages free tier (both support SvelteKit natively with server functions)
**Later:** Same code, same deploy targets, just add auth and paid features

### Why Supabase (not SQLite in git)

SQLite-in-git was fine for the scraper prototype. But:
- Can't have multiple writers (cloud scraper + admin uploads + user actions)
- No real-time subscriptions
- No row-level security
- No auth

**Supabase free tier gives us:**
- Postgres database (500MB, unlimited API requests)
- Built-in auth (email, Google, magic links) — zero code for login/signup
- Row-level security (premium users see premium data, free users don't)
- Real-time subscriptions (push price changes to connected clients)
- Storage (for CSV uploads)
- Edge functions (serverless, for the scraper)

**Today:** Free tier. Same schema we already have, just in Postgres instead of SQLite.
**Later:** Upgrade to Pro ($25/mo) when you have paying users. No code changes.

### Why NOT Next.js

- Heavier, more boilerplate, React's mental model is more complex
- Vendor-locked to Vercel for best experience
- SvelteKit is lighter, faster, better DX for a data dashboard
- Svelte 5's reactivity model is ideal for real-time data updates

## Architecture Diagram

```
┌─────────────────────────────────────────────────────────┐
│                    FRONTEND (SvelteKit)                   │
│                                                           │
│  /              Dashboard (price movers, transfer flow)  │
│  /player/[id]   Player detail + charts                   │
│  /compare       Compare players                          │
│  /alerts        Price alerts (premium)                   │
│  /my-team       Personal squad tracker (auth required)   │
│  /admin         CSV upload, data management (you only)   │
│                                                           │
├─────────────────────────────────────────────────────────┤
│                  API LAYER (SvelteKit server routes)      │
│                                                           │
│  /api/players          GET player list + latest data     │
│  /api/player/[id]      GET player timeline               │
│  /api/price-changes    GET detected changes              │
│  /api/import           POST CSV upload + process         │
│  /api/alerts           POST create/manage alerts         │
│                                                           │
├─────────────────────────────────────────────────────────┤
│                     SUPABASE                              │
│                                                           │
│  Database (Postgres)    Same schema as fpl_tracker.db    │
│  Auth                   Users, sessions, roles           │
│  Storage                CSV uploads, exports             │
│  Real-time              Price change push notifications  │
│  Edge Functions         Hourly scraper (replaces GH Act) │
│                                                           │
└─────────────────────────────────────────────────────────┘
```

## Migration Path

### Phase 1: NOW (Free, solo use)
- **Deploy:** Vercel free tier (SvelteKit + server routes)
- **Database:** Supabase free tier (migrate SQLite → Postgres)
- **Scraper:** Keep GitHub Actions for now (reliable, free)
- **Auth:** None yet (admin page protected by simple env-var password)
- **Cost:** £0

### Phase 2: BETA (Invite friends, test with real users)
- **Auth:** Turn on Supabase Auth (Google sign-in, magic links)
- **Features:** Personal squad tracking, watchlists, basic alerts
- **Scraper:** Move to Supabase Edge Function (or keep GH Actions)
- **Cost:** Still £0 (free tiers)

### Phase 3: LAUNCH (Public, freemium)
- **Premium tier:** Stripe integration via SvelteKit server routes
- **Features:** Real-time alerts, AI-powered predictions, advanced analytics
- **Row-level security:** Free users see delayed data, premium see live
- **Cost:** Supabase Pro $25/mo + Vercel Pro $20/mo = $45/mo (covered by ~5 subscribers)

### Phase 4: SCALE
- **Custom domain**, proper branding
- **Mobile app** (PWA from the same SvelteKit codebase)
- **More data sources** (ext_ tables in Supabase)
- **Cost:** Scales with usage, Supabase/Vercel pricing is linear

## What Changes Between Phases

| Component | Phase 1 | Phase 3 | Code Change? |
|-----------|---------|---------|-------------|
| Frontend | SvelteKit on Vercel | Same | None |
| API | SvelteKit server routes | Same | Add auth middleware |
| Database | Supabase free Postgres | Supabase Pro | None (same schema) |
| Auth | Env-var password | Supabase Auth | Add, not rewrite |
| Payments | None | Stripe | Add server route |
| Scraper | GitHub Actions | Supabase Edge Fn | Minor rewrite |
| Hosting | Vercel free | Vercel Pro | Config change |
| AI | Gemini free | Same | None |

## Key Decisions

1. **SvelteKit** — not a static site generator. Full-stack from day one.
2. **Supabase** — not SQLite. Real database from day one.
3. **Vercel** — not GitHub Pages. Supports server routes from day one.
4. **API-first** — frontend talks to API routes, not directly to DB. Means mobile app / third-party integrations come free later.
5. **TypeScript** — type safety across frontend and API. Worth the setup cost.

## Tech Stack (Final)

| Layer | Choice | Free Tier | Paid |
|-------|--------|-----------|------|
| Framework | SvelteKit 2 + TypeScript | ✓ | Same |
| Styling | Tailwind CSS 4 | ✓ | Same |
| Charts | ApexCharts | ✓ | Same |
| Hosting | Vercel | 100GB bandwidth | $20/mo |
| Database | Supabase Postgres | 500MB, unlimited API | $25/mo |
| Auth | Supabase Auth | 50k MAU | Same |
| Storage | Supabase Storage | 1GB | Scales |
| Real-time | Supabase Realtime | 200 connections | Scales |
| AI | Google Gemini | 1000 req/day | Cheap |
| Scraper | GitHub Actions | 2000 min/mo | Same |
| Payments | Stripe | Free until revenue | 2.9% + 30¢ |
| Domain | (later) | - | £10/yr |

## What We Keep From Current Work

Everything important carries over:
- **Scraper logic** (`daily_scrape.py`) → becomes Supabase Edge Function or stays as GH Action writing to Supabase
- **Import logic** (`import_csv.py` + `ai_resolve.py`) → becomes API route in SvelteKit
- **Schema** (snapshots, players, player_snapshots, etc.) → direct port to Postgres
- **Name matching** → same Python/JS logic
- **All accumulated data** → migrated to Supabase Postgres

## Immediate Next Steps

1. Create Supabase project (free)
2. Migrate schema from SQLite → Postgres
3. Point scraper at Supabase instead of local SQLite
4. Scaffold SvelteKit project with Tailwind + Supabase client
5. Build first pages (dashboard, player detail)
6. Deploy to Vercel
