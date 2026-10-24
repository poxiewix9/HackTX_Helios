"use client";

import { useEffect, useRef, useState } from "react";

import type { AgentEvent } from "@arco/schemas";

import { Badge } from "@/components/ui/badge";
import { AGENT_COLORS } from "@/lib/colors";
import { fmtClock } from "@/lib/utils";
import { useWorld } from "@/store/world";

type Filter = "all" | AgentEvent["agent"];

export function AgentPanel() {
  const events = useWorld((s) => s.agentEvents);
  const [filter, setFilter] = useState<Filter>("all");
  const [follow, setFollow] = useState(true);
  const listRef = useRef<HTMLOListElement | null>(null);
  const visible = (filter === "all" ? events : events.filter((e) => e.agent === filter)).slice(
    -150,
  );

  useEffect(() => {
    if (follow && listRef.current) listRef.current.scrollTop = listRef.current.scrollHeight;
  }, [visible.length, follow]);

  return (
    <section className="panel flex h-full min-h-0 flex-col" data-testid="agent-panel">
      <div className="panel-title">
        <span>agent decisions · {events.length}</span>
        <span className="flex gap-1">
          {(["all", "cartographer", "navigator", "consensus"] as Filter[]).map((f) => (
            <button
              key={f}
              type="button"
              className={`rounded-sm px-1 ${filter === f ? "bg-ops-edge text-ops-text" : "text-ops-dim hover:text-ops-text"}`}
              style={f !== "all" && filter === f ? { color: AGENT_COLORS[f] } : undefined}
              onClick={() => {
                setFilter(f);
              }}
            >
              {f}
            </button>
          ))}
          <button
            type="button"
            className={`rounded-sm px-1 ${follow ? "text-ops-accent" : "text-ops-dim"}`}
            onClick={() => {
              setFollow((v) => !v);
            }}
            title="auto-scroll"
          >
            ⤓
          </button>
        </span>
      </div>
      <ol
        ref={listRef}
        className="min-h-0 flex-1 overflow-y-auto px-2 py-1 font-mono text-[11px]"
        onScroll={(e) => {
          const el = e.currentTarget;
          setFollow(el.scrollHeight - el.scrollTop - el.clientHeight < 24);
        }}
      >
        {visible.length === 0 && (
          <li className="text-ops-dim">no agent events yet — play a scenario</li>
        )}
        {visible.map((ev) => (
          <li
            key={ev.id}
            className="mb-1.5 border-l-2 pl-2"
            style={{ borderColor: AGENT_COLORS[ev.agent] }}
            data-testid="agent-event"
          >
            <div className="flex items-center gap-1.5 text-[10px]">
              <span style={{ color: AGENT_COLORS[ev.agent] }}>{ev.agent}</span>
              <Badge tone={kindTone(ev.kind)}>{ev.kind}</Badge>
              <span className="text-ops-dim">t {fmtClock(ev.scenario_t)}</span>
              {ev.edge_ids && ev.edge_ids.length > 0 && (
                <span className="text-ops-dim">{ev.edge_ids.length} edges</span>
              )}
              {ev.route_ids && ev.route_ids.length > 0 && (
                <span className="text-ops-dim">{ev.route_ids.length} routes</span>
              )}
            </div>
            <div className="whitespace-pre-wrap text-ops-text/90">{ev.reasoning}</div>
          </li>
        ))}
      </ol>
    </section>
  );
}

function kindTone(kind: string): "ok" | "warn" | "danger" | "accent" | "neutral" {
  if (kind.includes("severed") || kind.includes("disputed") || kind.includes("rejected"))
    return "danger";
  if (kind.includes("penalized") || kind.includes("pending") || kind.includes("overload"))
    return "warn";
  if (kind.startsWith("routes") || kind.includes("created")) return "accent";
  if (kind.includes("resolved") || kind.includes("restored") || kind.includes("corroborated"))
    return "ok";
  return "neutral";
}
