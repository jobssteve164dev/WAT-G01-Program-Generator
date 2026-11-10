import { useEffect, useMemo, useRef, useState } from "react";

import type {
  CompileRequest,
  CompileResult,
  TestSelection,
  ValidationFinding,
} from "./types";
import "./styles.css";


const PARAMETER_LABELS: Record<string, string> = {
  drain_current_a: "漏极电流 (A)",
  gate_start_v: "栅压起点 (V)",
  gate_stop_v: "栅压终点 (V)",
  gate_step_v: "栅压步进 (V)",
  drain_start_v: "漏压起点 (V)",
  drain_stop_v: "漏压终点 (V)",
  drain_step_v: "漏压步进 (V)",
  target_current_a: "判定电流 (A)",
  compliance_a: "限流值 (A)",
  drain_voltage_v: "漏极电压 (V)",
  gate_voltage_v: "栅极电压 (V)",
  measurement_range_a: "测量量程 (A)",
};

const LIMIT_LABELS: Record<string, string> = {
  minimum_v: "下限 (V)",
  maximum_v: "上限 (V)",
  maximum_a: "上限 (A)",
  maximum_ohm: "上限 (Ω)",
};

const TEST_NAMES: Record<string, string> = {
  vgs_threshold: "栅极阈值电压",
  drain_breakdown: "漏源击穿电压",
  drain_leakage: "零栅压漏极漏电",
  gate_leakage: "栅源漏电",
  rds_on_package: "封装导通电阻",
};

type ResultTab = "simulation" | "program" | "files";


function formatValue(value: number): string {
  if (value !== 0 && Math.abs(value) < 0.001) return value.toExponential(3);
  return Number(value.toPrecision(6)).toString();
}


function updateTest(
  request: CompileRequest,
  index: number,
  update: (test: TestSelection) => TestSelection,
): CompileRequest {
  return {
    ...request,
    tests: request.tests.map((test, testIndex) => testIndex === index ? update(test) : test),
  };
}


function ThresholdChart({ result }: { result: CompileResult }) {
  const curve = result.replay.curves.find((item) => item.primitive_id === "vgs_threshold");
  if (!curve || curve.points.length < 2) return null;

  const width = 680;
  const height = 240;
  const pad = { left: 62, right: 22, top: 20, bottom: 42 };
  const xMin = Math.min(...curve.points.map((point) => point.x));
  const xMax = Math.max(...curve.points.map((point) => point.x));
  const logs = curve.points.map((point) => Math.log10(Math.max(point.y, 1e-12)));
  const yMin = Math.min(...logs);
  const yMax = Math.max(...logs);
  const xAt = (x: number) => pad.left + ((x - xMin) / (xMax - xMin)) * (width - pad.left - pad.right);
  const yAt = (y: number) => pad.top + ((yMax - Math.log10(Math.max(y, 1e-12))) / (yMax - yMin)) * (height - pad.top - pad.bottom);
  const points = curve.points.map((point) => `${xAt(point.x)},${yAt(point.y)}`).join(" ");

  return (
    <figure className="chart-card">
      <figcaption>
        <strong>VGS–ID 公开典型值回放</strong>
        <span>对数电流轴，用于检查扫描窗口和限值</span>
      </figcaption>
      <svg viewBox={`0 0 ${width} ${height}`} role="img" aria-label="栅极电压与漏极电流公开典型值回放曲线">
        <line className="chart-axis" x1={pad.left} y1={pad.top} x2={pad.left} y2={height - pad.bottom} />
        <line className="chart-axis" x1={pad.left} y1={height - pad.bottom} x2={width - pad.right} y2={height - pad.bottom} />
        {[0, 1, 2, 3, 4, 5].map((tick) => (
          <g key={tick}>
            <line className="chart-grid" x1={xAt(tick)} y1={pad.top} x2={xAt(tick)} y2={height - pad.bottom} />
            <text x={xAt(tick)} y={height - 16} textAnchor="middle">{tick}</text>
          </g>
        ))}
        {[-9, -6, -3, 0].map((power) => (
          <g key={power}>
            <line className="chart-grid" x1={pad.left} y1={yAt(10 ** power)} x2={width - pad.right} y2={yAt(10 ** power)} />
            <text x={pad.left - 10} y={yAt(10 ** power) + 4} textAnchor="end">10^{power}</text>
          </g>
        ))}
        <polyline className="chart-line" points={points} />
        <circle className="chart-point" cx={xAt(3)} cy={yAt(1e-3)} r="5" />
        <text className="chart-label" x={xAt(3) + 10} y={yAt(1e-3) - 10}>Vth = 3.0 V</text>
        <text className="chart-axis-label" x={(pad.left + width - pad.right) / 2} y={height - 2} textAnchor="middle">VGS (V)</text>
        <text className="chart-axis-label" x="14" y={height / 2} transform={`rotate(-90 14 ${height / 2})`} textAnchor="middle">ID (A)</text>
      </svg>
    </figure>
  );
}


function FindingList({ findings }: { findings: ValidationFinding[] }) {
  return (
    <ul className="finding-list">
      {findings.map((finding, index) => (
        <li key={`${finding.code}-${index}`} className={`finding finding-${finding.severity}`}>
          <span>{finding.severity === "error" ? "需修正" : finding.severity === "warning" ? "边界提醒" : "说明"}</span>
          <p>
            {finding.primitive_id && TEST_NAMES[finding.primitive_id] ? (
              <a href={`#primitive-${finding.primitive_id}`}>{TEST_NAMES[finding.primitive_id]}：</a>
            ) : null}
            {finding.message}
          </p>
        </li>
      ))}
    </ul>
  );
}


export default function App() {
  const [request, setRequest] = useState<CompileRequest | null>(null);
  const [result, setResult] = useState<CompileResult | null>(null);
  const [loadError, setLoadError] = useState("");
  const [findings, setFindings] = useState<ValidationFinding[]>([]);
  const [isCompiling, setIsCompiling] = useState(false);
  const [activeTab, setActiveTab] = useState<ResultTab>("simulation");
  const errorRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    let active = true;
    fetch("/api/example")
      .then(async (response) => {
        if (!response.ok) throw new Error("示例规格加载失败，请检查服务是否已启动。");
        return response.json() as Promise<CompileRequest>;
      })
      .then((value) => {
        if (active) setRequest(value);
      })
      .catch((error: unknown) => {
        if (active) setLoadError(error instanceof Error ? error.message : "示例规格加载失败");
      });
    return () => {
      active = false;
    };
  }, []);

  useEffect(() => {
    if (findings.some((finding) => finding.severity === "error")) errorRef.current?.focus();
  }, [findings]);

  const selectedCount = request?.tests.filter((test) => test.enabled).length ?? 0;
  const programFile = result?.files.find((file) => file.path.endsWith(".tis.c"));
  const downloadHref = useMemo(
    () => result ? `data:application/zip;base64,${result.package_base64}` : "",
    [result],
  );

  function changeEnabled(index: number, enabled: boolean) {
    if (!request) return;
    setRequest(updateTest(request, index, (test) => ({ ...test, enabled })));
    setResult(null);
  }

  function changeNumber(index: number, group: "parameters" | "limits", key: string, value: string) {
    if (!request) return;
    setRequest(updateTest(request, index, (test) => ({
      ...test,
      [group]: { ...test[group], [key]: Number(value) },
    })));
    setResult(null);
  }

  async function compile() {
    if (!request) return;
    setIsCompiling(true);
    setFindings([]);
    setResult(null);
    try {
      const response = await fetch("/api/compile", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(request),
      });
      const payload = await response.json() as CompileResult | { findings?: ValidationFinding[] };
      if (!response.ok) {
        const validationFindings = "findings" in payload ? payload.findings ?? [] : [];
        setFindings(validationFindings.length ? validationFindings : [{
          severity: "error",
          code: "GENERATION_FAILED",
          message: "生成失败，请检查输入后重试。",
          primitive_id: null,
        }]);
        return;
      }
      const compileResult = payload as CompileResult;
      setResult(compileResult);
      setFindings(compileResult.findings);
      setActiveTab("simulation");
      window.setTimeout(() => document.getElementById("result-title")?.scrollIntoView?.({ behavior: "smooth", block: "start" }), 0);
    } catch {
      setFindings([{
        severity: "error",
        code: "SERVICE_UNAVAILABLE",
        message: "无法连接生成服务，请确认服务已启动后重试。",
        primitive_id: null,
      }]);
    } finally {
      setIsCompiling(false);
    }
  }

  return (
    <main className="app-shell">
      <header className="topbar">
        <div className="brand-mark" aria-hidden="true">W</div>
        <div>
          <p className="eyebrow">4080 参考流程 · G‑01</p>
          <h1>WAT 程序生成工作台</h1>
        </div>
        <span className="environment-badge">本地工程回放模式</span>
      </header>

      {loadError ? <div className="error-summary" role="alert">{loadError}</div> : null}
      {!request && !loadError ? <div className="loading-panel" role="status">正在加载公开样例…</div> : null}

      {request ? (
        <>
          <section className="product-strip" aria-labelledby="product-title">
            <div>
              <p className="eyebrow">当前器件</p>
              <h2 id="product-title">{request.product.model}</h2>
              <p>{request.product.manufacturer} · {request.product.qualification} · {request.product.description}</p>
            </div>
            <div className="product-actions">
              <span className="voltage-chip">{request.product.voltage_class_v} V</span>
              <a href={request.product.datasheet_url} target="_blank" rel="noreferrer">查看原厂数据手册</a>
            </div>
          </section>

          <aside className="boundary-note">
            <strong>使用边界</strong>
            <p>本例用公开封装器件规格跑通生成流程，不把封装数据手册限值当作晶圆 WAT 量产限值。</p>
          </aside>

          <div className="workspace-grid">
            <section className="panel test-panel" aria-labelledby="tests-title">
              <div className="section-heading">
                <div>
                  <p className="step-label">步骤 1</p>
                  <h2 id="tests-title">选择并确认测试条件</h2>
                </div>
                <span>{selectedCount} 项已启用</span>
              </div>

              <div className="test-list">
                {request.tests.map((test, index) => {
                  const titleId = `test-${test.primitive_id}`;
                  const locked = Boolean(test.blocked_reason);
                  return (
                    <article id={`primitive-${test.primitive_id}`} className={`test-card ${test.enabled ? "test-card-active" : ""} ${locked ? "test-card-locked" : ""}`} key={test.primitive_id}>
                      <div className="test-card-header">
                        <label className="test-toggle" htmlFor={`enabled-${test.primitive_id}`}>
                          <input
                            id={`enabled-${test.primitive_id}`}
                            type="checkbox"
                            checked={test.enabled}
                            disabled={locked}
                            onChange={(event) => changeEnabled(index, event.target.checked)}
                          />
                          <span aria-hidden="true" />
                        </label>
                        <div>
                          <h3 id={titleId}>{test.name}</h3>
                          <p className="test-cn-name">{TEST_NAMES[test.primitive_id]}</p>
                        </div>
                        <span className={`status-chip ${locked ? "status-blocked" : test.enabled ? "status-active" : "status-idle"}`}>
                          {locked ? "超出能力" : test.enabled ? "参与生成" : "未启用"}
                        </span>
                      </div>
                      <p className="condition-line">公开规格条件：{test.source_condition}</p>
                      {test.blocked_reason ? <p className="blocked-reason">{test.blocked_reason}</p> : null}

                      {!locked ? (
                        <div className="field-groups" aria-labelledby={titleId}>
                          <fieldset disabled={!test.enabled}>
                            <legend>激励与测量</legend>
                            <div className="field-grid">
                              {Object.entries(test.parameters).map(([key, value]) => (
                                <label key={key}>
                                  <span>{PARAMETER_LABELS[key] ?? key}</span>
                                  <input type="number" step="any" value={value} onChange={(event) => changeNumber(index, "parameters", key, event.target.value)} />
                                </label>
                              ))}
                            </div>
                          </fieldset>
                          <fieldset disabled={!test.enabled}>
                            <legend>判定限值</legend>
                            <div className="field-grid limits-grid">
                              {Object.entries(test.limits).map(([key, value]) => (
                                <label key={key}>
                                  <span>{LIMIT_LABELS[key] ?? key}</span>
                                  <input type="number" step="any" value={value} onChange={(event) => changeNumber(index, "limits", key, event.target.value)} />
                                </label>
                              ))}
                            </div>
                          </fieldset>
                        </div>
                      ) : null}
                    </article>
                  );
                })}
              </div>
            </section>

            <aside className="panel generation-panel" aria-labelledby="generate-title">
              <p className="step-label">步骤 2</p>
              <h2 id="generate-title">校验并生成</h2>
              <dl className="summary-list">
                <div><dt>器件</dt><dd>当前公开样例</dd></div>
                <div><dt>测试项</dt><dd>{selectedCount} 项</dd></div>
                <div><dt>参考能力</dt><dd>{request.capability.max_voltage_v} V / {request.capability.max_current_a} A</dd></div>
                <div><dt>机台资源</dt><dd>待现场确认</dd></div>
              </dl>
              <button className="primary-action" type="button" disabled={isCompiling} onClick={compile}>
                {isCompiling ? "正在校验与生成…" : "校验并生成程序包"}
              </button>
              <p className="action-help">生成前会检查电压、电流、限流值和测试能力边界。</p>
            </aside>
          </div>

          {findings.some((finding) => finding.severity === "error") ? (
            <div className="error-summary" role="alert" tabIndex={-1} ref={errorRef}>
              <h2>生成已停止，请先修正以下条件</h2>
              <FindingList findings={findings.filter((finding) => finding.severity === "error")} />
            </div>
          ) : null}

          {result ? (
            <section className="result-section" aria-labelledby="result-title">
              <div className={`result-hero ${result.status === "REPLAY_LIMIT_FAILED" ? "result-hero-failed" : ""}`}>
                <div className={`success-mark ${result.status === "REPLAY_LIMIT_FAILED" ? "result-mark-failed" : ""}`} aria-hidden="true">
                  {result.status === "REPLAY_LIMIT_FAILED" ? "!" : "✓"}
                </div>
                <div>
                  <p className="step-label">步骤 3</p>
                  <h2 id="result-title">
                    {result.status === "REPLAY_LIMIT_FAILED"
                      ? "典型值回放完成，存在未通过项"
                      : "公开典型值回放完成，程序包已生成"}
                  </h2>
                  <p>
                    {result.status === "REPLAY_LIMIT_FAILED"
                      ? "程序包已生成供定位问题，请先复核未通过限值。"
                      : "可立即下载并参与评审；当前不会误导为已具备机台执行资格。"}
                  </p>
                </div>
                <span className="deployment-lock">机台部署锁定</span>
              </div>

              <div className="result-actions">
                <a className="download-action" href={downloadHref} download={`${request.product.model}-g01-reference.zip`}>
                  下载参考程序包
                </a>
                <code title={result.artifact_hash}>版本 {result.artifact_hash.slice(0, 12)}</code>
              </div>

              <div className="tab-list" role="tablist" aria-label="生成结果">
                {([
                  ["simulation", "典型值回放"],
                  ["program", "程序预览"],
                  ["files", `文件清单 (${result.files.length})`],
                ] as Array<[ResultTab, string]>).map(([tab, label]) => (
                  <button key={tab} type="button" role="tab" aria-selected={activeTab === tab} onClick={() => setActiveTab(tab)}>{label}</button>
                ))}
              </div>

              {activeTab === "simulation" ? (
                <div className="tab-panel" role="tabpanel">
                  <div className="metric-grid">
                    {result.replay.results.map((item) => (
                      <article className="metric-card" key={item.primitive_id}>
                        <span className={item.passed ? "pass-dot" : "fail-dot"}>{item.passed ? "通过" : "未通过"}</span>
                        <p>{TEST_NAMES[item.primitive_id] ?? item.name}</p>
                        <strong>{formatValue(item.value)} {item.unit}</strong>
                      </article>
                    ))}
                  </div>
                  <ThresholdChart result={result} />
                  <FindingList findings={findings.filter((finding) => finding.severity !== "error")} />
                </div>
              ) : null}

              {activeTab === "program" ? (
                <div className="tab-panel" role="tabpanel">
                  <div className="preview-heading">
                    <div><strong>reference_algorithm.tis.c</strong><span>4080/TIS 风格评审稿</span></div>
                    <span className="deployment-lock">禁止直接上机</span>
                  </div>
                  <pre className="code-preview"><code>{programFile?.content ?? "程序文件未生成"}</code></pre>
                </div>
              ) : null}

              {activeTab === "files" ? (
                <div className="tab-panel" role="tabpanel">
                  <table className="file-table">
                    <thead><tr><th>文件</th><th>类型</th><th>SHA-256</th></tr></thead>
                    <tbody>
                      {result.files.map((file) => (
                        <tr key={file.path}>
                          <td>{file.path}</td>
                          <td>{file.media_type}</td>
                          <td><code title={file.sha256}>{file.sha256.slice(0, 12)}</code></td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              ) : null}
            </section>
          ) : null}
        </>
      ) : null}
    </main>
  );
}
