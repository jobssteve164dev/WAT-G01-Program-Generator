export interface ProductReference {
  manufacturer: string;
  model: string;
  description: string;
  qualification: string;
  datasheet_url: string;
  voltage_class_v: number;
}

export interface TestSelection {
  primitive_id: string;
  name: string;
  enabled: boolean;
  parameters: Record<string, number>;
  limits: Record<string, number>;
  unit: string;
  source_condition: string;
  blocked_reason: string | null;
}

export interface CompileRequest {
  product: ProductReference;
  capability: {
    profile_id: string;
    max_voltage_v: number;
    max_current_a: number;
    low_current_floor_a: number;
    pin_map: Record<string, string>;
  };
  tests: TestSelection[];
  usage_boundary: string;
  metadata: Record<string, unknown>;
}

export interface ValidationFinding {
  severity: "info" | "warning" | "error";
  code: string;
  message: string;
  primitive_id: string | null;
}

export interface GeneratedFile {
  path: string;
  media_type: string;
  content: string;
  sha256: string;
}

export interface CompileResult {
  status: string;
  deployable_to_tester: boolean;
  artifact_hash: string;
  findings: ValidationFinding[];
  files: GeneratedFile[];
  replay: {
    results: Array<{
      primitive_id: string;
      name: string;
      value: number;
      unit: string;
      passed: boolean;
    }>;
    curves: Array<{
      primitive_id: string;
      x_label: string;
      y_label: string;
      points: Array<{ x: number; y: number }>;
    }>;
  };
  package_base64: string;
}

