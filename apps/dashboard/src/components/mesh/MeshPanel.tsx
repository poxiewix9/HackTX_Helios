"use client";

/**
 * Community mesh panel (SIMULATED): device graph rendered with React Three Fiber + drei,
 * offline toggle, partition toggle, packet-loss control and "drop pin on device". Pins
 * dropped here are signed by the virtual phone, spread over the simulated mesh, and
 * reach the real Consensus agent through the gateway when the backhaul is up.
 */
import { Html, Line, OrbitControls } from "@react-three/drei";
import { Canvas, useFrame } from "@react-three/fiber";
import { useEffect, useMemo, useRef, useState } from "react";
import type * as THREE from "three";

import { HAZARD_CLASSES } from "@arco/schemas";
import type { HazardClass } from "@arco/schemas";

import { Button } from "@/components/ui/button";
import { config } from "@/lib/config";
import { bboxCenter } from "@/lib/geo";
import { useWorld } from "@/store/world";

import { MeshDemo, defaultDevices } from "./meshDemo";
import type { DeviceSnapshot, MeshSnapshot } from "./meshDemo";

const CONN_COLOR = { cell: "#3ddc84", mesh: "#36c4ff", isolated: "#ff5c5c" } as const;
const PIN_CLASSES: HazardClass[] = ["fire_front", "smoke", "blocked_road", "debris", "downed_line"];
const SCALE = 1 / 40; // metres → scene units

function DeviceMesh({
  d,
  selected,
  onSelect,
  flash,
}: {
  d: DeviceSnapshot;
  selected: boolean;
  onSelect: () => void;
  flash: number;
}) {
  const ring = useRef<THREE.Mesh>(null);
  const body = useRef<THREE.Mesh>(null);
  useFrame(({ clock }) => {
    const t = clock.getElapsedTime();
    if (body.current) {
      const s = d.isGateway ? 1.25 : 1;
      body.current.scale.setScalar(s + (selected ? 0.15 * Math.sin(t * 6) : 0));
    }
    if (ring.current) {
      // Pin-propagation flash: expands and fades for ~1.2 s after `flash` (ms timestamp).
      const age = (Date.now() - flash) / 1200;
      const visible = flash > 0 && age < 1;
      ring.current.visible = visible;
      if (visible) {
        ring.current.scale.setScalar(1 + age * 2.5);
        const mat = ring.current.material as THREE.MeshBasicMaterial;
        mat.opacity = 1 - age;
      }
    }
  });
  const color = CONN_COLOR[d.connectivity];
  return (
    <group position={[d.x * SCALE, 0, -d.y * SCALE]}>
      <mesh
        ref={body}
        onClick={(e) => {
          e.stopPropagation();
          onSelect();
        }}
      >
        <sphereGeometry args={[0.35, 24, 24]} />
        <meshStandardMaterial
          color={color}
          emissive={color}
          emissiveIntensity={selected ? 0.9 : 0.35}
        />
      </mesh>
      {d.isGateway && (
        <mesh rotation={[-Math.PI / 2, 0, 0]}>
          <ringGeometry args={[0.55, 0.62, 48]} />
          <meshBasicMaterial color="#3ddc84" transparent opacity={0.8} />
        </mesh>
      )}
      <mesh ref={ring} rotation={[-Math.PI / 2, 0, 0]} visible={false}>
        <ringGeometry args={[0.4, 0.46, 48]} />
        <meshBasicMaterial color="#ffb347" transparent opacity={0.9} />
      </mesh>
      <Html position={[0, 0.7, 0]} center zIndexRange={[10, 0]} style={{ pointerEvents: "none" }}>
        <div
          className="whitespace-nowrap rounded border border-ops-edge bg-ops-panel/90 px-1 py-px font-mono text-[9px] leading-tight text-ops-text"
          data-testid={`mesh-device-${d.id}`}
          data-pins={d.pins}
          data-conn={d.connectivity}
        >
          <span style={{ color }}>●</span> {d.id}
          {d.isGateway ? " ⇡" : ""} · {d.pins} pin{d.pins === 1 ? "" : "s"}
          {d.pendingUpload > 0 && d.isGateway ? ` · ${d.pendingUpload} queued` : ""}
        </div>
      </Html>
    </group>
  );
}

function Links({ snap }: { snap: MeshSnapshot }) {
  const byId = new Map(snap.devices.map((d) => [d.id, d]));
  return (
    <>
      {snap.links.map((l) => {
        const a = byId.get(l.a);
        const b = byId.get(l.b);
        if (!a || !b) return null;
        return (
          <Line
            key={`${l.a}-${l.b}`}
            points={[
              [a.x * SCALE, 0, -a.y * SCALE],
              [b.x * SCALE, 0, -b.y * SCALE],
            ]}
            color={l.up ? "#36c4ff" : "#ff5c5c"}
            lineWidth={l.up ? 1.5 : 1}
            dashed={!l.up}
            dashSize={0.2}
            gapSize={0.15}
            transparent
            opacity={l.up ? 0.8 : 0.6}
          />
        );
      })}
    </>
  );
}

export function MeshPanel() {
  const scenario = useWorld((s) => s.scenario);
  const routes = useWorld((s) => s.routes);
  const clockT = useWorld((s) => s.clock.t);
  const clockRef = useRef(clockT);
  clockRef.current = clockT;

  const demo = useMemo(() => {
    const center = scenario ? bboxCenter(scenario.bbox) : null;
    if (!center) return null;
    const relayUrl = config.meshRelayWs;
    return new MeshDemo({
      devices: defaultDevices(center),
      gatewayId: "gateway",
      blackboardUrl: config.rest.bb,
      relay: relayUrl ? { url: relayUrl, room: `arco:${scenario?.id ?? "demo"}` } : null,
      scenarioT: () => clockRef.current,
    });
  }, [scenario]);

  const [snap, setSnap] = useState<MeshSnapshot | null>(null);
  const [selected, setSelected] = useState<string>("phone-5");
  const [picker, setPicker] = useState(false);
  const flashes = useRef<Map<string, number>>(new Map());
  const lastPins = useRef<Map<string, number>>(new Map());

  useEffect(() => {
    if (!demo) return;
    demo.start();
    const update = () => {
      const s = demo.snapshot();
      for (const d of s.devices) {
        const prev = lastPins.current.get(d.id) ?? 0;
        if (d.pins > prev) flashes.current.set(d.id, Date.now());
        lastPins.current.set(d.id, d.pins);
      }
      setSnap(s);
    };
    const off = demo.onChange(update);
    update();
    const tick = setInterval(() => {
      demo.tick();
    }, 50);
    const up = setInterval(() => {
      void demo.flushUplink();
    }, 1500);
    return () => {
      off();
      clearInterval(tick);
      clearInterval(up);
      demo.stop();
    };
  }, [demo]);

  // Routes from the agents enter the mesh through the gateway.
  useEffect(() => {
    demo?.ingestRoutes(routes);
  }, [demo, routes]);

  if (!demo || !snap) {
    return (
      <div
        className="panel flex h-full items-center justify-center text-xs text-ops-dim"
        data-testid="mesh-panel"
      >
        mesh panel waits for a scenario
      </div>
    );
  }

  return (
    <div className="panel flex h-full min-h-0 flex-col" data-testid="mesh-panel">
      <div className="panel-title">
        <span className="flex items-center gap-2 text-ops-text">
          Mesh
          <span className="rounded-md border border-ops-warn/40 px-1.5 py-px font-mono text-[9px] font-normal uppercase tracking-wider text-ops-warn">
            Simulated
          </span>
        </span>
        <span
          className="truncate font-mono text-[10px] font-normal text-ops-dim"
          data-testid="mesh-stats"
          data-pins={snap.totalPins}
          data-uploaded={snap.uploaded}
          title={
            snap.relayState
              ? `relay ${snap.relayState}${snap.relayPeers.length ? ` (${snap.relayPeers.length} phones)` : ""}`
              : undefined
          }
        >
          {snap.totalPins} pins · {snap.stats.delivered} delivered · {snap.uploaded} uploaded
        </span>
      </div>
      <div className="relative min-h-0 flex-1" data-testid="mesh-canvas">
        <Canvas
          camera={{ position: [3.8, 7, 8.5], fov: 34 }}
          gl={{ antialias: true, alpha: true }}
          dpr={[1, 1.5]}
        >
          <ambientLight intensity={0.6} />
          <pointLight position={[5, 8, 5]} intensity={40} />
          <gridHelper args={[16, 16, "#1c2633", "#141b24"]} position={[4.5, -0.4, 0]} />
          <Links snap={snap} />
          {snap.devices.map((d) => (
            <DeviceMesh
              key={d.id}
              d={d}
              selected={d.id === selected}
              flash={flashes.current.get(d.id) ?? 0}
              onSelect={() => {
                setSelected(d.id);
              }}
            />
          ))}
          <OrbitControls enablePan={false} minDistance={6} maxDistance={20} target={[3.8, 0, 0]} />
        </Canvas>
      </div>
      <div className="flex flex-wrap items-center gap-1.5 border-t border-ops-edge p-1.5">
        <Button
          size="sm"
          variant={snap.cellular ? "accent" : "danger"}
          data-testid="mesh-cellular"
          data-state={snap.cellular ? "on" : "off"}
          onClick={() => {
            demo.setCellular(!snap.cellular);
          }}
        >
          {snap.cellular ? "Cellular on" : "Cellular off"}
        </Button>
        <Button
          size="sm"
          variant={snap.partitioned ? "danger" : "default"}
          data-testid="mesh-partition"
          onClick={() => {
            demo.setPartition(!snap.partitioned);
          }}
        >
          {snap.partitioned ? "Heal split" : "Split"}
        </Button>
        <label className="flex items-center gap-1 font-mono text-[10px] text-ops-dim">
          loss
          <input
            type="range"
            min={0}
            max={0.6}
            step={0.05}
            value={snap.loss}
            className="h-1 w-16 accent-[#36c4ff]"
            data-testid="mesh-loss"
            onChange={(e) => {
              demo.setLoss(Number(e.target.value));
            }}
          />
          {Math.round(snap.loss * 100)}%
        </label>
        <div className="relative ml-auto">
          <Button
            size="sm"
            variant="danger"
            data-testid="mesh-drop-pin"
            onClick={() => {
              setPicker((p) => !p);
            }}
          >
            Pin {selected}
          </Button>
          {picker && (
            <div
              className="absolute bottom-full right-0 z-10 mb-1 flex flex-col gap-1 rounded border border-ops-edge bg-ops-panel p-1.5"
              data-testid="mesh-pin-menu"
            >
              {PIN_CLASSES.map((cls) => (
                <button
                  key={cls}
                  type="button"
                  className="rounded px-2 py-1 text-left font-mono text-[11px] hover:bg-ops-edge"
                  style={{ color: HAZARD_CLASSES[cls].color }}
                  data-testid={`mesh-pin-${cls}`}
                  onClick={() => {
                    demo.dropPin(selected, cls, `pinned from ${selected} (simulated mesh)`);
                    setPicker(false);
                  }}
                >
                  {HAZARD_CLASSES[cls].label}
                </button>
              ))}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
