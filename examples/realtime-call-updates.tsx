"use client";

import {
  useEffect,
  useRef,
  useState,
} from "react";

import { useRouter } from "next/navigation";
import { createClient } from "@/lib/supabase/client";

type RealtimeStatus =
  | "connecting"
  | "live"
  | "reconnecting"
  | "offline";

type Environment = "production" | "staging";

const labels: Record<RealtimeStatus, string> = {
  connecting: "Connecting live updates…",
  live: "Live updates on",
  reconnecting: "Reconnecting live updates…",
  offline: "Live updates offline",
};

export function RealtimeCallUpdates({
  environment,
}: {
  environment: Environment;
}) {
  const router = useRouter();

  const timer = useRef<
    ReturnType<typeof setTimeout> | null
  >(null);

  const [status, setStatus] =
    useState<RealtimeStatus>("connecting");

  useEffect(() => {
    const supabase = createClient();

    let active = true;

    function scheduleRefresh() {
      if (!active) return;

      if (timer.current) {
        clearTimeout(timer.current);
      }

      /**
       * A single analyzed call can receive several closely spaced updates:
       * worker persistence, review state, and notification status.
       *
       * Collapse them into one server refresh instead of re-rendering the
       * dashboard for every individual database event.
       */
      timer.current = setTimeout(() => {
        timer.current = null;
        router.refresh();
      }, 750);
    }

    const filter =
      `platform_environment=eq.${environment}`;

    const channel = supabase
      .channel(`qa-call-updates-${environment}`)

      .on(
        "postgres_changes",
        {
          event: "INSERT",
          schema: "public",
          table: "qa_calls",
          filter,
        },
        scheduleRefresh,
      )

      .on(
        "postgres_changes",
        {
          event: "UPDATE",
          schema: "public",
          table: "qa_calls",
          filter,
        },
        scheduleRefresh,
      )

      .subscribe((channelStatus) => {
        if (!active) return;

        if (channelStatus === "SUBSCRIBED") {
          setStatus("live");
          return;
        }

        if (
          channelStatus === "CHANNEL_ERROR" ||
          channelStatus === "TIMED_OUT"
        ) {
          setStatus("reconnecting");
          return;
        }

        if (channelStatus === "CLOSED") {
          setStatus("offline");
        }
      });

    return () => {
      active = false;

      if (timer.current) {
        clearTimeout(timer.current);
      }

      timer.current = null;

      void supabase.removeChannel(channel);
    };
  }, [environment, router]);

  return (
    <div
      data-status={status}
      role="status"
      aria-live="polite"
    >
      <span aria-hidden="true">●</span>
      <span>{labels[status]}</span>
    </div>
  );
}
