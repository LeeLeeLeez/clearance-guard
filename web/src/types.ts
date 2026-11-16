/** 与后端 api/ 对应的类型。 */

export interface TaskState {
  running: boolean
  kind: string
  stage: string
  detail: string
  error: string
  finished: boolean
  cancelled: boolean
  elapsed: number
}

export interface Counts {
  components: number
  pads: number
  nets: number
  sheets: number
  mounts: number
  floating: number
  cutouts: number
}

export interface SheetInfo {
  name: string
  nets: number
  shared: number
  path: string
  total: number
  shared_count: number
}

export interface Summary {
  scanned: boolean
  board_name?: string
  project_path?: string
  source_note?: string
  scanned_at?: string
  demo?: boolean
  counts?: Counts
  net_classes?: Record<string, number>
  sheets?: SheetInfo[]
  violations?: { total: number; components: number; errors: number; warnings: number }
  warnings?: string[]
}

export interface StatusResp {
  connected: boolean
  error: string
  scanned: boolean
  demo: boolean
  project_path: string
  task: TaskState
  summary: Summary
}

export interface NetRow {
  net: string
  net_class: string
  class_label: string
  auto_class: string
  auto_class_label: string
  voltage: number | null
  voltage_min: number | null
  voltage_max: number | null
  range_text: string
  confidence: number
  reasons: string[]
  confirmed: boolean
  needs_review: boolean
  is_drive: boolean
  ref_ground: string
  ref_ground_reason: string
  ref_ground_voltage: string
  user_edited: boolean
  follow_net: string
  sheet_side: string
  sheets: string[]
  shared: boolean
  auto_named: boolean
}

export interface NetClassOption {
  value: string
  label: string
}

export interface NetsResp {
  scanned: boolean
  nets: NetRow[]
  classes: NetClassOption[]
  summary: Summary
}

export interface SheetsResp {
  sheets: SheetInfo[]
  shared: string[]
  common: { power: string[]; control: string[] }
}

export interface MountRow {
  name: string
  x: number
  y: number
  width: number
  height: number
  hole: number
  net: string
  layer: string
  net_class: string
  net_class_label: string
  voltage: number | null
  voltage_min: number | null
  voltage_max: number | null
  enabled: boolean
  note: string
}

export interface FloatingRow {
  designator: string
  comment: string
  footprint: string
  x: number
  y: number
  width: number
  height: number
  layer: string
  pad_count: number
  net_class: string
  net_class_label: string
  voltage: number | null
  voltage_min: number | null
  voltage_max: number | null
  enabled: boolean
}

export interface CutoutRow {
  index: number
  kind: string
  name: string
  x: number
  y: number
  width: number
  height: number
  diameter: number
  role: string
  points: number
  outline: [number, number][]
}

export interface ComponentRow {
  designator: string
  comment: string
  footprint: string
  category: string
  layer: string
  x: number
  y: number
  rotation: number
  pad_count: number
}

export interface ComponentsResp {
  rows: ComponentRow[]
  total: number
  categories: NetClassOption[]
}

export interface ObjectsResp {
  mounts: MountRow[]
  floating: FloatingRow[]
  cutouts: CutoutRow[]
  net_classes: NetClassOption[]
}

export interface RulesResp {
  matrix: Record<string, number>
  isolation_gap_mm: number
  isolation_clearance_mm: number
  voltage_step_enabled: boolean
  voltage_step_v: number
  voltage_step_mm: number
  check_unknown: boolean
  include_tracks: boolean
  cross_class_only: boolean
  check_same_component: boolean
  board_edge_gap_mm: number
  board_edge_enabled: boolean
  component_gap_mm: number
  component_gap_enabled: boolean
  body_gap_mm: number
  body_gap_enabled: boolean
  matrix_keys: string[]
  [key: string]: unknown
}

export interface ViolationRow {
  designator_a?: string
  designator_b?: string
  item_a?: string
  item_b?: string
  net_a: string
  net_b: string
  class_a: string
  class_b: string
  class_a_label: string
  class_b_label: string
  actual_mm: number
  required_mm: number
  deficit_mm: number
  x: number
  y: number
  rule_name: string
  severity: string
  count?: number
  pad_a?: string
  pad_b?: string
}

export interface RuleOption {
  key: string
  label: string
  count: number
}

export interface ViolationsResp {
  rows: ViolationRow[]
  rules: RuleOption[]
  total: number
  filtered: number
  summary: {
    pads: { total: number; errors: number; warnings: number }
    components: { total: number; errors: number; warnings: number }
    worst_deficit: number
  }
}

export interface LayoutData {
  scanned: boolean
  board_name: string
  width: number
  height: number
  outline: [number, number][]
  components: {
    designator: string
    comment: string
    footprint: string
    x: number
    y: number
    rotation: number
    layer: string
    bbox: number[]
    body_bbox: number[]
  }[]
  pads: {
    x: number
    y: number
    w: number
    h: number
    rot: number
    layer: string
    net: string
    comp: string
    cls: string
    cls_label: string
    hole: number
  }[]
  cutouts: {
    kind: string
    name: string
    role: string
    x: number
    y: number
    w: number
    h: number
    points: [number, number][]
  }[]
  violations: { x: number; y: number; a: string; b: string; deficit: number; severity: string }[]
  class_labels: Record<string, string>
}

export interface SolveRow {
  designator: string
  x0: number
  y0: number
  x1: number
  y1: number
  rot0: number
  rot1: number
  moved: boolean
  reason: string
  shift_mm: number
  details: string[]
}

export interface PageProps {
  setStatus: (msg: string) => void
  setStatusError: (msg: string) => void
  active: boolean
  /** 数据版本号：扫描/导入后 +1，各页据此重新拉数据 */
  revision: number
  onRefreshGlobal: () => void
}
