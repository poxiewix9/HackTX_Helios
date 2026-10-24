import type {
  AnyMessage,
  BlackboardMessage,
  DetectionsMessage,
  RoutesUpdatedMessage,
  TelemetryChannelMessage,
} from "./generated/arco";

/** WebSocket channels. Path is `/ws/<channel>`. Mirrors `arco_schemas.messages.Channel`. */
export const CHANNELS = ["detections", "blackboard", "routes", "telemetry"] as const;
export type Channel = (typeof CHANNELS)[number];

export interface ChannelMessage {
  detections: DetectionsMessage;
  blackboard: BlackboardMessage;
  routes: RoutesUpdatedMessage;
  telemetry: TelemetryChannelMessage;
}

export type MessageType = AnyMessage["type"];

/**
 * Message variant(s) whose `type` can be `T`. Distributive so that variants with a
 * multi-literal `type` (e.g. HazardEvent: created|updated|resolved|disputed) are kept.
 */
type Distribute<M, T> = M extends { type: infer U } ? (T extends U ? M : never) : never;
export type MessageOfType<T extends MessageType> = Distribute<AnyMessage, T>;

export function channelPath(channel: Channel, baseUrl = ""): string {
  return `${baseUrl}/ws/${channel}`;
}

/** Type guard narrowing a message by its `type` discriminator. */
export function isMessageOfType<T extends MessageType>(
  msg: AnyMessage,
  type: T,
): msg is MessageOfType<T> {
  return msg.type === type;
}
