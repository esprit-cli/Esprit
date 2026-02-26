-- One-time free-scan claims.
-- Enforces at most one lifetime free cloud scan per user via primary key on user_id.

create table if not exists public.free_scan_claims (
    user_id uuid primary key references public.profiles (id) on delete cascade,
    scan_id uuid not null references public.scans (id) on delete cascade,
    claimed_at timestamptz not null default now()
);

create index if not exists idx_free_scan_claims_scan_id
    on public.free_scan_claims (scan_id);
