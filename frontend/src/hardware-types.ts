export const modeLabels = {
  voltage_current: "施加电压并测电流",
  voltage: "施加电压",
  current_voltage: "施加电流并测电压",
  measure_voltage: "测电压",
  measure_current: "测电流",
  reference: "参考地",
  guard: "Guard",
};

export const paths = ["DC", "AUX", "HF", "Extended", "Chuck"] as const;
export const conductors = ["force", "sense", "guard"] as const;
export const conductorLabels = { force: "Force", sense: "Sense", guard: "Guard" };

export type Mode = keyof typeof modeLabels;
export type RoutePath = typeof paths[number];
export type Conductor = typeof conductors[number];
export type Pin = number | "CHUCK";

export interface ProfileRef {
  id: string;
  revision: number;
}

export interface Channel {
  id: string;
  channel: string;
  port: string;
  path: RoutePath;
  pins: Pin[];
  modes: Mode[];
  max_voltage_v: number | null;
  max_current_a: number | null;
  min_current_range_a: number | null;
  kelvin: boolean;
  shared_group: string;
}

export interface MachineDetails {
  name: string;
  model: string;
  system_version: string;
  specs_version: string;
  specs_fa_version: string;
  test_head: string;
  pin_board: string;
  prober: string;
  chuck: string;
  source: string;
  modules: Array<{ slot: string; model: string; channels: Channel[] }>;
}

export type Machine = MachineDetails & ProfileRef;

export interface Needle {
  id: string;
  site: string;
  needle: string;
  pin: Pin;
  specs_pad: number | null;
  conductors: Conductor[];
}

export interface CardDetails {
  name: string;
  version: string;
  source: string;
  machines: ProfileRef[];
  needles: Needle[];
}

export type Card = CardDetails & ProfileRef;

export interface Structure {
  id: string;
  name: string;
  site: string;
  terminals: Array<{ terminal: string; pad: string; needle_id: string }>;
}

export interface Step {
  phase: string;
  terminal: string;
  mode: Mode;
  voltage_v: number | null;
  current_a: number | null;
  current_range_a: number | null;
  kelvin: boolean;
  resource_id: string | null;
}

export interface Measurement {
  test_index: number;
  structure_id: string;
  steps: Step[];
}

export interface HardwareTarget {
  previous_id?: string | null;
  plan_revision: number;
  machine: ProfileRef;
  card: ProfileRef;
  structures: Structure[];
  measurements: Measurement[];
}

export interface HardwareReview {
  issues: Array<{ code: string; message: string; test_index: number | null }>;
  routes: Array<{
    test_name: string; structure: string; site: string; phase: string; terminal: string;
    pad: string; needle: string; pin: Pin; pad_number: number | null; slot: string; module: string; channel: string;
    port: string; path: RoutePath; mode: Mode;
  }>;
}

export interface HardwareBinding extends HardwareTarget {
  id: string;
  machine_name: string;
  card_name: string;
  review: HardwareReview;
  stale: boolean;
}

export function profileRef({ id, revision }: ProfileRef): ProfileRef {
  return { id, revision };
}

export function sameProfile(left: ProfileRef, right: ProfileRef): boolean {
  return left.id === right.id && left.revision === right.revision;
}
