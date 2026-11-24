import { useEffect, useRef, useState } from "react";
import type { FormEvent } from "react";
import { modeLabels, profileRef, sameProfile } from "./hardware-types";
import type { Card, HardwareBinding, HardwareReview, HardwareTarget, Machine, Mode, ProfileRef, Step, Structure } from "./hardware-types";
import type { Plan } from "./types";
import { downloadFile, jsonRequest, messageFor, positiveNumber, requestData, RequestError } from "./workbench-api";

type Requirement = Omit<Step, "voltage_v" | "current_a" | "current_range_a"> & {
  key: string; voltage_v: string; current_a: string; current_range_a: string;
};
type MeasurementDraft = { key: string; test_index: number; structure_id: string; steps: Requirement[] };
type TargetDraft = {
  machine: ProfileRef | null; card: ProfileRef | null; previousId: string | null;
  structures: Structure[]; measurements: MeasurementDraft[];
};
type Catalog = { machines: Machine[]; cards: Card[]; bindings: HardwareBinding[] };

const emptyTarget: TargetDraft = { machine: null, card: null, previousId: null, structures: [], measurements: [] };
const modes = Object.entries(modeLabels) as Array<[Mode, string]>;
const requirements = [
  ["voltage_v", "电压边界（V）"], ["current_a", "电流边界（A）"], ["current_range_a", "测流量程（A）"],
] as const;

function draftFrom(binding: HardwareBinding, machine?: Machine, card?: Card, retainMeasurements = true, retainResources = true): TargetDraft {
  return {
    machine: machine ? profileRef(machine) : binding.machine, card: card ? profileRef(card) : binding.card,
    previousId: binding.id, structures: binding.structures,
    measurements: retainMeasurements ? binding.measurements.map((measurement) => ({
      ...measurement, key: crypto.randomUUID(), steps: measurement.steps.map((step) => ({
        ...step, key: crypto.randomUUID(), voltage_v: step.voltage_v?.toString() ?? "", current_a: step.current_a?.toString() ?? "",
        current_range_a: step.current_range_a?.toString() ?? "", resource_id: retainResources ? step.resource_id : null,
      })),
    })) : [],
  };
}

interface Props {
  plan: Plan;
  onSessionExpired: () => void;
  onDirtyChange: (dirty: boolean) => void;
  onSavingChange: (saving: boolean) => void;
  allowLeave: () => boolean;
}

export default function PlanHardware({ plan, onSessionExpired, onDirtyChange, onSavingChange, allowLeave }: Props) {
  const endpoint = `/api/plans/${plan.id}/hardware`;
  const [catalog, setCatalog] = useState<Catalog | null>(null);
  const [draft, setDraft] = useState<TargetDraft | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [review, setReview] = useState<HardwareReview | null>(null);
  const [busy, setBusy] = useState(false);
  const [changed, setChanged] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [retry, setRetry] = useState(0);
  const active = useRef<AbortController | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    active.current = controller;
    Promise.all([
      requestData<Machine[]>("/api/hardware/machines", onSessionExpired, { signal: controller.signal }),
      requestData<Card[]>("/api/hardware/cards", onSessionExpired, { signal: controller.signal }),
      requestData<HardwareBinding[]>(endpoint, onSessionExpired, { signal: controller.signal }),
    ]).then(([machines, cards, bindings]) => {
      if (!controller.signal.aborted) setCatalog({ machines, cards, bindings });
    }).catch((error: unknown) => {
      if (!controller.signal.aborted) setError(messageFor(error));
    });
    return () => controller.abort();
  }, [endpoint, onSessionExpired, retry]);

  const selected = catalog?.bindings.find((row) => row.id === selectedId);
  const machine = draft?.machine && catalog?.machines.find((row) => sameProfile(row, draft.machine!));
  const cards = catalog?.cards.filter((card) => machine && card.machines.some((entry) => sameProfile(entry, machine))) ?? [];
  const card = draft?.card && cards.find((row) => sameProfile(row, draft.card!));

  function edit(next: TargetDraft) {
    setDraft(next);
    setChanged(true);
    onDirtyChange(true);
    setReview(null);
    setError("");
    setNotice("");
  }

  function open(binding: HardwareBinding | null) {
    if (!allowLeave()) return;
    setSelectedId(binding?.id ?? null);
    setDraft(binding ? binding.stale || plan.archived ? null : draftFrom(binding) : emptyTarget);
    setReview(binding?.review ?? null);
    setChanged(false);
    onDirtyChange(false);
    setError("");
    setNotice("");
  }

  function renew(binding: HardwareBinding) {
    const latestMachine = catalog?.machines.find((row) => row.id === binding.machine.id);
    const latestCard = catalog?.cards.find((row) => row.id === binding.card.id);
    edit(draftFrom(binding, latestMachine, latestCard, binding.plan_revision === plan.revision, false));
    setSelectedId(null);
  }

  function editStructure(index: number, next: Structure) {
    if (!draft) return;
    edit({ ...draft, structures: draft.structures.map((row, position) => position === index ? next : row) });
  }

  function editMeasurement(index: number, next: MeasurementDraft) {
    if (!draft) return;
    edit({ ...draft, measurements: draft.measurements.map((row, position) => position === index ? next : row) });
  }

  function payload(): HardwareTarget {
    if (!draft || !machine || !card) throw new RequestError("请选择目标机台和已确认可安装的探针卡。");
    return {
      plan_revision: plan.revision, machine: profileRef(machine), card: profileRef(card), structures: draft.structures,
      previous_id: draft.previousId,
      measurements: draft.measurements.map(({ test_index, structure_id, steps }) => ({
        test_index, structure_id, steps: steps.map(({ key: _key, ...step }) => ({
          ...step, voltage_v: positiveNumber(step.voltage_v), current_a: positiveNumber(step.current_a),
          current_range_a: positiveNumber(step.current_range_a),
        })),
      })),
    };
  }

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (busy) return;
    const submitter = (event.nativeEvent as SubmitEvent).submitter as HTMLButtonElement | null;
    const save = submitter?.value === "save";
    const signal = active.current?.signal;
    setError("");
    setNotice("");
    setBusy(true);
    onSavingChange(true);
    try {
      const options = { ...jsonRequest("POST", payload()), signal };
      if (save) {
        const binding = await requestData<HardwareBinding>(endpoint, onSessionExpired, options);
        if (signal?.aborted) return;
        setSelectedId(binding.id);
        setDraft((previous) => previous && { ...previous, previousId: binding.id });
        setCatalog((previous) => previous && { ...previous, bindings: [binding, ...previous.bindings.filter((row) =>
          row.machine.id !== binding.machine.id || row.card.id !== binding.card.id)] });
        setReview(binding.review);
        setChanged(false);
        onDirtyChange(false);
        setNotice("已保存机台接线。");
      } else {
        const result = await requestData<HardwareReview>(`${endpoint}/review`, onSessionExpired, options);
        if (!signal?.aborted) setReview(result);
      }
    } catch (error) {
      if (!signal?.aborted) setError(messageFor(error));
    } finally {
      if (!signal?.aborted) {
        setBusy(false);
        onSavingChange(false);
      }
    }
  }

  async function download() {
    if (!selected || changed) return;
    const signal = active.current?.signal;
    setError("");
    try {
      await downloadFile(`${endpoint}/${selected.id}/export`, "wat-hardware.json", onSessionExpired, { signal });
    } catch (error) {
      if (!signal?.aborted) setError(messageFor(error));
    }
  }

  return <section aria-labelledby="plan-hardware-title">
    <div className="workbench-section-head">
      <h3 id="plan-hardware-title">机台接线</h3>
      {catalog && !plan.archived && <button type="button" className="secondary-action" disabled={busy || catalog.machines.length === 0}
        onClick={() => open(null)}>添加机台组合</button>}
    </div>
    {error && <p className="workbench-alert" role="alert">{error} <button type="button" className="secondary-action" disabled={busy}
      onClick={() => { setError(""); setRetry((value) => value + 1); }}>重新读取档案</button></p>}
    {notice && <p className="workbench-notice" role="status">{notice}</p>}
    {!catalog && !error && <p role="status">正在读取机台档案与接线…</p>}
    {catalog && !catalog.machines.length && <p className="workbench-empty">先在「机台与探针卡」登记实装机台。</p>}
    {catalog && catalog.machines.length > 0 && !catalog.cards.length && <p className="workbench-empty">先在「机台与探针卡」登记卡针接线。</p>}
    {catalog && !draft && !selected && catalog.machines.length > 0 && catalog.cards.length > 0 && !catalog.bindings.length &&
      <p className="workbench-empty">{plan.archived ? "没有已保存的机台接线。" : "添加机台组合，开始配置这份计划的接线。"}</p>}
    {catalog && catalog.bindings.length > 0 && <label className="hardware-select">已保存的机台组合
      <select value={selected?.id ?? ""} disabled={busy} onChange={(event) => {
        const binding = catalog.bindings.find((row) => row.id === event.target.value);
        if (binding) open(binding);
      }}>
        <option value="" disabled>选择已保存的组合</option>
        {catalog.bindings.map((row, index) => <option key={row.id} value={row.id}>
          {row.machine_name} · {row.card_name} · {row.stale ? "待重核" : row.review.issues.length ? "接线待补齐" : "已核对"}（{index + 1}）
        </option>)}
      </select>
    </label>}
    {selectedId && !selected && <p className="workbench-empty">这条接线已被替换，请选择最新保存的组合。</p>}
    {selected?.stale && <div className="workbench-actions plan-item-heading">
      <span>计划或设备档案已换版。此处显示保存时的接线。</span>
      {!plan.archived && <button className="secondary-action" type="button" disabled={busy} onClick={() => renew(selected)}>按最新档案重新核对</button>}
    </div>}
    {catalog && draft && !selected?.stale && (!selectedId || selected) && <form onSubmit={submit} className="hardware-target">
      <fieldset disabled={busy}>
        <div className="workbench-fields">
          <label>目标机台<select required value={machine?.id ?? ""} onChange={(event) => {
            const next = catalog.machines.find((row) => row.id === event.target.value);
            edit({
              ...draft, machine: next ? profileRef(next) : null, card: null,
              previousId: draft.machine?.id === next?.id ? draft.previousId : null,
              measurements: draft.measurements.map((row) => ({ ...row, steps: row.steps.map((step) => ({ ...step, resource_id: null })) })),
            });
          }}>
            <option value="">选择机台</option>
            {catalog.machines.map((row) => <option key={row.id} value={row.id}>{row.name} · 配置版本 {row.revision}</option>)}
          </select></label>
          <label>探针卡<select required value={card?.id ?? ""} disabled={!machine} onChange={(event) => {
            const next = cards.find((row) => row.id === event.target.value);
            edit({ ...draft, card: next ? profileRef(next) : null,
              previousId: draft.card?.id && draft.card.id !== next?.id ? null : draft.previousId });
          }}>
            <option value="">{machine && !cards.length ? "该机台尚无确认可安装的探针卡" : "选择探针卡"}</option>
            {cards.map((row) => <option key={row.id} value={row.id}>{row.name} · {row.version}</option>)}
          </select></label>
        </div>
        {card && <>
          <div className="workbench-section-head plan-item-heading"><h3>测试结构</h3><button type="button" className="secondary-action"
            disabled={draft.structures.length >= 64} onClick={() => edit({ ...draft, structures: [...draft.structures, {
              id: crypto.randomUUID(), name: "", site: "", terminals: [],
            }] })}>添加结构</button></div>
          {draft.structures.map((structure, index) => <StructureEditor key={structure.id} structure={structure} card={card}
            onChange={(next) => editStructure(index, next)} legacy={plan.pin_setup?.mappings ?? []}
            onRemove={() => {
              if (draft.measurements.some((row) => row.structure_id === structure.id) && !window.confirm("移除此结构和引用它的测项接线？")) return;
              edit({ ...draft, structures: draft.structures.filter((row) => row.id !== structure.id),
                measurements: draft.measurements.filter((row) => row.structure_id !== structure.id) });
            }} />)}
          <div className="workbench-section-head plan-item-heading"><h3>测项与资源</h3><button type="button" className="secondary-action"
            disabled={!plan.tests.length || !draft.structures.length || draft.measurements.length >= 128} onClick={() => edit({
              ...draft, measurements: [...draft.measurements, { key: crypto.randomUUID(), test_index: -1, structure_id: "", steps: [] }],
            })}>添加测项接线</button></div>
          {draft.measurements.map((row, index) => <MeasurementEditor key={row.key} value={row} plan={plan}
            structures={draft.structures} card={card} machine={machine!} onChange={(next) => editMeasurement(index, next)}
            onRemove={() => edit({ ...draft, measurements: draft.measurements.filter((item) => item.key !== row.key) })} />)}
        </>}
      </fieldset>
      <div className="workbench-actions">
        <button className="primary-action" type="submit" value="save" disabled={busy || !card}>{busy ? "处理中…" : "保存机台接线"}</button>
        <button className="secondary-action" type="submit" value="review" disabled={busy || !card}>核对接线</button>
      </div>
    </form>}
    {review && <RouteReview review={review} />}
    {selected && !selected.stale && !changed && !selected.review.issues.length && <>
      <div className="workbench-actions plan-item-heading">
        <button className="secondary-action" type="button" disabled={busy} onClick={() => void download()}>下载工程接线</button>
      </div>
      <RecipeBuild key={selected.id} path={`${endpoint}/${selected.id}/specs31-build`} onSessionExpired={onSessionExpired}
        machine={catalog?.machines.find((row) => sameProfile(row, selected.machine))}
        missingPad={selected.review.routes.some((route) => route.pad_number == null || route.pin === "CHUCK")} />
    </>}
  </section>;
}

function StructureEditor({ structure, card, legacy, onChange, onRemove }: {
  structure: Structure; card: Card; legacy: NonNullable<Plan["pin_setup"]>["mappings"];
  onChange: (value: Structure) => void; onRemove: () => void;
}) {
  const sites = Array.from(new Set(card.needles.map((needle) => needle.site)));
  const needles = card.needles.filter((needle) => needle.site === structure.site);
  return <fieldset className="plan-item">
    <legend>{structure.name || "测试结构"}</legend>
    <div className="workbench-fields">
      <label>结构名称<input value={structure.name} required maxLength={80} onChange={(event) => onChange({ ...structure, name: event.target.value })} /></label>
      <label>Site<select required value={structure.site} onChange={(event) => onChange({
        ...structure, site: event.target.value, terminals: structure.terminals.map((row) => ({ ...row, needle_id: "" })),
      })}><option value="">选择 Site</option>{sites.map((site) => <option key={site}>{site}</option>)}</select></label>
    </div>
    {structure.terminals.map((row, index) => {
      const update = (changes: Partial<typeof row>) => onChange({ ...structure, terminals: structure.terminals.map((entry, position) =>
        position === index ? { ...entry, ...changes } : entry) });
      return <div className="hardware-row" key={index}>
        <div className="workbench-fields hardware-three-fields">
          <label>端子<input required maxLength={80} value={row.terminal} onChange={(event) => update({ terminal: event.target.value })} /></label>
          <label>焊盘<input required maxLength={80} value={row.pad} onChange={(event) => update({ pad: event.target.value })} /></label>
          <label>卡针<select required value={needles.some((needle) => needle.id === row.needle_id) ? row.needle_id : ""} onChange={(event) => update({ needle_id: event.target.value })}>
            <option value="">选择卡针</option>{needles.map((needle) => <option key={needle.id} value={needle.id}>{needle.needle} → {needle.pin}</option>)}</select></label>
        </div>
        <div className="item-actions"><button type="button" onClick={() => onChange({ ...structure, terminals: structure.terminals.filter((_, position) => position !== index) })}>移除端子</button></div>
      </div>;
    })}
    <div className="item-actions">
      {!structure.terminals.length && legacy.length > 0 && <button type="button" onClick={() => onChange({
        ...structure, terminals: legacy.slice(0, 48).map(({ terminal, wafer_pad }) => ({ terminal, pad: wafer_pad, needle_id: "" })),
      })}>沿用已有端子和焊盘</button>}
      <button type="button" disabled={structure.terminals.length >= 48} onClick={() => onChange({
        ...structure, terminals: [...structure.terminals, { terminal: "", pad: "", needle_id: "" }],
      })}>添加端子</button>
      <button type="button" onClick={onRemove}>移除结构</button>
    </div>
  </fieldset>;
}

function MeasurementEditor({ value, plan, structures, card, machine, onChange, onRemove }: {
  value: MeasurementDraft; plan: Plan; structures: Structure[]; card: Card; machine: Machine;
  onChange: (next: MeasurementDraft) => void; onRemove: () => void;
}) {
  const structure = structures.find((row) => row.id === value.structure_id);
  const test = plan.tests[value.test_index];
  const channels = machine.modules.flatMap((module) => module.channels.map((channel) => ({ channel, module })));
  return <fieldset className="plan-item">
    <legend>{test?.name || "测项接线"}</legend>
    <div className="workbench-fields">
      <label>测项<select required value={test ? value.test_index : ""} onChange={(event) => onChange({ ...value, test_index: event.target.value ? Number(event.target.value) : -1 })}>
        <option value="">选择测项</option>{plan.tests.map((test, index) => <option key={index} value={index}>{test.name}</option>)}</select></label>
      <label>待测结构<select required value={value.structure_id} onChange={(event) => onChange({ ...value, structure_id: event.target.value, steps: value.steps.map((row) => ({
        ...row, terminal: "", resource_id: null,
      })) })}><option value="">选择结构</option>
        {structures.map((row) => <option key={row.id} value={row.id}>{row.name || "未命名结构"} · {row.site || "未选 Site"}</option>)}</select></label>
    </div>
    {test?.conditions && <p className="workbench-empty">{test.conditions}</p>}
    {structure && value.steps.map((step, index) => {
      const terminal = structure.terminals.find((row) => row.terminal === step.terminal);
      const needle = card.needles.find((row) => row.id === terminal?.needle_id && row.site === structure.site);
      const candidates = needle ? channels.filter(({ channel }) => channel.pins.includes(needle.pin) && channel.modes.includes(step.mode)) : [];
      const update = (changes: Partial<Requirement>) => onChange({
        ...value, steps: value.steps.map((row) => row.key === step.key ? { ...row, ...changes } : row),
      });
      const passive = step.mode === "reference" || step.mode === "guard";
      const measuringCurrent = step.mode === "voltage_current" || step.mode === "measure_current";
      return <fieldset className="plan-item hardware-step" key={step.key}>
        <legend>{step.terminal || `测量动作 ${index + 1}`}</legend>
        <div className="workbench-fields">
          <label>阶段<input required maxLength={80} value={step.phase} onChange={(event) => update({ phase: event.target.value })} /></label>
          <label>测量端子<select required value={terminal?.terminal ?? ""} onChange={(event) => update({ terminal: event.target.value, resource_id: null })}>
            <option value="">选择端子</option>{structure.terminals.filter((row) => row.terminal.trim()).map((row, index) =>
              <option key={index} value={row.terminal}>{row.terminal}</option>)}</select></label>
          <label>测量动作<select value={step.mode} onChange={(event) => update({
            mode: event.target.value as Mode, resource_id: null, voltage_v: "", current_a: "", current_range_a: "", kelvin: false,
          })}>{modes.map(([mode, label]) => <option key={mode} value={mode}>{label}</option>)}</select></label>
          <label>测量资源<select value={step.resource_id ?? ""} onChange={(event) => update({ resource_id: event.target.value || null })}>
            <option value="">{needle && !candidates.length ? "没有能到达该针位的资源" : "选择资源"}</option>
            {step.resource_id && !candidates.some(({ channel }) => channel.id === step.resource_id) &&
              <option value={step.resource_id}>原路径需重新选择</option>}
            {candidates.map(({ channel, module }) => <option key={channel.id} value={channel.id}>{module.model} · {channel.port} · {channel.path}</option>)}
          </select></label>
          {!passive && requirements.map(([field, label]) => (field !== "current_range_a" || measuringCurrent) &&
            <label key={field}>{label}<input value={step[field]} maxLength={40} onChange={(event) => update({ [field]: event.target.value })} /></label>)}
        </div>
        {needle && <p className="workbench-empty">{terminal?.pad} → {needle.needle} → 测试头针位 {needle.pin}</p>}
        {!passive && <div className="test-pin-options"><label><input type="checkbox" checked={step.kelvin}
          onChange={(event) => update({ kelvin: event.target.checked })} />四线测量</label></div>}
        <div className="item-actions"><button type="button" onClick={() => onChange({
          ...value, steps: value.steps.filter((row) => row.key !== step.key),
        })}>移除测量动作</button></div>
      </fieldset>;
    })}
    <div className="item-actions">
      <button type="button" disabled={!structure?.terminals.length || value.steps.length >= 32} onClick={() => onChange({ ...value, steps: [...value.steps, {
        key: crypto.randomUUID(), phase: "测量", terminal: "", mode: "voltage_current", resource_id: null,
        voltage_v: "", current_a: "", current_range_a: "", kelvin: false,
      }] })}>添加测量动作</button>
      <button type="button" onClick={onRemove}>移除测项接线</button>
    </div>
  </fieldset>;
}

function RouteReview({ review }: { review: HardwareReview }) {
  return <section className="route-review" aria-labelledby="route-review-title">
    <h3 id="route-review-title">接线核对结果</h3>
    {review.issues.length ? <ul className="workbench-alert" aria-label="待处理项">{review.issues.map((issue, index) =>
      <li key={index}>{issue.message}</li>)}</ul> : <p className="workbench-notice" role="status">本组合的测项接线核对通过。</p>}
    <ul className="route-list">{review.routes.map((route, index) => <li key={index}>
      <strong>{route.test_name} · {route.structure} · Site {route.site} · {route.phase}</strong>
      <span>{route.terminal} · {route.pad} → {route.needle}{route.pad_number != null && ` / Pad ${route.pad_number}`} → 针位 {route.pin}</span>
      <span>{modeLabels[route.mode]} · {route.module} 槽位 {route.slot} → {route.port} / {route.path}</span>
    </li>)}</ul>
  </section>;
}

function RecipeBuild({ path, machine, missingPad, onSessionExpired }: {
  path: string; machine?: Machine; missingPad: boolean; onSessionExpired: () => void;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const active = useRef<AbortController | null>(null);
  useEffect(() => () => active.current?.abort(), []);

  async function build(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (busy) return;
    const fields = new FormData(event.currentTarget);
    const value = (key: string) => String(fields.get(key) ?? "").trim();
    const names = (key: string) => value(key).split(/[,，]/).map((name) => name.trim()).filter(Boolean);
    const controller = new AbortController();
    active.current = controller;
    setBusy(true);
    setError("");
    try {
      await downloadFile(path, "specs31-build.zip", onSessionExpired, {
        ...jsonRequest("POST", {
          test_plan: value("test_plan"), recipe_key: value("recipe_key"), recipe_file: value("recipe_file"),
          limit_file: value("limit_file") || null, process: value("process") || null, prober_file: value("prober_file") || null,
          libraries: names("libraries"), constants: names("constants"),
        }),
        signal: controller.signal,
      });
    } catch (error) {
      if (!controller.signal.aborted) setError(messageFor(error));
    } finally {
      if (!controller.signal.aborted) setBusy(false);
    }
  }

  return <details className="recipe-build">
    <summary>SPECS 配方准备包</summary>
    {!/^D\.03\.1[0-9]$/.test(machine?.specs_version ?? "") ? <p className="workbench-empty">请核对机台档案。此构建包适用于 SPECS D.03.1x。</p>
      : missingPad ? <p className="workbench-empty">请在参与测试的探针卡针位上补全 SPECS Pad Number。使用 Chuck 时请先单独验证 Probe Spec。</p>
      : <form onSubmit={build}>
        <p className="workbench-empty">在 Test Organizer 中核对针位、测项和算法后，填写该工程计划的原厂文件名。配方由工程环境中的 SPECS 生成。</p>
        {error && <p className="workbench-alert" role="alert">{error}</p>}
        <fieldset className="workbench-fields" disabled={busy}>
          <label>原厂计划文件<input name="test_plan" required maxLength={160} placeholder="例如 engineering/leak.tpl" /></label>
          <label>新配方文件名<input name="recipe_file" required maxLength={160} /></label>
          <label>Recipe Key<input name="recipe_key" required maxLength={30} pattern="[A-Za-z0-9_]+" /></label>
          <label>限值文件<input name="limit_file" maxLength={160} /></label>
          <label>测量算法库<input name="libraries" maxLength={960} placeholder="多个库用逗号分隔" /></label>
          <label>计划常量文件<input name="constants" maxLength={2560} placeholder="多个文件用逗号分隔" /></label>
          <label>探针台配方文件<input name="prober_file" maxLength={160} /></label>
          <label>Process ID<input name="process" maxLength={80} /></label>
        </fieldset>
        <div className="workbench-actions"><button type="submit" className="secondary-action" disabled={busy}>
          {busy ? "正在打包…" : "下载 SPECS 配方准备包"}</button></div>
      </form>}
  </details>;
}
