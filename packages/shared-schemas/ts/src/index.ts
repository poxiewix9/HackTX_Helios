/**
 * @arco/schemas - TypeScript side of the ARCO contract.
 *
 * Everything here is derived from the Pydantic models in `python/arco_schemas`.
 * Never hand-write a duplicate type in an app; import it from here.
 */
export * from "./generated/arco";
export { SCHEMA_VERSION } from "./generated/version";
export {
  HAZARD_CLASSES,
  HAZARD_CLASS_IDS,
  MACRO_CLASSES,
  MICRO_CLASSES,
  classesForStream,
  hazardClassInfo,
} from "./hazardClasses";
export type { HazardClassId } from "./hazardClasses";
export { CHANNELS, channelPath, isMessageOfType } from "./channels";
export type { ChannelMessage, MessageOfType, MessageType } from "./channels";
export { SIMULATION_LABEL } from "./labels";
