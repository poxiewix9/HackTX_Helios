#!/usr/bin/env node
/**
 * ARCO dev mesh relay. Rooms of WebSocket clients; binary frames are forwarded to one
 * member or to all others; the member list is pushed on every join/leave.
 *
 *   node scripts/relay-server.mjs            # ws://0.0.0.0:8787
 *   MESH_RELAY_PORT=9000 node scripts/relay-server.mjs
 *
 * Also importable: `startRelay({ port: 0 })` returns `{ port, close() }` for tests.
 * The relay never looks inside payloads (they are Yjs updates); it carries no state
 * beyond who is in which room.
 */
import { WebSocketServer } from "ws";

const enc = new TextEncoder();
const dec = new TextDecoder();

export async function startRelay({ port = 8787, host = "0.0.0.0", log = null } = {}) {
  const emit = log ?? (() => undefined);
  const wss = new WebSocketServer({ port, host });
  await new Promise((resolve, reject) => {
    wss.once("listening", resolve);
    wss.once("error", reject);
  });
  /** @type {Map<string, Map<string, import("ws").WebSocket>>} room -> id -> socket */
  const rooms = new Map();

  const peersMessage = (room) => JSON.stringify({ t: "peers", peers: [...room.keys()] });
  const broadcastPeers = (room) => {
    const msg = peersMessage(room);
    for (const s of room.values()) if (s.readyState === s.OPEN) s.send(msg);
  };

  wss.on("connection", (socket) => {
    let roomName = null;
    let id = null;

    socket.on("message", (data, isBinary) => {
      if (!isBinary) {
        let msg;
        try {
          msg = JSON.parse(data.toString());
        } catch {
          return;
        }
        if (msg?.t === "hello" && typeof msg.room === "string" && typeof msg.id === "string") {
          roomName = msg.room;
          id = msg.id;
          let room = rooms.get(roomName);
          if (!room) {
            room = new Map();
            rooms.set(roomName, room);
          }
          const prev = room.get(id);
          if (prev && prev !== socket) prev.close();
          room.set(id, socket);
          emit(`join ${roomName}/${id} (${room.size})`);
          broadcastPeers(room);
        }
        return;
      }
      if (!roomName || !id) return;
      const room = rooms.get(roomName);
      if (!room) return;
      const bytes = new Uint8Array(data.buffer, data.byteOffset, data.byteLength);
      const toLen = bytes[0] ?? 0;
      if (bytes.length < 1 + toLen) return;
      const to = toLen === 0 ? null : dec.decode(bytes.subarray(1, 1 + toLen));
      const payload = bytes.subarray(1 + toLen);
      const fromBytes = enc.encode(id);
      const out = new Uint8Array(1 + fromBytes.length + payload.length);
      out[0] = fromBytes.length;
      out.set(fromBytes, 1);
      out.set(payload, 1 + fromBytes.length);
      if (to === null) {
        for (const [pid, s] of room) if (pid !== id && s.readyState === s.OPEN) s.send(out);
      } else {
        const s = room.get(to);
        if (s && s.readyState === s.OPEN) s.send(out);
      }
    });

    socket.on("close", () => {
      if (!roomName || !id) return;
      const room = rooms.get(roomName);
      if (!room || room.get(id) !== socket) return;
      room.delete(id);
      emit(`leave ${roomName}/${id} (${room.size})`);
      if (room.size === 0) rooms.delete(roomName);
      else broadcastPeers(room);
    });
  });

  const address = wss.address();
  const boundPort = typeof address === "object" && address ? address.port : port;
  return {
    port: boundPort,
    rooms,
    close: () =>
      new Promise((resolve) => {
        for (const c of wss.clients) c.terminate();
        wss.close(() => resolve());
      }),
  };
}

const isMain = process.argv[1] && new URL(import.meta.url).pathname === process.argv[1];
if (isMain) {
  const port = Number(process.env.MESH_RELAY_PORT ?? 8787);
  const relay = await startRelay({ port, log: (m) => console.log(`[relay] ${m}`) });
  console.log(`[relay] listening on ws://0.0.0.0:${relay.port}`);
}
