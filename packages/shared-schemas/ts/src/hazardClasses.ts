import taxonomy from "../../schema/hazard_classes.json";
import type { HazardClass, HazardClassInfo } from "./generated/arco";

export type HazardClassId = HazardClass;

/** Full taxonomy, keyed by class id. Generated from `arco_schemas.hazard_classes`. */
export const HAZARD_CLASSES: Readonly<Record<HazardClassId, HazardClassInfo>> = Object.fromEntries(
  (taxonomy.classes as HazardClassInfo[]).map((c) => [c.id, c]),
) as Record<HazardClassId, HazardClassInfo>;

export const HAZARD_CLASS_IDS: readonly HazardClassId[] = (
  taxonomy.classes as HazardClassInfo[]
).map((c) => c.id);

export const MACRO_CLASSES: readonly HazardClassId[] = taxonomy.macro as HazardClassId[];
export const MICRO_CLASSES: readonly HazardClassId[] = taxonomy.micro as HazardClassId[];

export function hazardClassInfo(id: HazardClassId): HazardClassInfo {
  return HAZARD_CLASSES[id];
}

export function classesForStream(stream: "macro" | "micro"): readonly HazardClassId[] {
  return stream === "macro" ? MACRO_CLASSES : MICRO_CLASSES;
}
