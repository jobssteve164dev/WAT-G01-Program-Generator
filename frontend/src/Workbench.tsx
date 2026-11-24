import { useEffect, useRef, useState } from "react";
import type { FormEvent, MouseEvent, ReactNode } from "react";

import PinMappings from "./PinMappings";
import PlanHardware from "./PlanHardware";
import type { Plan, PlanItem, PlanPinSetup, PlanSummary, Product, ProductDetails } from "./types";
import { downloadFile, jsonRequest, messageFor, requestData, RequestError } from "./workbench-api";


interface DraftItem extends Omit<PlanItem, "lower_limit" | "upper_limit"> {
  key: number;
  lower_limit: string;
  upper_limit: string;
}

interface DraftPlan {
  createId: string;
  saved?: Plan;
  name: string;
  description: string;
  tests: DraftItem[];
  pin_setup: PlanPinSetup;
}

const emptyPinSetup: PlanPinSetup = { probe_card: "", tester: "", mappings: [] };

function summaryOf({ id, product_id, name, revision, updated_at, archived, test_count }: Plan): PlanSummary {
  return { id, product_id, name, revision, updated_at, archived, test_count };
}

function parseLimit(text: string): number | null {
  const value = text.trim();
  if (!value) return null;
  if (!/^[+-]?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?$/.test(value) || !Number.isFinite(Number(value))) {
    throw new RequestError("请填写有效的数值限值。支持小数和科学计数法。");
  }
  return Number(value);
}

const dateFormat = new Intl.DateTimeFormat("zh-CN", { dateStyle: "medium", timeStyle: "short" });

function ProductPlans({ product, onSessionExpired, onDirtyChange, allowLeave, hasUnsavedChanges, isSaving, onSavingChange }: {
  product: Product;
  onSessionExpired: () => void;
  onDirtyChange: (dirty: boolean) => void;
  allowLeave: () => boolean;
  hasUnsavedChanges: boolean;
  isSaving: boolean;
  onSavingChange: (saving: boolean) => void;
}) {
  const [showArchived, setShowArchived] = useState(false);
  const [plans, setPlans] = useState<PlanSummary[] | null>(null);
  const [draft, setDraft] = useState<DraftPlan | null>(null);
  const [loadVersion, setLoadVersion] = useState(0);
  const [loadingError, setLoadingError] = useState("");
  const [actionError, setActionError] = useState("");
  const [notice, setNotice] = useState("");
  const [busy, setBusy] = useState(false);
  const [hardwareOpen, setHardwareOpen] = useState(false);
  const nextKey = useRef(0);
  const operations = useRef<AbortController | null>(null);
  const listRequest = useRef<AbortController | null>(null);
  const planName = useRef<HTMLInputElement>(null);
  const endpoint = `/api/products/${product.id}/plans`;

  useEffect(() => {
    const controller = new AbortController();
    operations.current = controller;
    setBusy(false);
    setActionError("");
    return () => controller.abort();
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    listRequest.current = controller;
    requestData<PlanSummary[]>(`${endpoint}?archived=${showArchived}`, onSessionExpired, { signal: controller.signal })
      .then((value) => {
        if (!controller.signal.aborted) {
          setLoadingError("");
          setPlans(value);
        }
      })
      .catch((error: unknown) => { if (!controller.signal.aborted) setLoadingError(messageFor(error)); });
    return () => controller.abort();
  }, [endpoint, loadVersion, onSessionExpired, showArchived]);

  const isNewDraft = Boolean(draft && !draft.saved);
  useEffect(() => {
    if (isNewDraft) planName.current?.focus();
  }, [isNewDraft]);

  function resetFeedback() {
    setActionError("");
    setNotice("");
  }

  function acceptPlan(plan: Plan) {
    setHardwareOpen(false);
    setDraft({
      createId: plan.id,
      saved: plan,
      name: plan.name,
      description: plan.description,
      pin_setup: plan.pin_setup ?? emptyPinSetup,
      tests: plan.tests.map((item) => ({
        ...item,
        key: nextKey.current++,
        lower_limit: item.lower_limit?.toString() ?? "",
        upper_limit: item.upper_limit?.toString() ?? "",
      })),
    });
    onDirtyChange(false);
  }

  function selectArchive(archived: boolean) {
    setLoadingError("");
    setPlans(null);
    setShowArchived(archived);
  }

  function startDraft(copy?: DraftPlan) {
    if (!allowLeave()) return;
    resetFeedback();
    setHardwareOpen(false);
    const createId = crypto.randomUUID();
    setDraft(copy ? { ...copy, createId, saved: undefined, name: `${copy.name.slice(0, 70)}（副本）` }
      : { createId, name: "", description: "", tests: [], pin_setup: emptyPinSetup });
    onDirtyChange(Boolean(copy));
  }

  function editDraft(update: Partial<DraftPlan>) {
    setDraft((value) => value && { ...value, ...update });
    setNotice("");
    onDirtyChange(true);
  }

  async function openPlan(id: string) {
    if (!allowLeave()) return;
    const signal = operations.current?.signal;
    resetFeedback();
    setBusy(true);
    try {
      const plan = await requestData<Plan>(`/api/plans/${id}`, onSessionExpired, { signal });
      if (!signal?.aborted) acceptPlan(plan);
    } catch (error) {
      if (!signal?.aborted) setActionError(messageFor(error));
    } finally {
      if (!signal?.aborted) setBusy(false);
    }
  }

  async function savePlan(archived: boolean) {
    if (!draft || busy || isSaving) return;
    const signal = operations.current?.signal;
    resetFeedback();
    setBusy(true);
    onSavingChange(true);
    try {
      if (!draft.name.trim()) throw new RequestError("请输入计划名称。");
      const tests = draft.tests.map(({ name, conditions, unit, lower_limit, upper_limit, pin_ids }, index) => {
        if (!name.trim()) throw new RequestError(`请输入第 ${index + 1} 项的测项名称。`);
        const lower = parseLimit(lower_limit);
        const upper = parseLimit(upper_limit);
        if (lower !== null && upper !== null && lower > upper) {
          throw new RequestError(`第 ${index + 1} 项的下限不能大于上限。`);
        }
        return { name, conditions, unit, lower_limit: lower, upper_limit: upper, pin_ids };
      });
      const payload = { name: draft.name, description: draft.description, tests, pin_setup: draft.pin_setup };
      const saved = draft.saved;
      const path = saved ? `/api/plans/${saved.id}` : endpoint;
      const plan = await requestData<Plan>(path, onSessionExpired, {
        ...jsonRequest(saved ? "PUT" : "POST", saved ? { ...payload, revision: saved.revision, archived } : { ...payload, id: draft.createId }), signal,
      });
      if (signal?.aborted) return;
      listRequest.current?.abort();
      acceptPlan(plan);
      if (plan.archived !== showArchived) selectArchive(plan.archived);
      else {
        setPlans((items) => [summaryOf(plan), ...(items ?? []).filter((item) => item.id !== plan.id)]);
        setLoadVersion((version) => version + 1);
      }
      setNotice(!saved || saved.archived === plan.archived ? "已保存测试计划。" : plan.archived ? "计划已归档。" : "计划已恢复。");
    } catch (error) {
      if (!signal?.aborted) setActionError(messageFor(error));
    } finally {
      if (!signal?.aborted) {
        setBusy(false);
        onSavingChange(false);
      }
    }
  }

  async function downloadPlan(event: MouseEvent<HTMLAnchorElement>) {
    event.preventDefault();
    const path = event.currentTarget.getAttribute("href");
    if (!path) return;
    const signal = operations.current?.signal;
    setActionError("");
    try {
      await downloadFile(path, "wat-test-plan.json", onSessionExpired, { signal });
    } catch (error) {
      if (!signal?.aborted) setActionError(messageFor(error));
    }
  }

  function moveItem(index: number, offset: number) {
    if (!draft) return;
    const items = [...draft.tests];
    const [item] = items.splice(index, 1);
    items.splice(index + offset, 0, item);
    editDraft({ tests: items });
  }

  function itemFields(item: DraftItem, index: number): ReactNode {
    const updateItem = (fields: Partial<DraftItem>) => {
      if (draft) editDraft({ tests: draft.tests.map((entry) => entry.key === item.key ? { ...entry, ...fields } : entry) });
    };
    const change = (key: keyof Omit<DraftItem, "key" | "pin_ids">, value: string) => updateItem({ [key]: value });
    const pinIds = item.pin_ids ?? [];
    const pins = draft?.pin_setup.mappings.filter((pin) => pin.terminal.trim() !== "") ?? [];
    return (
      <fieldset className="plan-item" key={item.key} disabled={busy || draft?.saved?.archived}>
        <legend>测项 {index + 1}</legend>
        <div className="workbench-fields workbench-item-fields">
          <label className="item-name">测项名称<input value={item.name} required maxLength={80} onChange={(event) => change("name", event.target.value)} /></label>
          <label>单位<input value={item.unit} maxLength={24} onChange={(event) => change("unit", event.target.value)} /></label>
          <label>下限<input value={item.lower_limit} maxLength={40} onChange={(event) => change("lower_limit", event.target.value)} /></label>
          <label>上限<input value={item.upper_limit} maxLength={40} onChange={(event) => change("upper_limit", event.target.value)} /></label>
          <label className="full-field">测量条件<textarea aria-label="测量条件" rows={2} value={item.conditions} maxLength={500} onChange={(event) => change("conditions", event.target.value)} /></label>
        </div>
        {pins.length > 0 && <div className="test-pin-options" role="group" aria-label="使用的针脚">
          <span>使用的针脚</span>
          {pins.map((pin) => <label key={pin.id}>
            <input type="checkbox" checked={pinIds.includes(pin.id)} onChange={(event) => updateItem({
              pin_ids: event.target.checked ? [...pinIds, pin.id] : pinIds.filter((id) => id !== pin.id),
            })} />{pin.terminal.trim()}
          </label>)}
        </div>}
        <div className="item-actions">
          <button type="button" disabled={index === 0} onClick={() => moveItem(index, -1)}>上移</button>
          <button type="button" disabled={index === (draft?.tests.length ?? 0) - 1} onClick={() => moveItem(index, 1)}>下移</button>
          <button type="button" onClick={() => {
            if (draft) editDraft({ tests: draft.tests.filter((entry) => entry.key !== item.key) });
          }}>移除测项</button>
        </div>
      </fieldset>
    );
  }

  const saved = draft?.saved;
  return (
    <div className="plan-layout">
      <section className="panel plan-index" aria-labelledby="plan-index-title">
        <div className="workbench-section-head">
          <h2 id="plan-index-title">测试计划</h2>
          <button type="button" className="primary-action" disabled={busy || plans === null} onClick={() => startDraft()}>新建计划</button>
        </div>
        <div className="plan-filters" aria-label="筛选测试计划">
          {[false, true].map((archived) => (
            <button type="button" key={String(archived)} disabled={busy} aria-pressed={showArchived === archived} onClick={() => {
              if (showArchived === archived || !allowLeave()) return;
              resetFeedback();
              onDirtyChange(false);
              setDraft(null);
              selectArchive(archived);
            }}>{archived ? "已归档" : "未归档"}</button>
          ))}
        </div>
        {loadingError ? <div className="workbench-alert" role="alert">
          <p>{loadingError}</p>
          <button className="secondary-action" type="button" onClick={() => { setLoadingError(""); setLoadVersion((v) => v + 1); }}>重试</button>
        </div> : plans === null ? <p className="workbench-empty" role="status">正在加载计划…</p> : plans.length === 0 ? (
          <p className="workbench-empty">{showArchived ? "暂无已归档的计划。" : "这个产品还没有测试计划。"}</p>
        ) : (
          <ul className="plan-items">
            {plans.map((plan) => <li key={plan.id}>
              <button type="button" disabled={busy} aria-current={saved?.id === plan.id ? "true" : undefined} onClick={() => openPlan(plan.id)}>
                <strong>{plan.name}</strong>
                <span>{plan.test_count} 个测项 · {dateFormat.format(new Date(plan.updated_at))}</span>
              </button>
            </li>)}
          </ul>
        )}
      </section>

      <section className="panel plan-editor" aria-labelledby="plan-editor-title">
        <div className="workbench-section-head">
          <h2 id="plan-editor-title">{saved ? <>{saved.name}{saved.archived && <span className="plan-archive-label">已归档</span>}</> : draft ? "新建测试计划" : "计划详情"}</h2>
          {saved && <div className="workbench-actions">
            <button type="button" className="secondary-action" disabled={busy || isSaving || (!hardwareOpen && hasUnsavedChanges)} onClick={() => {
              if (hardwareOpen && !allowLeave()) return;
              setHardwareOpen(!hardwareOpen);
              onDirtyChange(false);
            }}>{hardwareOpen ? "返回计划详情" : "配置机台接线"}</button>
            {!hardwareOpen && <a className="secondary-action plan-export" href={`/api/plans/${saved.id}/export`} download onClick={downloadPlan}>下载已保存计划</a>}
          </div>}
        </div>
        {actionError && <p className="workbench-alert" role="alert">{actionError}</p>}
        {notice && <p className="workbench-notice" role="status">{notice}</p>}
        {hardwareOpen && saved ? <PlanHardware key={`${saved.id}:${saved.revision}`} plan={saved} onSessionExpired={onSessionExpired}
          allowLeave={allowLeave} onDirtyChange={onDirtyChange} onSavingChange={onSavingChange} />
          : draft ? <form onSubmit={(event) => { event.preventDefault(); void savePlan(false); }}>
          <fieldset className="workbench-fields" disabled={busy || saved?.archived}>
            <label>计划名称<input ref={planName} required maxLength={80} value={draft.name} onChange={(event) => editDraft({ name: event.target.value })} /></label>
            <label className="full-field">说明<textarea aria-label="说明" rows={2} value={draft.description} maxLength={500} onChange={(event) => editDraft({ description: event.target.value })} /></label>
          </fieldset>
          <PinMappings setup={draft.pin_setup} disabled={busy} archived={Boolean(saved?.archived)} onChange={(pin_setup) => editDraft({ pin_setup })}
            onRemove={(id) => {
              const referenceCount = draft.tests.filter((item) => item.pin_ids?.includes(id)).length;
              if (referenceCount && !window.confirm(`移除此针脚，同时解除 ${referenceCount} 个测项的引用？`)) return;
              editDraft({
                pin_setup: { ...draft.pin_setup, mappings: draft.pin_setup.mappings.filter((pin) => pin.id !== id) },
                tests: draft.tests.map((item) => item.pin_ids?.includes(id) ? { ...item, pin_ids: item.pin_ids.filter((pin) => pin !== id) } : item),
              });
            }} />
          <div className="workbench-section-head plan-item-heading">
            <h3>测项与限值</h3>
            {!saved?.archived && <button type="button" className="secondary-action" disabled={busy || draft.tests.length >= 64} onClick={() => editDraft({ tests: [...draft.tests, {
              key: nextKey.current++, name: "", conditions: "", unit: "", lower_limit: "", upper_limit: "",
            }] })}>添加测项</button>}
          </div>
          {!draft.tests.length && <p className="workbench-empty">暂无测项。</p>}
          {draft.tests.map(itemFields)}
          <div className="workbench-actions">
            {!saved?.archived && <button type="submit" className="primary-action" disabled={busy}>{busy ? "处理中…" : "保存计划"}</button>}
            {saved && <button type="button" className="secondary-action" disabled={busy || hasUnsavedChanges} onClick={() => startDraft(draft)}>复制为新计划</button>}
            {saved && <button type="button" className="secondary-action" disabled={busy || hasUnsavedChanges} onClick={() => {
              if (!saved.archived && !window.confirm("归档这个计划？之后可以恢复。")) return;
              void savePlan(!saved.archived);
            }}>{saved.archived ? "恢复计划" : "归档计划"}</button>}
            {(!saved || hasUnsavedChanges) && <button type="button" className="secondary-action" disabled={busy} onClick={() => {
              if (!allowLeave()) return;
              resetFeedback();
              if (saved) acceptPlan(saved);
              else { setDraft(null); onDirtyChange(false); }
            }}>{saved ? "撤销未保存修改" : "取消"}</button>}
          </div>
          {saved && <p className="workbench-empty">下载的测试计划供评审使用，尚未验证机台加载。</p>}
        </form> : <p className="workbench-empty">选择已有计划，或新建一份测试计划。</p>}
      </section>
    </div>
  );
}

export default function Workbench({ products, onProductsChange, onSessionExpired, onDirtyChange, allowLeave, hasUnsavedChanges, isSaving, onSavingChange }: {
  products: Product[];
  onProductsChange: (products: Product[]) => void;
  onSessionExpired: () => void;
  onDirtyChange: (dirty: boolean) => void;
  allowLeave: () => boolean;
  hasUnsavedChanges: boolean;
  isSaving: boolean;
  onSavingChange: (saving: boolean) => void;
}) {
  const [selectedId, setSelectedId] = useState("");
  const [productForm, setProductForm] = useState<"new" | "edit" | null>(null);
  const [newProductId, setNewProductId] = useState("");
  const [error, setError] = useState("");
  const operations = useRef<AbortController | null>(null);
  const product = products.find((entry) => entry.id === selectedId) ?? products[0];
  const editingProduct = productForm === "edit" ? product : undefined;

  useEffect(() => {
    const controller = new AbortController();
    operations.current = controller;
    setError("");
    return () => controller.abort();
  }, []);

  function showProductForm(mode: "new" | "edit") {
    if (!allowLeave()) return;
    setError("");
    setProductForm(mode);
    if (mode === "new") setNewProductId(crypto.randomUUID());
    onDirtyChange(false);
  }

  async function saveProduct(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (isSaving) return;
    const fields = new FormData(event.currentTarget);
    const details: ProductDetails = {
      model: String(fields.get("model")).trim(),
      manufacturer: String(fields.get("manufacturer")).trim(),
      description: String(fields.get("description")).trim(),
    };
    if (!details.model) { setError("请输入产品型号。"); return; }
    const signal = operations.current?.signal;
    onSavingChange(true);
    setError("");
    try {
      const record = await requestData<Product>(editingProduct ? `/api/products/${editingProduct.id}` : "/api/products", onSessionExpired, {
        ...jsonRequest(editingProduct ? "PUT" : "POST", editingProduct ? details : { ...details, id: newProductId }), signal,
      });
      if (signal?.aborted) return;
      const next = [...products.filter((entry) => entry.id !== record.id), record].sort((a, b) => a.model.localeCompare(b.model));
      onProductsChange(next);
      setSelectedId(record.id);
      setProductForm(null);
      onDirtyChange(false);
    } catch (problem) {
      if (!signal?.aborted) setError(messageFor(problem));
    } finally {
      if (!signal?.aborted) onSavingChange(false);
    }
  }

  return (
    <>
      <section className="panel product-picker" aria-label="产品管理">
        <div>
          <label htmlFor="workbench-product">产品</label>
          {product ? <select id="workbench-product" value={product.id} disabled={isSaving || productForm !== null} onChange={(event) => {
            if (!allowLeave()) return;
            setSelectedId(event.target.value);
            onDirtyChange(false);
          }}>
            {products.map((entry) => <option key={entry.id} value={entry.id}>{entry.model}</option>)}
          </select> : <p className="workbench-empty">还没有产品。</p>}
          {product && (product.manufacturer || product.description) && <p className="product-detail">{[product.manufacturer, product.description].filter(Boolean).join(" · ")}</p>}
        </div>
        <div className="workbench-actions">
          {product && <button type="button" className="secondary-action" disabled={isSaving || productForm !== null} onClick={() => showProductForm("edit")}>编辑产品</button>}
          <button type="button" className="primary-action" disabled={isSaving || productForm !== null} onClick={() => showProductForm("new")}>新建产品</button>
        </div>
      </section>

      {productForm && <section className="panel product-edit" aria-labelledby="product-editor-title">
        <div className="workbench-section-head"><h2 id="product-editor-title">{editingProduct ? "编辑产品资料" : "新建产品资料"}</h2></div>
        {error && <p className="workbench-alert" role="alert">{error}</p>}
        <form onSubmit={saveProduct} onChange={() => onDirtyChange(true)}>
          <fieldset className="workbench-fields workbench-product-fields" disabled={isSaving}>
            <label>产品型号<input name="model" maxLength={80} defaultValue={editingProduct?.model} required autoFocus /></label>
            <label>厂商<input name="manufacturer" maxLength={80} defaultValue={editingProduct?.manufacturer} /></label>
            <label className="full-field">产品说明<textarea aria-label="产品说明" name="description" rows={2} maxLength={240} defaultValue={editingProduct?.description} /></label>
          </fieldset>
          <div className="workbench-actions">
            <button type="submit" className="primary-action" disabled={isSaving}>{isSaving ? "正在保存…" : "保存产品"}</button>
            <button type="button" className="secondary-action" disabled={isSaving} onClick={() => {
              if (!allowLeave()) return;
              setProductForm(null);
              onDirtyChange(false);
            }}>取消</button>
          </div>
        </form>
      </section>}
      {product && !productForm && <ProductPlans key={product.id} product={product} onSessionExpired={onSessionExpired}
        onDirtyChange={onDirtyChange} allowLeave={allowLeave} hasUnsavedChanges={hasUnsavedChanges} isSaving={isSaving} onSavingChange={onSavingChange} />}
    </>
  );
}
