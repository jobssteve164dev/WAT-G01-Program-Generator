import { useEffect, useRef, useState } from "react";
import type { FormEvent } from "react";
import { conductorLabels, conductors, modeLabels, paths, profileRef, sameProfile } from "./hardware-types";
import type { Card, CardDetails, Channel, Machine, MachineDetails, Mode, Needle, Pin, RoutePath } from "./hardware-types";
import { jsonRequest, messageFor, positiveNumber, requestData, RequestError } from "./workbench-api";

const machineFields = [
  ["name", "机台编号"], ["model", "机型"], ["system_version", "系统软件版本"],
  ["test_head", "测试头"], ["pin_board", "Pin Board"], ["source", "装机配置依据"],
  ["specs_version", "SPECS 版本"], ["specs_fa_version", "SPECS-FA 版本"], ["prober", "探针台"], ["chuck", "Chuck"],
] as const;

const modeEntries = Object.entries(modeLabels) as Array<[Mode, string]>;
const numberFields = [["max_voltage_v", "最大电压（V）"], ["max_current_a", "最大电流（A）"],
  ["min_current_range_a", "最小测流量程（A）"]] as const;

const emptyMachine: MachineDetails = {
  name: "", model: "", system_version: "", specs_version: "", specs_fa_version: "",
  test_head: "", pin_board: "", prober: "", chuck: "", source: "", modules: [],
};
const emptyCard: CardDraft = { name: "", version: "", source: "", machines: [], needles: [] };

interface ChannelDraft extends Omit<Channel, "pins" | "max_voltage_v" | "max_current_a" | "min_current_range_a"> {
  pins: string;
  max_voltage_v: string;
  max_current_a: string;
  min_current_range_a: string;
}

interface ModuleDraft { key: string; slot: string; model: string; channels: ChannelDraft[] }
interface MachineDraft extends Omit<MachineDetails, "modules"> { modules: ModuleDraft[] }
interface CardDraft extends Omit<CardDetails, "needles"> { needles: Array<Omit<Needle, "pin" | "specs_pad"> & { pin: string; specs_pad: string }> }

type Editor = {
  kind: "machines"; existing?: Machine; value: MachineDraft;
} | {
  kind: "cards"; existing?: Card; value: CardDraft;
};

function machineDraft(machine: MachineDetails): MachineDraft {
  const { id: _id, revision: _revision, ...details } = machine as Machine;
  return {
    ...details, modules: details.modules.map((module) => ({
      ...module, key: crypto.randomUUID(), channels: module.channels.map((channel) => ({
        ...channel, pins: channel.pins.join(", "), max_voltage_v: channel.max_voltage_v?.toString() ?? "",
        max_current_a: channel.max_current_a?.toString() ?? "", min_current_range_a: channel.min_current_range_a?.toString() ?? "",
      })),
    })),
  };
}

function cardDraft(card: Card): CardDraft {
  const { id: _id, revision: _revision, ...details } = card;
  return { ...details, needles: details.needles.map((needle) => ({
    ...needle, pin: String(needle.pin), specs_pad: needle.specs_pad?.toString() ?? "",
  })) };
}

function expandPins(text: string): number[] {
  const pins: number[] = [];
  for (const entry of text.trim().split(/[,，\s]+/)) {
    const range = /^(\d{1,2})(?:-(\d{1,2}))?$/.exec(entry);
    if (!range) throw new RequestError("可达针位请填写 1–48 的针号，用逗号分隔，也可填写区间。");
    const start = Number(range[1]);
    const end = Number(range[2] ?? start);
    if (start < 1 || end > 48 || start > end) throw new RequestError("可达针位请填写 1–48 的针号或顺序区间。");
    for (let pin = start; pin <= end; pin++) pins.push(pin);
  }
  if (new Set(pins).size !== pins.length) throw new RequestError("可达针位有重复，请核对针号或区间。");
  return pins;
}

function machinePayload(draft: MachineDraft): MachineDetails {
  return {
    ...draft, modules: draft.modules.map(({ slot, model, channels }) => ({
      slot, model, channels: channels.map((channel) => ({
        ...channel, pins: channel.path === "Chuck" ? ["CHUCK"] : expandPins(channel.pins),
        max_voltage_v: positiveNumber(channel.max_voltage_v), max_current_a: positiveNumber(channel.max_current_a),
        min_current_range_a: positiveNumber(channel.min_current_range_a),
      })),
    })),
  };
}

function cardPayload(draft: CardDraft): CardDetails {
  if (!draft.machines.length) throw new RequestError("请确认至少一套可安装这张探针卡的机台配置。");
  return {
    ...draft, needles: draft.needles.map((needle) => {
      const pin = needle.pin.trim().toUpperCase();
      if (pin !== "CHUCK" && !/^(?:[1-9]|[1-3][0-9]|4[0-8])$/.test(pin)) {
        throw new RequestError("测试头针位请填写 1–48 的针号或 CHUCK。");
      }
      if (!needle.conductors.length) throw new RequestError("请为每根卡针选择实际接线的导体。");
      const pad = needle.specs_pad.trim();
      if (pad && (!/^\d{1,10}$/.test(pad) || Number(pad) < 1 || Number(pad) > 2147483647)) {
        throw new RequestError("SPECS Pad Number 请填写 Probe Spec 使用的正整数。");
      }
      return { ...needle, pin: (pin === "CHUCK" ? pin : Number(pin)) as Pin, specs_pad: pad ? Number(pad) : null };
    }),
  };
}

interface DirectoryProps {
  onSessionExpired: () => void;
  onDirtyChange: (dirty: boolean) => void;
  onSavingChange: (saving: boolean) => void;
  allowLeave: () => boolean;
}

export default function HardwareDirectory({ onSessionExpired, onDirtyChange, onSavingChange, allowLeave }: DirectoryProps) {
  const [machines, setMachines] = useState<Machine[]>([]);
  const [cards, setCards] = useState<Card[]>([]);
  const [editor, setEditor] = useState<Editor | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [attempt, setAttempt] = useState(0);
  const active = useRef<AbortController | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    active.current = controller;
    setLoading(true);
    Promise.all([
      requestData<Machine[]>("/api/hardware/machines", onSessionExpired, { signal: controller.signal }),
      requestData<Card[]>("/api/hardware/cards", onSessionExpired, { signal: controller.signal }),
    ]).then(([nextMachines, nextCards]) => {
      if (controller.signal.aborted) return;
      setMachines(nextMachines);
      setCards(nextCards);
    }).catch((error: unknown) => {
      if (!controller.signal.aborted) setError(messageFor(error));
    }).finally(() => {
      if (!controller.signal.aborted) setLoading(false);
    });
    return () => controller.abort();
  }, [attempt, onSessionExpired]);

  function open(next: Editor | null) {
    if (!allowLeave()) return;
    setEditor(next);
    setError("");
    setNotice("");
    onDirtyChange(false);
  }

  function update(next: Editor) {
    setEditor(next);
    setNotice("");
    onDirtyChange(true);
  }

  async function save(event: FormEvent) {
    event.preventDefault();
    if (!editor || busy) return;
    const signal = active.current?.signal;
    setError("");
    setNotice("");
    setBusy(true);
    onSavingChange(true);
    try {
      const path = `/api/hardware/${editor.kind}${editor.existing ? `/${editor.existing.id}` : ""}`;
      const method = editor.existing ? "PUT" : "POST";
      const version = editor.existing ? { revision: editor.existing.revision } : {};
      if (editor.kind === "machines") {
        const value = await requestData<Machine>(path, onSessionExpired, { ...jsonRequest(method, { ...machinePayload(editor.value), ...version }), signal });
        if (signal?.aborted) return;
        setMachines((rows) => [...rows.filter((row) => row.id !== value.id), value]);
        setEditor({ kind: "machines", existing: value, value: machineDraft(value) });
      } else {
        const value = await requestData<Card>(path, onSessionExpired, { ...jsonRequest(method, { ...cardPayload(editor.value), ...version }), signal });
        if (signal?.aborted) return;
        setCards((rows) => [...rows.filter((row) => row.id !== value.id), value]);
        setEditor({ kind: "cards", existing: value, value: cardDraft(value) });
      }
      onDirtyChange(false);
      setNotice("档案已保存。");
    } catch (error) {
      if (!signal?.aborted) setError(messageFor(error));
    } finally {
      if (!signal?.aborted) {
        setBusy(false);
        onSavingChange(false);
      }
    }
  }

  return <div className="plan-layout hardware-layout">
    <section className="panel plan-index" aria-labelledby="machines-title">
      <div className="workbench-section-head"><h2 id="machines-title">机台档案</h2>
        <button type="button" className="secondary-action" disabled={busy || loading} onClick={() => open({ kind: "machines", value: machineDraft(emptyMachine) })}>新增机台</button>
      </div>
      {loading && <p role="status">正在读取档案…</p>}
      {!loading && !machines.length && <p className="workbench-empty">还没有机台。</p>}
      <ul className="plan-items">{machines.map((machine) => <li key={machine.id}>
        <button type="button" disabled={busy} aria-current={editor?.existing?.id === machine.id} onClick={() => open({ kind: "machines", existing: machine, value: machineDraft(machine) })}>
          <strong>{machine.name}</strong><span>{machine.model} · 配置版本 {machine.revision}</span>
        </button>
      </li>)}</ul>
      <div className="workbench-section-head plan-item-heading"><h2>探针卡</h2>
        <button type="button" className="secondary-action" disabled={busy || loading || machines.length === 0} onClick={() => open({ kind: "cards", value: emptyCard })}>新增探针卡</button>
      </div>
      {!loading && !cards.length && <p className="workbench-empty">{machines.length ? "还没有探针卡。" : "先登记机台，再登记探针卡。"}</p>}
      <ul className="plan-items">{cards.map((card) => <li key={card.id}>
        <button type="button" disabled={busy} aria-current={editor?.existing?.id === card.id} onClick={() => open({
          kind: "cards", existing: card, value: cardDraft(card),
        })}><strong>{card.name}</strong><span>{card.version} · {card.needles.length} 根卡针</span></button>
      </li>)}</ul>
    </section>
    <section className="panel plan-editor" aria-labelledby="hardware-editor-title">
      <div className="workbench-section-head"><h2 id="hardware-editor-title">{editor
        ? editor.kind === "machines" ? "机台配置" : "探针卡接线" : "设备档案"}</h2></div>
      {error && <p className="workbench-alert" role="alert">{error} {!editor && !loading && <button className="secondary-action"
        type="button" onClick={() => { setError(""); setAttempt((count) => count + 1); }}>重试</button>}</p>}
      {notice && <p className="workbench-notice" role="status">{notice}</p>}
      {editor ? <form onSubmit={save}>
        <fieldset disabled={busy}>
          {editor.kind === "machines" ? <MachineEditor value={editor.value} onChange={(value) => update({ ...editor, value })} />
            : <CardEditor value={editor.value} machines={machines} onChange={(value) => update({ ...editor, value })} />}
        </fieldset>
        <div className="workbench-actions">
          <button type="submit" className="primary-action" disabled={busy}>{busy ? "正在保存…" : "保存档案"}</button>
          <button type="button" className="secondary-action" disabled={busy} onClick={() => open(null)}>关闭</button>
        </div>
      </form> : <p className="workbench-empty">选择机台或探针卡查看档案。</p>}
    </section>
  </div>;
}

function MachineEditor({ value, onChange }: { value: MachineDraft; onChange: (next: MachineDraft) => void }) {
  function editModule(index: number, module: ModuleDraft) {
    onChange({ ...value, modules: value.modules.map((entry, position) => position === index ? module : entry) });
  }
  return <>
    <div className="workbench-fields">{machineFields.map(([field, label], index) => <label key={field}>{label}
      <input value={value[field]} required={index < 6} maxLength={80} onChange={(event) => onChange({ ...value, [field]: event.target.value })} />
    </label>)}</div>
    <div className="workbench-section-head plan-item-heading"><h3>实装模块</h3>
      <button type="button" className="secondary-action" disabled={value.modules.length >= 32} onClick={() =>
        onChange({ ...value, modules: [...value.modules, { key: crypto.randomUUID(), slot: "", model: "", channels: [] }] })}>添加模块</button>
    </div>
    {value.modules.map((module, index) => <fieldset className="plan-item" key={module.key}>
      <legend>{module.model || "模块"}{module.slot && ` · 槽位 ${module.slot}`}</legend>
      <div className="workbench-fields">
        <label>槽位<input required maxLength={80} value={module.slot} onChange={(event) => editModule(index, { ...module, slot: event.target.value })} /></label>
        <label>模块型号<input required maxLength={80} value={module.model} onChange={(event) => editModule(index, { ...module, model: event.target.value })} /></label>
      </div>
      {module.channels.map((channel, channelIndex) => <ChannelEditor key={channel.id} value={channel}
        onChange={(next) => editModule(index, { ...module, channels: module.channels.map((entry, position) => channelIndex === position ? next : entry) })}
        onRemove={() => editModule(index, { ...module, channels: module.channels.filter((entry) => entry.id !== channel.id) })} />)}
      <div className="item-actions">
        <button type="button" disabled={module.channels.length >= 24} onClick={() => editModule(index, { ...module, channels: [...module.channels, {
          id: crypto.randomUUID(), channel: "", port: "", path: "DC", pins: "", modes: [],
          max_voltage_v: "", max_current_a: "", min_current_range_a: "", kelvin: false, shared_group: "",
        }] })}>添加通道路径</button>
        <button type="button" onClick={() => onChange({ ...value, modules: value.modules.filter((entry) => entry.key !== module.key) })}>移除模块</button>
      </div>
    </fieldset>)}
  </>;
}

function ChannelEditor({ value, onChange, onRemove }: { value: ChannelDraft; onChange: (next: ChannelDraft) => void; onRemove: () => void }) {
  return <fieldset className="plan-item">
    <legend>{value.port || "通道路径"}</legend>
    <div className="workbench-fields">
      <label>模块通道<input required maxLength={80} value={value.channel} onChange={(event) => onChange({ ...value, channel: event.target.value })} /></label>
      <label>逻辑端口<input required maxLength={80} value={value.port} onChange={(event) => onChange({ ...value, port: event.target.value })} /></label>
      <label>路径<select value={value.path} onChange={(event) => onChange({ ...value, path: event.target.value as RoutePath })}>{paths.map((path) => <option key={path}>{path}</option>)}</select></label>
      {value.path !== "Chuck" && <label>可达针位<input required maxLength={180} placeholder="1-24, 30" value={value.pins}
        onChange={(event) => onChange({ ...value, pins: event.target.value })} /></label>}
      {numberFields.map(([field, label]) => <label key={field}>{label}<input value={value[field]} maxLength={40}
        onChange={(event) => onChange({ ...value, [field]: event.target.value })} /></label>)}
      <label>互斥资源组<input value={value.shared_group} maxLength={80} onChange={(event) => onChange({ ...value, shared_group: event.target.value })} /></label>
    </div>
    <div className="test-pin-options" role="group" aria-label="通道测量能力">
      {modeEntries.map(([mode, label]) => <label key={mode}><input type="checkbox" checked={value.modes.includes(mode)}
        onChange={(event) => onChange({ ...value, modes: event.target.checked ? [...value.modes, mode] : value.modes.filter((entry) => entry !== mode) })} />{label}</label>)}
      <label><input type="checkbox" checked={value.kelvin} onChange={(event) => onChange({ ...value, kelvin: event.target.checked })} />四线</label>
    </div>
    <div className="item-actions"><button type="button" onClick={onRemove}>移除通道路径</button></div>
  </fieldset>;
}

function CardEditor({ value, machines, onChange }: { value: CardDraft; machines: Machine[]; onChange: (next: CardDraft) => void }) {
  return <>
    <div className="workbench-fields">
      <label>探针卡编号<input required maxLength={80} value={value.name} onChange={(event) => onChange({ ...value, name: event.target.value })} /></label>
      <label>探针卡版本<input required maxLength={80} value={value.version} onChange={(event) => onChange({ ...value, version: event.target.value })} /></label>
      <label className="full-field">接线依据<input required maxLength={80} value={value.source} onChange={(event) => onChange({ ...value, source: event.target.value })} /></label>
    </div>
    <div className="test-pin-options" role="group" aria-label="已确认可安装的机台配置"><span>已确认可安装的机台配置</span>
      {machines.map((machine) => <label key={machine.id}><input type="checkbox" checked={value.machines.some((row) => sameProfile(row, machine))}
        onChange={(event) => {
          const others = value.machines.filter((row) => row.id !== machine.id);
          onChange({ ...value, machines: event.target.checked ? [...others, profileRef(machine)] : others });
        }} />{machine.name} · 版本 {machine.revision}
        {value.machines.some((row) => row.id === machine.id && !sameProfile(row, machine)) && "（待重新确认）"}
      </label>)}
    </div>
    <div className="workbench-section-head plan-item-heading"><h3>探针卡针位</h3><button type="button" className="secondary-action"
      disabled={value.needles.length >= 192} onClick={() => onChange({ ...value, needles: [...value.needles, {
        id: crypto.randomUUID(), site: "", needle: "", pin: "", specs_pad: "", conductors: [],
      }] })}>添加卡针</button></div>
    {value.needles.map((needle) => {
      const update = (changes: Partial<typeof needle>) => onChange({ ...value, needles: value.needles.map((row) => row.id === needle.id ? { ...row, ...changes } : row) });
      return <fieldset className="plan-item" key={needle.id}><legend>{needle.needle || "卡针"}</legend>
        <div className="workbench-fields">
          <label>Site<input required maxLength={80} value={needle.site} onChange={(event) => update({ site: event.target.value })} /></label>
          <label>卡针编号<input required maxLength={80} value={needle.needle} onChange={(event) => update({ needle: event.target.value })} /></label>
          <label>测试头针位<input required maxLength={5} value={needle.pin} onChange={(event) => update({ pin: event.target.value })} /></label>
          <label>SPECS Pad Number<input inputMode="numeric" maxLength={10} value={needle.specs_pad} onChange={(event) => update({ specs_pad: event.target.value })} /></label>
        </div>
        <div className="test-pin-options" role="group" aria-label="卡针导体">{conductors.map((conductor) => <label key={conductor}>
          <input type="checkbox" checked={needle.conductors.includes(conductor)} onChange={(event) => update({
            conductors: event.target.checked ? [...needle.conductors, conductor] : needle.conductors.filter((row) => row !== conductor),
          })} />{conductorLabels[conductor]}</label>)}</div>
        <div className="item-actions"><button type="button" onClick={() => onChange({ ...value, needles: value.needles.filter((row) => row.id !== needle.id) })}>移除卡针</button></div>
      </fieldset>;
    })}
  </>;
}
