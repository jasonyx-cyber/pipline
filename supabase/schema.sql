create table if not exists public.applications (
    id uuid primary key default gen_random_uuid(),
    user_id uuid not null references auth.users (id) on delete cascade,
    company text not null check (char_length(btrim(company)) between 1 and 160),
    role text not null check (char_length(btrim(role)) between 1 and 160),
    link text not null default ''
        check (char_length(link) <= 2048 and (link = '' or link ~* '^https?://')),
    notes text not null default '' check (char_length(notes) <= 10000),
    status text not null default 'Applied'
        check (status in ('Applied', 'Interview', 'Offer', 'Rejected')),
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now()
);

create table if not exists public.application_history (
    id bigint generated always as identity primary key,
    application_id uuid not null references public.applications (id) on delete cascade,
    user_id uuid not null references auth.users (id) on delete cascade,
    status text not null check (status in ('Applied', 'Interview', 'Offer', 'Rejected')),
    changed_at timestamptz not null default now()
);

create index if not exists applications_user_updated_idx
    on public.applications (user_id, updated_at desc);
create index if not exists application_history_owner_time_idx
    on public.application_history (user_id, application_id, changed_at desc);

alter table public.applications enable row level security;
alter table public.application_history enable row level security;

drop policy if exists "Users can read their applications" on public.applications;
create policy "Users can read their applications" on public.applications
    for select to authenticated using (user_id = (select auth.uid()));
drop policy if exists "Users can create their applications" on public.applications;
create policy "Users can create their applications" on public.applications
    for insert to authenticated with check (user_id = (select auth.uid()));
drop policy if exists "Users can update their applications" on public.applications;
create policy "Users can update their applications" on public.applications
    for update to authenticated
    using (user_id = (select auth.uid()))
    with check (user_id = (select auth.uid()));
drop policy if exists "Users can delete their applications" on public.applications;
create policy "Users can delete their applications" on public.applications
    for delete to authenticated using (user_id = (select auth.uid()));

drop policy if exists "Users can read their application history" on public.application_history;
create policy "Users can read their application history" on public.application_history
    for select to authenticated using (user_id = (select auth.uid()));

create or replace function public.track_application_status()
returns trigger
language plpgsql
security definer
set search_path = ''
as $$
begin
    if tg_op = 'INSERT' or new.status is distinct from old.status then
        insert into public.application_history (application_id, user_id, status)
        values (new.id, new.user_id, new.status);
    end if;

    return new;
end;
$$;

create or replace function public.set_application_updated_at()
returns trigger
language plpgsql
set search_path = ''
as $$
begin
    new.updated_at := now();
    return new;
end;
$$;

drop trigger if exists track_application_status on public.applications;
create trigger track_application_status
    after insert or update on public.applications
    for each row execute function public.track_application_status();
drop trigger if exists set_application_updated_at on public.applications;
create trigger set_application_updated_at
    before update on public.applications
    for each row execute function public.set_application_updated_at();

revoke all on function public.track_application_status() from public, anon;
grant execute on function public.track_application_status() to authenticated;
revoke all on function public.set_application_updated_at() from public, anon;
grant execute on function public.set_application_updated_at() to authenticated;

grant select, insert, update, delete on public.applications to authenticated;
grant select on public.application_history to authenticated;
grant usage, select on sequence public.application_history_id_seq to authenticated;
