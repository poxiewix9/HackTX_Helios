"use client";

import { useCallback, useEffect, useState } from "react";

import { MACRO_CLASSES, MICRO_CLASSES, HAZARD_CLASSES } from "@arco/schemas";
import type { HazardClass } from "@arco/schemas";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { api } from "@/lib/api";
import type { ScenarioSummary } from "@/lib/api";
import { squareAround } from "@/lib/geo";
import type { LngLat } from "@/lib/geo";
import { fmtClock } from "@/lib/utils";
import { displayClockT, useWorld } from "@/store/world";

const SPEEDS = [1, 5, 10, 30, 60, 120, 200];
const CHAOS_CLASSES: HazardClass[] = [...MACRO_CLASSES, ...MICRO_CLASSES];

export interface ControlBarProps {
  satellite: boolean;
  setSatellite: (v: boolean) => void;
  three: boolean;
  setThree: (v: boolean) => void;
  labels: boolean;
  setLabels: (v: boolean) => void;
  presentation: boolean;
  setPresentation: (v: boolean) => void;
  /** Chaos hazard placement: parent routes the next map click here. */
  placing: HazardClass | null;
  setPlacing: (cls: HazardClass | null) => void;
}

export function useChaosPlacement(
  placing: HazardClass | null,
  setPlacing: (c: HazardClass | null) => void,
) {
  return useCallback(
    (lngLat: LngLat) => {
      if (!placing) return;
      const cls = placing;
      setPlacing(null);
      void api.sim
        .chaosHazard({
          geom: squareAround(lngLat, 60),
          hazard_class: cls,
          confidence: 0.9,
          note: "chaos button (dashboard)",
        })
        .catch((e: unknown) => {
          useWorld.getState().setError(`chaos hazard failed: ${String(e)}`);
        });
    },
    [placing, setPlacing],
  );
}

export function ControlBar(props: ControlBarProps) {
  const clock = useWorld((s) => s.clock);
  const mode = useWorld((s) => s.mode);
  const cellularUp = useWorld((s) => s.cellularUp);
  const scenario = useWorld((s) => s.scenario);
  const [scenarios, setScenarios] = useState<ScenarioSummary[]>([]);
  const [busy, setBusy] = useState<string | null>(null);
  const [, setTick] = useState(0); // re-render 4×/s so the extrapolated clock advances
  const [chaosOpen, setChaosOpen] = useState(false);
  const live = mode === "live";

  useEffect(() => {
    const id = setInterval(() => {
      setTick((x) => x + 1);
    }, 250);
    return () => {
      clearInterval(id);
    };
  }, []);

  useEffect(() => {
    if (!live) return;
    api.sim
      .scenarios()
      .then(setScenarios)
      .catch(() => {
        setScenarios([]);
      });
  }, [live]);

  const run = (label: string, fn: () => Promise<unknown>) => {
    setBusy(label);
    fn()
      .catch((e: unknown) => {
        useWorld.getState().setError(`${label} failed: ${String(e)}`);
      })
      .finally(() => {
        setBusy(null);
      });
  };

  const t = displayClockT(clock);
  const duration = clock.durationS ?? scenario?.duration_s ?? 0;

  return (
    <div className="flex h-11 items-center gap-2 border-t border-ops-edge bg-ops-panel px-3 font-mono text-[11px]">
      <select
        className="h-8 rounded border border-ops-edge bg-ops-bg px-2 text-ops-text"
        value={clock.scenarioId ?? scenario?.id ?? ""}
        disabled={!live || busy !== null}
        data-testid="scenario-select"
        onChange={(e) => {
          run("load", () => api.sim.load(e.target.value));
        }}
      >
        {scenarios.length === 0 && (
          <option value={scenario?.id ?? ""}>{scenario?.name ?? "no scenario"}</option>
        )}
        {scenarios.map((s) => (
          <option key={s.id} value={s.id}>
            {s.name} ({s.event}, {s.event_date})
          </option>
        ))}
      </select>
      <Button
        variant="accent"
        size="sm"
        disabled={!live || busy !== null}
        data-testid={clock.playing ? "pause-button" : "play-button"}
        onClick={() => {
          run(clock.playing ? "pause" : "play", () =>
            clock.playing ? api.sim.pause() : api.sim.play(),
          );
        }}
      >
        {clock.playing ? "❚❚ pause" : "▶ play"}
      </Button>
      <span className="w-14 text-right text-sm text-ops-text" data-testid="clock-t">
        {fmtClock(t)}
      </span>
      <input
        type="range"
        min={0}
        max={Math.max(1, duration)}
        step={1}
        value={Math.min(t, duration || t)}
        disabled={!live || busy !== null}
        className="h-1 w-56 cursor-pointer accent-[#36c4ff]"
        data-testid="scrub"
        onChange={(e) => {
          run("seek", () => api.sim.seek(Number(e.target.value)));
        }}
      />
      <span className="w-12 text-ops-dim">{fmtClock(duration)}</span>
      <select
        className="h-8 rounded border border-ops-edge bg-ops-bg px-2 text-ops-text"
        value={String(clock.speed)}
        disabled={!live || busy !== null}
        data-testid="speed-select"
        onChange={(e) => {
          run("speed", () => api.sim.speed(Number(e.target.value)));
        }}
      >
        {!SPEEDS.includes(clock.speed) && (
          <option value={String(clock.speed)}>{clock.speed}×</option>
        )}
        {SPEEDS.map((s) => (
          <option key={s} value={String(s)}>
            {s}×
          </option>
        ))}
      </select>
      <span className="mx-1 h-5 w-px bg-ops-edge" />
      <div className="relative">
        <Button
          variant="danger"
          size="sm"
          disabled={!live}
          data-testid="chaos-button"
          onClick={() => {
            setChaosOpen((o) => !o);
          }}
        >
          ⚡ chaos
        </Button>
        {chaosOpen && (
          <div className="panel absolute bottom-10 left-0 z-30 w-72 p-2" data-testid="chaos-menu">
            <div className="panel-title mb-1">inject hazard (click the map to place)</div>
            <div className="grid grid-cols-2 gap-1">
              {CHAOS_CLASSES.map((cls) => (
                <button
                  key={cls}
                  type="button"
                  className="flex items-center gap-1 rounded border border-ops-edge px-1.5 py-1 text-left text-[10px] hover:border-ops-accent"
                  style={
                    props.placing === cls ? { borderColor: HAZARD_CLASSES[cls].color } : undefined
                  }
                  data-testid={`chaos-class-${cls}`}
                  onClick={() => {
                    props.setPlacing(cls);
                    setChaosOpen(false);
                  }}
                >
                  <span
                    className="inline-block h-2 w-2 rounded-sm"
                    style={{ background: HAZARD_CLASSES[cls].color }}
                  />
                  {HAZARD_CLASSES[cls].label}
                </button>
              ))}
            </div>
            <div className="panel-title my-1">network</div>
            <Button
              variant={cellularUp ? "danger" : "accent"}
              size="sm"
              className="w-full"
              data-testid="chaos-cellular"
              onClick={() => {
                setChaosOpen(false);
                run("cellular", () =>
                  api.sim.chaosCellular(!cellularUp, "chaos button (dashboard)"),
                );
              }}
            >
              {cellularUp ? "kill cellular" : "restore cellular"}
            </Button>
          </div>
        )}
      </div>
      {props.placing && (
        <Badge tone="danger" className="animate-pulse">
          click map to drop {HAZARD_CLASSES[props.placing].label}
          <button
            type="button"
            className="ml-1 underline"
            onClick={() => {
              props.setPlacing(null);
            }}
          >
            cancel
          </button>
        </Badge>
      )}
      <span className="ml-auto flex items-center gap-1">
        <Toggle
          label="satellite"
          on={props.satellite}
          onChange={props.setSatellite}
          testId="toggle-satellite"
        />
        <Toggle label="3D" on={props.three} onChange={props.setThree} testId="toggle-three" />
        <Toggle
          label="labels"
          on={props.labels}
          onChange={props.setLabels}
          testId="toggle-labels"
        />
        <Toggle
          label="present"
          on={props.presentation}
          onChange={props.setPresentation}
          testId="toggle-presentation"
        />
      </span>
    </div>
  );
}

function Toggle({
  label,
  on,
  onChange,
  testId,
}: {
  label: string;
  on: boolean;
  onChange: (v: boolean) => void;
  testId: string;
}) {
  return (
    <Button
      variant={on ? "accent" : "ghost"}
      size="sm"
      data-testid={testId}
      aria-pressed={on}
      onClick={() => {
        onChange(!on);
      }}
    >
      {label}
    </Button>
  );
}
