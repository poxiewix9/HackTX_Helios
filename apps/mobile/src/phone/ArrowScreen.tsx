import * as Haptics from "expo-haptics";
import * as Speech from "expo-speech";
import { useEffect, useRef, useState } from "react";
import { Pressable, ScrollView, StyleSheet, Text, View } from "react-native";

import { HAZARD_CLASSES, SIMULATION_LABEL } from "@arco/schemas";
import type { HazardClass } from "@arco/schemas";

import type { Connectivity } from "@/core/transport";

import type { DeviceActions, DeviceView } from "./useDevice";

const PIN_CLASSES: HazardClass[] = [
  "fire_front",
  "smoke",
  "blocked_road",
  "debris",
  "flooded_road",
  "downed_line",
];

const CONN_COLOR: Record<Connectivity, string> = {
  cell: "#3ddc84",
  mesh: "#36c4ff",
  isolated: "#ff5c5c",
};

function fmtM(m: number): string {
  return m < 1000 ? `${Math.round(m)} m` : `${(m / 1000).toFixed(1)} km`;
}

export function ArrowScreen({ view, actions }: { view: DeviceView; actions: DeviceActions }) {
  const [picker, setPicker] = useState(false);
  const lastTurn = useRef<string | null>(null);

  // Haptic + spoken cue whenever the manoeuvre changes.
  const turn = view.guidance?.turn ?? null;
  const instruction = view.guidance?.instruction ?? null;
  useEffect(() => {
    if (!turn || turn === lastTurn.current) return;
    lastTurn.current = turn;
    void Haptics.impactAsync(
      turn === "arrive" ? Haptics.ImpactFeedbackStyle.Heavy : Haptics.ImpactFeedbackStyle.Medium,
    ).catch(() => undefined);
    if (instruction) Speech.speak(instruction, { rate: 1.05 });
  }, [turn, instruction]);

  const g = view.guidance;
  const conn = view.connectivity;

  return (
    <View style={styles.root}>
      {/* connectivity */}
      <View style={styles.topBar}>
        <View style={[styles.badge, { borderColor: CONN_COLOR[conn] }]}>
          <View style={[styles.dot, { backgroundColor: CONN_COLOR[conn] }]} />
          <Text style={[styles.badgeText, { color: CONN_COLOR[conn] }]}>{conn.toUpperCase()}</Text>
        </View>
        <Text style={styles.dim}>
          relay {view.relayState}
          {view.relayState === "reconnecting" ? ` #${view.relayAttempt}` : ""} · {view.peers.length}{" "}
          peer{view.peers.length === 1 ? "" : "s"}
        </Text>
      </View>

      {/* arrow */}
      <View style={styles.arrowWrap} accessibilityLabel="direction arrow" testID="arrow">
        <View style={{ transform: [{ rotate: `${g?.arrowDeg ?? 0}deg` }] }}>
          <Text style={[styles.arrow, !g && styles.arrowIdle]}>
            {g?.turn === "arrive" ? "◉" : "➤"}
          </Text>
        </View>
        <Text style={styles.distance}>{g ? fmtM(g.nextM) : "—"}</Text>
        <Text style={styles.instruction} numberOfLines={2}>
          {g?.instruction ??
            (view.route ? "Computing…" : "No route yet — isolated and no tile match")}
        </Text>
        {g && (
          <Text style={styles.dim}>
            {fmtM(g.remainingM)} to safety · heading {Math.round(view.headingDeg)}°
          </Text>
        )}
      </View>

      {/* route provenance — honesty label */}
      <View style={styles.sourceRow}>
        <Text
          style={[
            styles.source,
            view.route?.source === "navigator" ? styles.sourceNav : styles.sourceFallback,
          ]}
        >
          {view.route
            ? view.route.source === "navigator"
              ? `NAVIGATOR ROUTE · graph v${view.route.graphVersion ?? "?"} → ${view.route.safeZoneId}`
              : `ON-DEVICE FALLBACK (local A*) → ${view.route.safeZoneId}`
            : "NO ROUTE"}
        </Text>
        <Text style={styles.dim}>{SIMULATION_LABEL} · position SIMULATED</Text>
      </View>

      {/* controls */}
      <View style={styles.controls}>
        <Pressable
          style={[styles.btn, styles.btnDanger]}
          onPress={() => {
            setPicker((p) => !p);
          }}
          testID="drop-pin"
        >
          <Text style={styles.btnText}>⚑ Drop hazard pin</Text>
        </Pressable>
        <Pressable
          style={[styles.btn, view.cellular && styles.btnOn]}
          onPress={() => {
            actions.setCellular(!view.cellular);
          }}
          testID="toggle-cell"
        >
          <Text style={styles.btnText}>{view.cellular ? "Cellular ON" : "Cellular OFF"}</Text>
        </Pressable>
        <Pressable
          style={[styles.btn, view.walking && styles.btnOn]}
          onPress={() => {
            actions.setWalking(!view.walking);
          }}
          testID="toggle-walk"
        >
          <Text style={styles.btnText}>{view.walking ? "Walking" : "Walk"}</Text>
        </Pressable>
        <Pressable
          style={styles.btn}
          onPress={() => {
            actions.resetPosition();
          }}
        >
          <Text style={styles.btnText}>Reset</Text>
        </Pressable>
      </View>
      {picker && (
        <View style={styles.picker}>
          {PIN_CLASSES.map((cls) => (
            <Pressable
              key={cls}
              style={[styles.chip, { borderColor: HAZARD_CLASSES[cls].color }]}
              onPress={() => {
                actions.dropPin(cls);
                setPicker(false);
              }}
              testID={`pin-${cls}`}
            >
              <Text style={[styles.chipText, { color: HAZARD_CLASSES[cls].color }]}>
                {HAZARD_CLASSES[cls].label}
              </Text>
            </Pressable>
          ))}
        </View>
      )}

      {/* pins */}
      <ScrollView style={styles.pins}>
        <Text style={styles.sectionTitle}>
          COMMUNITY PINS · {view.pins.length} · {view.pendingUpload} awaiting uplink ·{" "}
          {view.uploaded} uploaded by this phone
        </Text>
        {view.pins.length === 0 && (
          <Text style={styles.dim}>none yet — pins from any phone in the mesh appear here</Text>
        )}
        {view.pins
          .slice()
          .reverse()
          .map((p) => (
            <View key={p.id} style={styles.pinRow}>
              <View
                style={[
                  styles.dot,
                  {
                    backgroundColor: p.hazard_class ? HAZARD_CLASSES[p.hazard_class].color : "#888",
                  },
                ]}
              />
              <Text style={styles.pinText} numberOfLines={1}>
                {p.report_type === "hazard_pin" && p.hazard_class
                  ? HAZARD_CLASSES[p.hazard_class].label
                  : p.report_type}
                {" · "}
                {p.reporter_hash === view.reporterHash ? "me" : p.reporter_hash.slice(0, 8)}
                {" · "}
                {p.created_at.slice(11, 19)}
              </Text>
            </View>
          ))}
        <Text style={styles.sectionTitle}>RADIOS</Text>
        {view.radios.map((r) => (
          <Text key={r.kind} style={styles.dim}>
            {r.kind}: {r.available ? "available" : `unavailable — ${r.reason ?? ""}`}
          </Text>
        ))}
        <Text style={styles.dim}>
          device {view.id} · key {view.reporterHash.slice(0, 12)}…
        </Text>
        {view.tileLabel && <Text style={styles.dim}>tile: {view.tileLabel}</Text>}
        {view.lastError && <Text style={styles.error}>{view.lastError}</Text>}
      </ScrollView>
    </View>
  );
}

const styles = StyleSheet.create({
  root: { flex: 1, backgroundColor: "#0b0f14", paddingTop: 56, paddingHorizontal: 16 },
  topBar: { flexDirection: "row", alignItems: "center", justifyContent: "space-between" },
  badge: {
    flexDirection: "row",
    alignItems: "center",
    gap: 6,
    borderWidth: 1,
    borderRadius: 4,
    paddingHorizontal: 8,
    paddingVertical: 3,
  },
  badgeText: { fontFamily: "monospace", fontSize: 12, fontWeight: "700", letterSpacing: 1 },
  dot: { width: 8, height: 8, borderRadius: 4 },
  dim: { color: "#7a8699", fontSize: 11, fontFamily: "monospace" },
  arrowWrap: { alignItems: "center", justifyContent: "center", paddingVertical: 20 },
  arrow: {
    fontSize: 170,
    lineHeight: 190,
    color: "#36c4ff",
    textShadowColor: "#36c4ff",
    textShadowRadius: 24,
  },
  arrowIdle: { color: "#2a3340", textShadowRadius: 0 },
  distance: { color: "#e8eef7", fontSize: 44, fontWeight: "800", fontVariant: ["tabular-nums"] },
  instruction: { color: "#e8eef7", fontSize: 18, textAlign: "center", marginTop: 4, minHeight: 48 },
  sourceRow: { alignItems: "center", gap: 2, marginBottom: 10 },
  source: { fontFamily: "monospace", fontSize: 11, fontWeight: "700", letterSpacing: 0.5 },
  sourceNav: { color: "#3ddc84" },
  sourceFallback: { color: "#ffb347" },
  controls: { flexDirection: "row", flexWrap: "wrap", gap: 8, justifyContent: "center" },
  btn: {
    borderWidth: 1,
    borderColor: "#2a3340",
    borderRadius: 6,
    paddingHorizontal: 14,
    paddingVertical: 10,
    backgroundColor: "#121922",
  },
  btnOn: { borderColor: "#3ddc84", backgroundColor: "#12261b" },
  btnDanger: { borderColor: "#ff5c5c", backgroundColor: "#2a1416" },
  btnText: { color: "#e8eef7", fontWeight: "600" },
  picker: {
    flexDirection: "row",
    flexWrap: "wrap",
    gap: 6,
    justifyContent: "center",
    marginTop: 8,
  },
  chip: { borderWidth: 1, borderRadius: 14, paddingHorizontal: 10, paddingVertical: 5 },
  chipText: { fontSize: 12, fontWeight: "600" },
  pins: { marginTop: 12, flex: 1 },
  sectionTitle: {
    color: "#7a8699",
    fontSize: 10,
    fontFamily: "monospace",
    letterSpacing: 1,
    marginTop: 10,
    marginBottom: 4,
  },
  pinRow: { flexDirection: "row", alignItems: "center", gap: 8, paddingVertical: 3 },
  pinText: { color: "#c6d0dd", fontSize: 12, fontFamily: "monospace", flex: 1 },
  error: { color: "#ff5c5c", fontSize: 11, marginTop: 6 },
});
