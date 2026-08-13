-- Coaching session lifecycle
--
-- Simplified public example based on the production QA platform.
--
-- The database enforces the lifecycle instead of trusting the UI to submit
-- valid status changes.

begin;

create table if not exists public.coaching_sessions (
  id uuid primary key default gen_random_uuid(),

  call_id uuid unique not null,
  agent_external_id text not null,
  agent_name text not null,

  supervisor_id uuid not null,
  created_by uuid not null,

  status text not null default 'draft'
    check (
      status in (
        'draft',
        'scheduled',
        'held',
        'acknowledged',
        'follow_up_due',
        'closed',
        'cancelled'
      )
    ),

  topic text not null default 'One-on-one call coaching',
  coaching_summary text,
  required_action text,
  supervisor_notes text,

  scheduled_at timestamptz,
  held_at timestamptz,

  agent_comments text,
  agent_acknowledgment_name text,
  agent_acknowledged_at timestamptz,
  acknowledgment_recorded_by uuid,

  follow_up_due_date date,
  follow_up_result text,
  follow_up_completed_at timestamptz,

  closed_at timestamptz,
  cancelled_at timestamptz,

  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create table if not exists public.coaching_session_events (
  id uuid primary key default gen_random_uuid(),

  coaching_session_id uuid not null
    references public.coaching_sessions(id)
    on delete cascade,

  event_type text not null
    check (event_type in ('created', 'status_changed')),

  from_status text,
  to_status text not null,
  actor_id uuid,

  details jsonb not null default '{}'::jsonb,

  created_at timestamptz not null default now()
);

create index if not exists
  idx_coaching_sessions_status_due
on public.coaching_sessions (
  status,
  follow_up_due_date,
  updated_at desc
);

create index if not exists
  idx_coaching_sessions_agent
on public.coaching_sessions (
  agent_external_id,
  created_at desc
);


create or replace function public.validate_coaching_transition()
returns trigger
language plpgsql
security definer
set search_path = public
as $$
begin
  if tg_op = 'INSERT' then
    if new.status <> 'draft' then
      raise exception
        'New coaching sessions must begin in draft status.';
    end if;

    return new;
  end if;

  -- The session must always remain attached to the original reviewed call
  -- and representative.
  if new.call_id is distinct from old.call_id
     or new.agent_external_id is distinct from old.agent_external_id
     or new.agent_name is distinct from old.agent_name
     or new.created_by is distinct from old.created_by then
    raise exception
      'Coaching session source identity cannot be changed.';
  end if;

  -- Once an acknowledgment has been recorded, the historical record is
  -- immutable.
  if old.agent_acknowledged_at is not null and (
    new.agent_acknowledgment_name
      is distinct from old.agent_acknowledgment_name
    or new.agent_acknowledged_at
      is distinct from old.agent_acknowledged_at
    or new.acknowledgment_recorded_by
      is distinct from old.acknowledgment_recorded_by
  ) then
    raise exception
      'Recorded agent acknowledgment cannot be changed.';
  end if;

  if new.status is distinct from old.status and not (
    (old.status = 'draft'
      and new.status in ('scheduled', 'cancelled'))

    or (old.status = 'scheduled'
      and new.status in ('held', 'cancelled'))

    or (old.status = 'held'
      and new.status in (
        'acknowledged',
        'follow_up_due',
        'closed'
      ))

    or (old.status = 'acknowledged'
      and new.status in ('follow_up_due', 'closed'))

    or (old.status = 'follow_up_due'
      and new.status = 'closed')
  ) then
    raise exception
      'Invalid coaching status transition from % to %.',
      old.status,
      new.status;
  end if;

  if new.status = 'scheduled'
     and new.scheduled_at is null then
    raise exception
      'A scheduled session requires a meeting date and time.';
  end if;

  if new.status = 'held'
     and new.held_at is null then
    new.held_at = now();
  end if;

  if new.status = 'acknowledged' then
    if nullif(
      btrim(new.agent_acknowledgment_name),
      ''
    ) is null then
      raise exception
        'Agent acknowledgment requires a name.';
    end if;

    new.agent_acknowledged_at =
      coalesce(new.agent_acknowledged_at, now());

    new.acknowledgment_recorded_by =
      coalesce(
        new.acknowledgment_recorded_by,
        auth.uid()
      );
  end if;

  if new.status = 'follow_up_due'
     and new.follow_up_due_date is null then
    raise exception
      'Follow-up status requires a due date.';
  end if;

  if new.status = 'closed' then
    new.closed_at =
      coalesce(new.closed_at, now());

    if old.status = 'follow_up_due' then
      new.follow_up_completed_at =
        coalesce(
          new.follow_up_completed_at,
          now()
        );
    end if;
  end if;

  if new.status = 'cancelled' then
    new.cancelled_at =
      coalesce(new.cancelled_at, now());
  end if;

  new.updated_at = now();

  return new;
end;
$$;


create or replace function public.log_coaching_event()
returns trigger
language plpgsql
security definer
set search_path = public
as $$
begin
  if tg_op = 'INSERT' then
    insert into public.coaching_session_events (
      coaching_session_id,
      event_type,
      from_status,
      to_status,
      actor_id
    )
    values (
      new.id,
      'created',
      null,
      new.status,
      coalesce(auth.uid(), new.created_by)
    );

  elsif new.status is distinct from old.status then
    insert into public.coaching_session_events (
      coaching_session_id,
      event_type,
      from_status,
      to_status,
      actor_id
    )
    values (
      new.id,
      'status_changed',
      old.status,
      new.status,
      auth.uid()
    );
  end if;

  return new;
end;
$$;


drop trigger if exists
  validate_coaching_transition_trigger
on public.coaching_sessions;

create trigger validate_coaching_transition_trigger
before insert or update
on public.coaching_sessions
for each row
execute function public.validate_coaching_transition();


drop trigger if exists
  log_coaching_event_trigger
on public.coaching_sessions;

create trigger log_coaching_event_trigger
after insert or update of status
on public.coaching_sessions
for each row
execute function public.log_coaching_event();


alter table public.coaching_sessions
  enable row level security;

alter table public.coaching_session_events
  enable row level security;


-- Example policy shape only.
-- Production authorization also scopes managers to the teams they are allowed
-- to review.

create policy coaching_manager_read
on public.coaching_sessions
for select
to authenticated
using (
  exists (
    select 1
    from public.profiles
    where profiles.id = auth.uid()
      and profiles.active = true
      and profiles.role in ('admin', 'qa_manager')
  )
);

create policy coaching_manager_write
on public.coaching_sessions
for all
to authenticated
using (
  exists (
    select 1
    from public.profiles
    where profiles.id = auth.uid()
      and profiles.active = true
      and profiles.role in ('admin', 'qa_manager')
  )
)
with check (
  exists (
    select 1
    from public.profiles
    where profiles.id = auth.uid()
      and profiles.active = true
      and profiles.role in ('admin', 'qa_manager')
  )
);

commit;
