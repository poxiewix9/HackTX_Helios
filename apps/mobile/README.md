# ARCO Community app (`apps/mobile`)

Expo / React Native app for people on the ground: a single big arrow that follows the
Navigator's route for the crowd group you are in, a one-tap hazard pin, and a
phone-to-phone mesh that keeps pins and routes flowing when cellular is gone.

Everything in `src/core` is plain TypeScript with **no React Native imports**, so the
same code runs in the phone, in Node tests, and inside the ops dashboard's
"Community mesh" panel (`apps/dashboard/src/components/mesh`).

## What is real and what is simulated

| Piece                                   | Status                                                                                              |
| --------------------------------------- | --------------------------------------------------------------------------------------------------- |
| Ed25519 signing of `CommunityReport`    | Real. Byte-for-byte compatible with the Python signer (`tests/fixtures/signed_reports.json`).       |
| CRDT state (`MeshDoc`, yjs)             | Real. Converges under packet loss and partitions (see tests).                                       |
| `WebSocketRelayTransport`               | Real. Needs a reachable relay (`make mesh-relay`) — a LAN/Wi-Fi hop, not cellular.                  |
| `SimulatedMeshTransport`                | **Simulated.** Range/loss/latency model in memory; used by tests and the dashboard panel.           |
| `BleTransport`, `WifiDirectTransport`   | Stubs with an honest `availability()` reason. **Not functional** in Expo Go (see below).            |
| On-device fallback router               | Real A\* over the bundled OSM tile; only used when no Navigator route is available.                 |
| Phone position                          | **Simulated** (walks along the active route). Labelled `position SIMULATED` on screen.              |
| Device key                              | Generated in memory per app launch. Not persisted to the keychain yet.                              |

## Run it

```bash
# 1. backend (from the repo root)
make up                     # or: make serve-blackboard + make serve-agents
make mesh-relay             # WebSocket relay on :8787 (MESH_RELAY_PORT)

# 2. the app — set the LAN IP of this machine so a real phone can reach it
EXPO_PUBLIC_MESH_RELAY_URL=ws://192.168.1.10:8787 \
EXPO_PUBLIC_BLACKBOARD_URL=http://192.168.1.10:8001 \
make dev-mobile             # scan the QR code with Expo Go
```

`EXPO_PUBLIC_*` values are inlined by Metro at bundle time (`src/phone/config.ts`);
restart the dev server after changing them. Defaults point at `localhost`, which only
works for the iOS simulator / Android emulator (`10.0.2.2` for Android emulator).

Two phones joined to the same relay room (`arco:<scenario>`) exchange pins and routes
with **cellular off** on both — the relay hop is Wi-Fi, and the dashboard's gateway is
in the same room, so a pin dropped on a phone shows up on the ops map after the
gateway's uplink flushes it to the blackboard.

### Screen

- Connectivity badge: `cell` (cellular toggle on) · `mesh` (no cellular but ≥1 peer) ·
  `isolated`.
- Arrow + distance + spoken/haptic turn cue (`expo-speech`, `expo-haptics`).
- Route provenance: `NAVIGATOR` when the route came through the mesh/uplink from the
  agents service; `ON-DEVICE FALLBACK` when the phone computed it from the bundled tile.
  The fallback is never presented as the Navigator's route.
- `Drop hazard pin` → picks a `HazardClass`; the pin is a signed `CommunityReport`
  (`report_type: "hazard_pin"`) from `@arco/schemas`.
- `Cellular` toggle: gates the uplink (`POST /reports`) and the `GET /routes` poll only;
  the mesh keeps running either way.

## Tests

```bash
make test-mobile            # = pnpm --filter @arco/mobile test   (vitest, Node)
```

| File                     | Proves                                                                                       |
| ------------------------ | -------------------------------------------------------------------------------------------- |
| `tests/signing.test.ts`  | Signatures match the Python `arco_agents.reports` signer on fixture reports; tamper detection |
| `tests/mesh.test.ts`     | Merge, forged-pin quarantine, route LWW, 3-hop delivery with cellular off, convergence at 35 % loss, **partition-and-heal**, late joiner |
| `tests/relay.test.ts`    | Real relay process on an ephemeral port: addressed + broadcast frames, peer lists, reconnect |
| `tests/router.test.ts`   | A\* on the real Lahaina tile; a hazard pin on the route forces a detour                      |
| `tests/uplink.test.ts`   | Pending pins upload once, 409 treated as success, failures retried                           |
| `tests/walker.test.ts`   | Simulated walker stays on the polyline                                                       |

Metro compile check without a device: `make bundle-mobile` (`expo export --platform ios`).

## Mesh protocol (`src/core/sync.ts`)

Frames are `tag(1 byte) + payload`: `0x01` = yjs state vector, `0x02` = yjs update.
On a new peer, and every `gossipEveryMs`, a node sends its state vector to `fanout`
random peers; a peer answers with `diffFor(sv)`. Updates are flood-forwarded **only when
they changed local state**, so loops die out. Any update containing a pin whose
signature does not verify is quarantined (`MeshDoc.rejected`) and never forwarded.

## BLE / Wi-Fi Direct — the honest version

The `Transport` interface is the only seam, and the two radio transports are wired to
`RadioAdapter`s that do not exist in this build. What it would take, and why they are
not shipped:

- **Expo Go has no BLE.** Any radio transport needs a development build
  (`npx expo run:ios` / `eas build`), which also means an Apple developer account.
- **iOS BLE mesh needs peripheral mode.** `react-native-ble-plx` is central-only: a
  phone can scan and connect, but cannot *be* discovered. Peer-to-peer therefore needs a
  peripheral-capable module (e.g. `react-native-ble-peripheral`, Multipeer Connectivity
  bridge). In the background iOS strips custom service UUIDs from advertisements
  (overflow area only) and throttles scans, so an unattended phone in a pocket is at
  best a slow, opportunistic hop.
- **Wi-Fi Direct is Android-only.** iOS has no public Wi-Fi Direct API; the closest is
  Multipeer Connectivity (Bluetooth + peer-to-peer Wi-Fi), which is also not exposed by
  Expo.
- Realistic BLE payloads are ~20–250 bytes per write; yjs updates for a pin are ~300 B,
  so a real adapter needs chunking (not implemented here).

`availability()` on each stub returns `{ available: false, reason }` and the UI shows
the reason under "radios", so nobody mistakes the stub for a working radio.

## Offline routing tile

`assets/tiles/lahaina_2023.json` (`format: "arco.tile/1"`) is exported from the
scenario's OSM road graph by `scripts/export_graph_tile.py` (`make export-tile
SCENARIO=lahaina_2023`). Source: OpenStreetMap contributors via OSMnx, **ODbL 1.0**;
current-day road geometry, which may differ from the August 2023 network. The tile also
carries the scenario's safe zones and population-cell centroids so the fallback router
has somewhere to route to and the simulated walker has somewhere to start.

## Known limitations

- Position is simulated (no `expo-location` yet); the UI says so.
- Device key lives in memory for the app session; `reporter_hash` therefore changes on
  restart.
- Radio transports are stubs (above). Mesh is real over the relay and in simulation.
- Expo SDK 58 wants Node ≥ 22.13; Node 20 prints a warning but bundles fine.
- `yjs` logs "Yjs was already imported" in the dashboard's Next dev server because the
  SSR bundle loads both the ESM and CJS builds; it is harmless in the browser bundle.
