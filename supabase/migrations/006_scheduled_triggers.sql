-- 006_scheduled_triggers.sql
--
-- WHY THIS EXISTS
-- GitHub runs scheduled ("cron") workflows on a best-effort basis: runs start
-- late and are silently dropped under load. In Nov 2026 our hourly price scrape
-- ran ~4 times a day and the 15-minute Patreon import ~4 times a day.
-- Manually dispatched runs start within seconds, so Supabase now acts as the
-- clock: pg_cron fires on time and pg_net calls GitHub's workflow_dispatch API.
-- The workflows keep a slower GitHub schedule as a backup, and use a
-- concurrency group so an overlap queues instead of running twice at once.
--
-- PREREQUISITE (one-off, NOT in this file so the token never lands in git):
-- a fine-grained GitHub token, scoped to SLC-Online/fpl-price-tracker with
-- "Actions: Read and write", stored in Supabase Vault:
--
--   select vault.create_secret('github_pat_...', 'github_actions_token',
--     'Dispatch fpl-price-tracker workflows (Actions read/write)');
--
-- To replace an expired token later:
--   select vault.update_secret(
--     (select id from vault.secrets where name = 'github_actions_token'),
--     'github_pat_NEW...');
--
-- Safe to re-run: everything below is create-or-replace / upsert.

create extension if not exists pg_cron;
create extension if not exists pg_net;

-- A schema that isn't exposed through the Data API, so nobody can call the
-- dispatcher over the website's API.
create schema if not exists private;
revoke all on schema private from public, anon, authenticated;

create or replace function private.dispatch_github_workflow(workflow text)
returns bigint
language plpgsql
security definer
set search_path = ''
as $$
declare
    token text;
begin
    select decrypted_secret into token
    from vault.decrypted_secrets
    where name = 'github_actions_token';

    if token is null then
        raise exception 'github_actions_token is missing from Supabase Vault';
    end if;

    -- GitHub answers 204 No Content when the run is queued.
    return net.http_post(
        url := 'https://api.github.com/repos/SLC-Online/fpl-price-tracker/actions/workflows/'
               || workflow || '/dispatches',
        headers := jsonb_build_object(
            'Authorization', 'Bearer ' || token,
            'Accept', 'application/vnd.github+json',
            'X-GitHub-Api-Version', '2022-11-28',
            'User-Agent', 'fpl-sorted-scheduler',
            'Content-Type', 'application/json'
        ),
        body := jsonb_build_object('ref', 'main'),
        timeout_milliseconds := 10000
    );
end;
$$;

revoke all on function private.dispatch_github_workflow(text) from public, anon, authenticated;

-- Schedules (UTC). Off the top of the hour, which is when everyone's jobs fire.
-- cron.schedule with an existing name replaces that job.
select cron.schedule(
    'fpl-price-scrape-hourly',
    '7 * * * *',
    $$select private.dispatch_github_workflow('daily_scrape.yml')$$
);

select cron.schedule(
    'patreon-import-every-15-min',
    '2,17,32,47 * * * *',
    $$select private.dispatch_github_workflow('scrape_patreon.yml')$$
);

-- CHECKING IT WORKS
--   select jobname, schedule, active from cron.job;
--   select jobname, status, return_message, start_time
--     from cron.job_run_details join cron.job using (jobid)
--     order by start_time desc limit 10;
--   -- GitHub's reply to each call (204 = run queued, 401/403 = token problem):
--   select id, status_code, created from net._http_response order by created desc limit 10;
