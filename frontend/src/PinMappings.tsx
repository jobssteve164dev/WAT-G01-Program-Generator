import type { PlanPinMapping, PlanPinSetup } from "./types";

const mappingFields = [
  ["terminal", "端子名称"],
  ["wafer_pad", "晶圆焊盘"],
  ["probe_pin", "探针卡针位"],
  ["tester_pin", "机台针位"],
  ["tester_port", "机台端口"],
] as const;

export default function PinMappings({ setup, disabled, archived, onChange, onRemove }: {
  setup: PlanPinSetup;
  disabled: boolean;
  archived: boolean;
  onChange: (setup: PlanPinSetup) => void;
  onRemove: (id: string) => void;
}) {
  function addPin() {
    const mapping: PlanPinMapping = {
      id: crypto.randomUUID(), terminal: "", wafer_pad: "", probe_pin: "", tester_pin: "", tester_port: "",
    };
    onChange({ ...setup, mappings: [...setup.mappings, mapping] });
  }

  return (
    <section aria-labelledby="plan-pins-title">
      <div className="workbench-section-head plan-item-heading">
        <h3 id="plan-pins-title">针脚与映射</h3>
        {!archived && <button type="button" className="secondary-action" disabled={disabled || setup.mappings.length >= 64} onClick={addPin}>添加针脚</button>}
      </div>
      <fieldset className="workbench-fields" disabled={disabled || archived} aria-label="探针卡和机台">
        <label>探针卡<input value={setup.probe_card} maxLength={80} onChange={(event) => onChange({ ...setup, probe_card: event.target.value })} /></label>
        <label>机台<input value={setup.tester} maxLength={80} onChange={(event) => onChange({ ...setup, tester: event.target.value })} /></label>
      </fieldset>
      {!setup.mappings.length && <p className="workbench-empty">添加针脚后，可以在测项中选用。</p>}
      {setup.mappings.map((pin, index) => (
        <fieldset className="plan-item" key={pin.id} disabled={disabled || archived}>
          <legend>{pin.terminal.trim() || `针脚 ${index + 1}`}</legend>
          <div className="workbench-fields pin-mapping-fields">
            {mappingFields.map(([key, label]) => (
              <label key={key}>{label}<input value={pin[key]} required={key === "terminal"} maxLength={80} onChange={(event) => {
                const value = event.target.value;
                onChange({ ...setup, mappings: setup.mappings.map((entry) => entry.id === pin.id ? { ...entry, [key]: value } : entry) });
              }} /></label>
            ))}
          </div>
          {!archived && <div className="item-actions"><button type="button" onClick={() => onRemove(pin.id)}>移除针脚</button></div>}
        </fieldset>
      ))}
    </section>
  );
}
