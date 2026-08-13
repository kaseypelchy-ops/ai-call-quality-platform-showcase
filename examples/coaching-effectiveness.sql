-- Pre/post coaching effectiveness analytics
--
-- Simplified public example based on the production QA platform.
--
-- The post-coaching window stops at the next coaching session so overlapping
-- interventions are not incorrectly attributed to the earlier session.

create or replace function public.coaching_effectiveness(
  p_window_days integer default 30
)
returns table (
  session_id uuid,
  agent_external_id text,
  held_at timestamptz,

  pre_call_count bigint,
  post_call_count bigint,

  pre_average_score numeric,
  post_average_score numeric,
  score_delta numeric,

  measurement_status text,
  category_deltas jsonb
)
language sql
stable
security invoker
set search_path = public
as $$
  with sessions as (
    select
      session.id as session_id,
      session.agent_external_id,
      session.held_at,

      case
        when p_window_days in (7, 14, 30)
          then p_window_days
        else 30
      end as window_days,

      lead(session.held_at) over (
        partition by session.agent_external_id
        order by session.held_at, session.created_at
      ) as next_coaching_at

    from public.coaching_sessions session

    where session.held_at is not null
      and session.status in (
        'held',
        'acknowledged',
        'follow_up_due',
        'closed'
      )
  ),

  aggregated as (
    select
      session.session_id,
      session.agent_external_id,
      session.held_at,

      count(call.id) filter (
        where call.processed_at >=
          session.held_at
            - make_interval(days => session.window_days)

          and call.processed_at < session.held_at

          and call.overall_score is not null
      )::bigint as pre_call_count,

      count(call.id) filter (
        where call.processed_at >= session.held_at

          and call.processed_at < least(
            now(),
            session.held_at
              + make_interval(days => session.window_days),

            coalesce(
              session.next_coaching_at,
              'infinity'::timestamptz
            )
          )

          and call.overall_score is not null
      )::bigint as post_call_count,

      avg(call.overall_score) filter (
        where call.processed_at < session.held_at
      ) as pre_average_score,

      avg(call.overall_score) filter (
        where call.processed_at >= session.held_at
      ) as post_average_score,

      avg(call.score_communication) filter (
        where call.processed_at < session.held_at
      ) as pre_communication,

      avg(call.score_communication) filter (
        where call.processed_at >= session.held_at
      ) as post_communication,

      avg(call.score_accuracy) filter (
        where call.processed_at < session.held_at
      ) as pre_accuracy,

      avg(call.score_accuracy) filter (
        where call.processed_at >= session.held_at
      ) as post_accuracy,

      avg(call.score_resolution) filter (
        where call.processed_at < session.held_at
      ) as pre_resolution,

      avg(call.score_resolution) filter (
        where call.processed_at >= session.held_at
      ) as post_resolution

    from sessions session

    left join public.qa_calls call
      on call.agent_external_id =
        session.agent_external_id

      and call.processed_at >=
        session.held_at
          - make_interval(days => session.window_days)

      and call.processed_at < least(
        now(),
        session.held_at
          + make_interval(days => session.window_days),

        coalesce(
          session.next_coaching_at,
          'infinity'::timestamptz
        )
      )

    group by
      session.session_id,
      session.agent_external_id,
      session.held_at,
      session.window_days,
      session.next_coaching_at
  )

  select
    aggregated.session_id,
    aggregated.agent_external_id,
    aggregated.held_at,

    aggregated.pre_call_count,
    aggregated.post_call_count,

    round(
      aggregated.pre_average_score,
      1
    ),

    round(
      aggregated.post_average_score,
      1
    ),

    round(
      aggregated.post_average_score
        - aggregated.pre_average_score,
      1
    ) as score_delta,

    case
      when aggregated.post_call_count = 0
        then 'awaiting_post_calls'

      when aggregated.pre_call_count < 3
        or aggregated.post_call_count < 3
        then 'insufficient_data'

      when aggregated.post_average_score
        - aggregated.pre_average_score >= 3
        then 'improved'

      when aggregated.post_average_score
        - aggregated.pre_average_score <= -3
        then 'declined'

      else 'stable'
    end as measurement_status,

    jsonb_build_object(
      'communication',
      jsonb_build_object(
        'pre', round(aggregated.pre_communication, 1),
        'post', round(aggregated.post_communication, 1),
        'delta', round(
          aggregated.post_communication
            - aggregated.pre_communication,
          1
        )
      ),

      'accuracy',
      jsonb_build_object(
        'pre', round(aggregated.pre_accuracy, 1),
        'post', round(aggregated.post_accuracy, 1),
        'delta', round(
          aggregated.post_accuracy
            - aggregated.pre_accuracy,
          1
        )
      ),

      'resolution',
      jsonb_build_object(
        'pre', round(aggregated.pre_resolution, 1),
        'post', round(aggregated.post_resolution, 1),
        'delta', round(
          aggregated.post_resolution
            - aggregated.pre_resolution,
          1
        )
      )
    ) as category_deltas

  from aggregated

  order by aggregated.held_at desc;
$$;

revoke execute
on function public.coaching_effectiveness(integer)
from public, anon;

grant execute
on function public.coaching_effectiveness(integer)
to authenticated;
