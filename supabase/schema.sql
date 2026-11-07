-- Caloric Scan: аккаунты, лимит сканов и платежи.
-- Вставь этот файл в Supabase → SQL Editor и выполни целиком.
-- Сутки лимита считаются по Москве (Europe/Moscow, UTC+3, без перехода на летнее время).

create table if not exists public.profiles (
  id uuid primary key references auth.users (id) on delete cascade,
  email text,
  plan text not null default 'free' check (plan in ('free', 'premium')),
  premium_until timestamptz
);

create table if not exists public.scans (
  id bigint generated always as identity primary key,
  user_id uuid not null references auth.users (id) on delete cascade,
  created_at timestamptz not null default now(),
  dish text,
  kcal numeric,
  protein numeric,
  fat numeric,
  carbs numeric,
  grams numeric
);

create index if not exists scans_user_created_idx
  on public.scans (user_id, created_at desc);

-- Счётчик не в scans: иначе клиент мог бы обойти лимит, не вставляя строку,
-- или вставить строку без проверки. Меняет счётчик только функция consume_scan.
create table if not exists public.scan_quota (
  user_id uuid not null references auth.users (id) on delete cascade,
  day date not null,
  used integer not null default 0 check (used >= 0),
  primary key (user_id, day)
);

create table if not exists public.payments (
  id bigint generated always as identity primary key,
  user_id uuid not null references auth.users (id) on delete cascade,
  provider text not null,
  provider_payment_id text,
  amount numeric not null,
  plan text not null check (plan in ('month', 'year')),
  status text not null default 'pending' check (status in ('pending', 'succeeded', 'canceled')),
  created_at timestamptz not null default now(),
  unique (provider, provider_payment_id)
);

alter table public.profiles enable row level security;
alter table public.scans enable row level security;
alter table public.scan_quota enable row level security;
alter table public.payments enable row level security;

-- Профиль: читать свой можно, писать plan и premium_until — нельзя.
-- Нет политики UPDATE/INSERT для authenticated, поэтому свой тариф пользователь не меняет.
drop policy if exists profiles_select_own on public.profiles;
create policy profiles_select_own
  on public.profiles
  for select
  to authenticated
  using (id = auth.uid());

drop policy if exists scans_select_own on public.scans;
create policy scans_select_own
  on public.scans
  for select
  to authenticated
  using (user_id = auth.uid());

drop policy if exists scans_insert_own on public.scans;
create policy scans_insert_own
  on public.scans
  for insert
  to authenticated
  with check (user_id = auth.uid());

drop policy if exists scans_delete_own on public.scans;
create policy scans_delete_own
  on public.scans
  for delete
  to authenticated
  using (user_id = auth.uid());

drop policy if exists payments_select_own on public.payments;
create policy payments_select_own
  on public.payments
  for select
  to authenticated
  using (user_id = auth.uid());

-- scan_quota без политик: клиент её не видит и не пишет.
revoke all on public.scan_quota from anon, authenticated;

create or replace function public.handle_new_user()
returns trigger
language plpgsql
security definer
set search_path = public
as $$
begin
  insert into public.profiles (id, email, plan)
  values (new.id, new.email, 'free')
  on conflict (id) do update set email = excluded.email;
  return new;
end;
$$;

drop trigger if exists on_auth_user_created on auth.users;
create trigger on_auth_user_created
  after insert on auth.users
  for each row execute function public.handle_new_user();

create or replace function public.moscow_today()
returns date
language sql
stable
as $$
  select (timezone('Europe/Moscow', now()))::date;
$$;

-- Только чтение: для надписи «Осталось N из 3».
create or replace function public.scan_status()
returns jsonb
language plpgsql
security definer
set search_path = public
as $$
declare
  uid uuid := auth.uid();
  premium boolean := false;
  until_at timestamptz;
  used_now integer := 0;
begin
  if uid is null then
    raise exception 'not authenticated';
  end if;
  select plan = 'premium' and premium_until is not null and premium_until > now(), premium_until
    into premium, until_at
  from public.profiles
  where id = uid;
  if premium then
    return jsonb_build_object('allowed', true, 'premium', true, 'used', 0, 'limit', null, 'premium_until', until_at);
  end if;
  select used into used_now
  from public.scan_quota
  where user_id = uid and day = public.moscow_today();
  used_now := coalesce(used_now, 0);
  return jsonb_build_object(
    'allowed', used_now < 3,
    'premium', false,
    'used', used_now,
    'limit', 3
  );
end;
$$;

-- Проверяет лимит и, если скан ещё можно, увеличивает счётчик на 1.
-- Бесплатный план: 3 скана за московские сутки. Премиум не ограничен и счётчик не тратит.
create or replace function public.consume_scan()
returns jsonb
language plpgsql
security definer
set search_path = public
as $$
declare
  uid uuid := auth.uid();
  premium boolean := false;
  until_at timestamptz;
  used_now integer;
begin
  if uid is null then
    raise exception 'not authenticated';
  end if;
  select plan = 'premium' and premium_until is not null and premium_until > now(), premium_until
    into premium, until_at
  from public.profiles
  where id = uid;
  if premium then
    return jsonb_build_object('allowed', true, 'premium', true, 'used', 0, 'limit', null, 'premium_until', until_at);
  end if;

  insert into public.scan_quota as q (user_id, day, used)
  values (uid, public.moscow_today(), 1)
  on conflict (user_id, day)
  do update set used = q.used + 1
  where q.used < 3
  returning q.used into used_now;

  if used_now is null then
    select used into used_now
    from public.scan_quota
    where user_id = uid and day = public.moscow_today();
    return jsonb_build_object('allowed', false, 'premium', false, 'used', coalesce(used_now, 3), 'limit', 3);
  end if;

  return jsonb_build_object('allowed', true, 'premium', false, 'used', used_now, 'limit', 3);
end;
$$;

revoke all on function public.scan_status() from public;
revoke all on function public.consume_scan() from public;
revoke all on function public.handle_new_user() from public;
grant execute on function public.scan_status() to authenticated;
grant execute on function public.consume_scan() to authenticated;
