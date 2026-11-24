import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import App from "./App";


const example = {
  product: {
    manufacturer: "Nexperia",
    model: "BUK7M6R0-40H",
    description: "40 V N-channel Trench 9 automotive MOSFET",
    qualification: "AEC-Q101",
    datasheet_url: "https://assets.nexperia.com/documents/data-sheet/BUK7M6R0-40H.pdf",
    voltage_class_v: 40,
  },
  capability: {
    profile_id: "4080_PUBLIC_REFERENCE_DEMO_PROFILE_V1",
    max_voltage_v: 200,
    max_current_a: 1,
    low_current_floor_a: 1e-9,
    pin_map: {
      drain: "DEMO_DRAIN_RESOURCE",
      gate: "DEMO_GATE_RESOURCE",
      source: "DEMO_SOURCE_RESOURCE",
    },
  },
  tests: [
    {
      primitive_id: "vgs_threshold",
      name: "Gate threshold voltage",
      enabled: true,
      parameters: { drain_current_a: 0.001, gate_start_v: 0, gate_stop_v: 5, gate_step_v: 0.05 },
      limits: { minimum_v: 2.4, maximum_v: 3.6 },
      unit: "V",
      source_condition: "ID=1 mA, VDS=VGS, Tj=25 C",
      blocked_reason: null,
    },
    {
      primitive_id: "rds_on_package",
      name: "Package on-state resistance",
      enabled: false,
      parameters: { gate_voltage_v: 10, drain_current_a: 20 },
      limits: { maximum_ohm: 0.006 },
      unit: "ohm",
      source_condition: "VGS=10 V, ID=20 A, Tj=25 C",
      blocked_reason: "数据手册条件要求 20 A，超过公开 4080 参考能力上限 1 A。",
    },
  ],
  usage_boundary: "PUBLIC_DATASHEET_ENGINEERING_DEMO_NOT_WAT_LIMITS",
  metadata: {},
};

const compileResult = {
  status: "REPLAY_READY",
  deployable_to_tester: false,
  artifact_hash: "a".repeat(64),
  findings: [
    {
      severity: "warning",
      code: "REFERENCE_ONLY",
      message: "生成的源码是可评审参考稿。",
      primitive_id: null,
    },
  ],
  files: [
    {
      path: "program/reference_algorithm.tis.c",
      media_type: "text/x-c",
      content: "/* reference */\ndisconnect_all();\n",
      sha256: "b".repeat(64),
    },
  ],
  replay: {
    results: [
      {
        primitive_id: "vgs_threshold",
        name: "Gate threshold voltage",
        value: 3,
        unit: "V",
        passed: true,
      },
    ],
    curves: [
      {
        primitive_id: "vgs_threshold",
        x_label: "VGS (V)",
        y_label: "ID (A)",
        points: [
          { x: 0, y: 1e-9 },
          { x: 3, y: 1e-3 },
          { x: 5, y: 1 },
        ],
      },
    ],
  },
  package_base64: "ZGVtbw==",
};

async function openExample() {
  fireEvent.click(await screen.findByRole("button", { name: "打开公开示例" }));
  await screen.findByText("BUK7M6R0-40H");
}

describe("WAT pilot", () => {
  beforeEach(() => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (endpoint) => new Response(JSON.stringify(endpoint === "/api/products" ? [] : example), { status: 200 }))
    );
  });

  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
  });

  it("opens the product workbench before any reference example", async () => {
    render(<App />);
    expect(await screen.findByRole("button", { name: "新建产品" })).toBeEnabled();
    expect(screen.getByRole("button", { name: "打开公开示例" })).toBeEnabled();
    expect(screen.queryByText("BUK7M6R0-40H")).not.toBeInTheDocument();
  });

  it("opens the installed hardware directory and returns to plans", async () => {
    vi.mocked(fetch).mockImplementation(async (endpoint) => Response.json(endpoint === "/api/hardware/machines" ? [{
      id: "machine-a", revision: 2, name: "工程机 A", model: "4082F", system_version: "WAT",
      specs_version: "SPECS", specs_fa_version: "", test_head: "Head A", pin_board: "Board A", prober: "", chuck: "",
      source: "装机记录", modules: [],
    }] : []));
    render(<App />);
    fireEvent.click(await screen.findByRole("button", { name: "机台与探针卡" }));
    expect(await screen.findByRole("button", { name: /工程机 A/ })).toBeEnabled();
    fireEvent.click(screen.getByRole("button", { name: "返回测试计划" }));
    expect(await screen.findByRole("button", { name: "新建产品" })).toBeEnabled();
  });

  it("keeps a measurement linked to the same pin when the terminal is renamed", async () => {
    vi.mocked(fetch).mockImplementation(async (endpoint, options) => {
      if (endpoint === "/api/products") return Response.json([{ id: "product-a", model: "SIC-650", manufacturer: "", description: "" }]);
      if (endpoint === "/api/products/product-a/plans" && options?.method === "POST") {
        const payload = JSON.parse(String(options.body));
        expect(payload.pin_setup.mappings[0]).toMatchObject({ terminal: "漏极", wafer_pad: "PAD-D", probe_pin: "A1", tester_pin: "12", tester_port: "SMU2" });
        expect(payload.tests[0].pin_ids).toEqual([payload.pin_setup.mappings[0].id]);
        return Response.json({ ...payload, product_id: "product-a", revision: 1, updated_at: "2026-10-01T00:00:00Z", archived: false, test_count: 1 });
      }
      return Response.json([]);
    });
    render(<App />);
    const create = await screen.findByRole("button", { name: "新建计划" });
    await waitFor(() => expect(create).toBeEnabled());
    fireEvent.click(create);
    fireEvent.change(screen.getByLabelText("计划名称"), { target: { value: "漏电验证" } });
    fireEvent.click(screen.getByRole("button", { name: "添加针脚" }));
    for (const [label, value] of Object.entries({ 端子名称: "D", 晶圆焊盘: "PAD-D", 探针卡针位: "A1", 机台针位: "12", 机台端口: "SMU2" })) {
      fireEvent.change(screen.getByLabelText(label), { target: { value } });
    }
    fireEvent.click(screen.getByRole("button", { name: "添加测项" }));
    fireEvent.change(screen.getByLabelText("测项名称"), { target: { value: "反向漏电" } });
    fireEvent.click(screen.getByRole("checkbox", { name: "D" }));
    fireEvent.change(screen.getByLabelText("端子名称"), { target: { value: "漏极" } });
    expect(screen.getByRole("checkbox", { name: "漏极" })).toBeChecked();
    fireEvent.click(screen.getByRole("button", { name: "保存计划" }));
    expect(await screen.findByText("已保存测试计划。")).toBeVisible();
    expect(screen.getByRole("checkbox", { name: "漏极" })).toBeChecked();
  });

  it("keeps the plan draft when a save requires a fresh login", async () => {
    const requests = vi.mocked(fetch);
    const sampleProduct = { id: "product-a", model: "SIC-650", manufacturer: "", description: "" };
    const savedPlan = { id: "plan-a", product_id: "product-a", name: "漏电验证", description: "",
      updated_at: "2026-10-01T00:00:00Z", revision: 1, archived: false, test_count: 0, tests: [] };
    let respondToSave: ((response: Response) => void) | undefined;
    requests.mockImplementation(async (endpoint, options) => {
      if (endpoint === "/api/products") return Response.json([sampleProduct]);
      if (endpoint === "/api/login") return new Response(null, { status: 204 });
      if (endpoint === "/api/plans/plan-a/export") return new Response(null, { status: 401 });
      if (endpoint === "/api/products/product-a/plans" && options?.method === "POST") {
        return new Promise((resolve) => { respondToSave = resolve; });
      }
      return Response.json([]);
    });
    render(<App />);
    const createPlan = await screen.findByRole("button", { name: "新建计划" });
    await waitFor(() => expect(createPlan).toBeEnabled());
    fireEvent.click(createPlan);
    fireEvent.change(screen.getByLabelText("计划名称"), { target: { value: "漏电验证" } });
    fireEvent.click(screen.getByRole("button", { name: "保存计划" }));
    expect.soft(screen.getByRole("button", { name: "打开公开示例" })).toBeDisabled();
    expect.soft(screen.getByRole("button", { name: "退出登录" })).toBeDisabled();
    expect.soft(screen.getByLabelText("产品", { exact: true })).toBeDisabled();
    respondToSave!(new Response(null, { status: 401 }));
    fireEvent.change(await screen.findByLabelText("账号"), { target: { value: "operator" } });
    fireEvent.change(screen.getByLabelText("密码"), { target: { value: "fixture-password" } });
    fireEvent.click(screen.getByRole("button", { name: "登录" }));
    expect(await screen.findByRole("textbox", { name: "计划名称" })).toHaveValue("漏电验证");
    fireEvent.click(screen.getByRole("button", { name: "保存计划" }));
    respondToSave!(Response.json(savedPlan, { status: 201 }));
    const download = await screen.findByRole("link", { name: "下载已保存计划" });
    expect(download).toHaveAttribute("href", "/api/plans/plan-a/export");
    fireEvent.click(download);
    expect(await screen.findByRole("heading", { name: "登录 WAT 工作台" })).toBeVisible();
  });

  it("ignores an expired example request after returning to plan editing", async () => {
    let answerCompile: ((response: Response) => void) | undefined;
    vi.mocked(fetch).mockImplementation(async (endpoint) => {
      if (endpoint === "/api/products") return Response.json([{ id: "product-a", model: "SIC-650", manufacturer: "", description: "" }]);
      if (endpoint === "/api/example") return Response.json(example);
      if (endpoint === "/api/compile") return new Promise((resolve) => { answerCompile = resolve; });
      if (endpoint === "/api/login") return new Response(null, { status: 204 });
      return Response.json([]);
    });
    render(<App />);
    await openExample();
    fireEvent.click(screen.getByRole("button", { name: "校验并生成程序包" }));
    fireEvent.click(screen.getByRole("button", { name: "返回测试计划" }));
    const createPlan = await screen.findByRole("button", { name: "新建计划" });
    await waitFor(() => expect(createPlan).toBeEnabled());
    fireEvent.click(createPlan);
    fireEvent.change(screen.getByLabelText("计划名称"), { target: { value: "仍在编辑" } });
    await act(async () => answerCompile!(new Response(null, { status: 401 })));
    expect(screen.queryByRole("button", { name: "登录" })).not.toBeInTheDocument();
    expect(screen.getByRole("textbox", { name: "计划名称" })).toHaveValue("仍在编辑");
  });

  it("shows G0 login, opens the workbench after sign-in and clears it on sign-out", async () => {
    const requests = vi.mocked(fetch);
    requests.mockResolvedValueOnce(new Response(null, { status: 401 }));
    render(<App />);

    const username = await screen.findByLabelText("账号");
    expect(screen.queryByRole("button", { name: "校验并生成程序包" })).not.toBeInTheDocument();
    fireEvent.change(username, { target: { value: "operator" } });
    fireEvent.change(screen.getByLabelText("密码"), { target: { value: "wrong-test-password" } });
    requests.mockResolvedValueOnce(new Response(null, { status: 401 }));
    fireEvent.click(screen.getByRole("button", { name: "登录" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("账号或密码错误");

    fireEvent.change(screen.getByLabelText("密码"), { target: { value: "fixture-password" } });
    requests.mockResolvedValueOnce(new Response(null, { status: 204 }));
    fireEvent.click(screen.getByRole("button", { name: "登录" }));
    expect(await screen.findByRole("button", { name: "新建产品" })).toBeEnabled();
    expect(requests).toHaveBeenCalledWith("/api/login", expect.objectContaining({ method: "POST" }));

    requests.mockResolvedValueOnce(new Response(null, { status: 204 }));
    fireEvent.click(screen.getByRole("button", { name: "退出登录" }));
    expect(await screen.findByLabelText("账号")).toBeInTheDocument();
    expect(screen.queryByText("BUK7M6R0-40H")).not.toBeInTheDocument();
  });

  it("opens the public automotive MOSFET example on demand", async () => {
    render(<App />);

    expect(await screen.findByRole("heading", { name: "WAT 程序生成工作台" })).toBeInTheDocument();
    await openExample();
    expect(screen.getByRole("button", { name: "校验并生成程序包" })).toBeEnabled();
    expect(screen.getByText("Package on-state resistance")).toBeInTheDocument();
    expect(screen.getByText(/要求 20 A/)).toBeInTheDocument();
  });

  it("generates a reviewable package and keeps tester deployment visibly locked", async () => {
    render(<App />);
    await openExample();
    vi.mocked(fetch).mockResolvedValueOnce(
      new Response(JSON.stringify(compileResult), { status: 200 })
    );

    fireEvent.click(screen.getByRole("button", { name: "校验并生成程序包" }));

    expect(await screen.findByRole("heading", { name: "公开典型值回放完成，程序包已生成" })).toBeInTheDocument();
    expect(screen.getByText("机台部署锁定")).toBeInTheDocument();
    expect(screen.getByText("Gate threshold voltage")).toBeInTheDocument();
    expect(screen.getByText("3 V")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "下载参考程序包" })).toHaveAttribute(
      "download",
      "BUK7M6R0-40H-g01-reference.zip"
    );
  });

  it("shows an actionable validation summary when generation is blocked", async () => {
    render(<App />);
    await openExample();
    vi.mocked(fetch).mockResolvedValueOnce(
      new Response(
        JSON.stringify({
          error: "PROGRAM_VALIDATION_FAILED",
          findings: [
            {
              severity: "error",
              code: "COMPLIANCE_REQUIRED",
              message: "击穿扫描必须设置大于 0 的电流限值。",
              primitive_id: "drain_breakdown",
            },
          ],
        }),
        { status: 422 }
      )
    );

    fireEvent.click(screen.getByRole("button", { name: "校验并生成程序包" }));

    const summary = await screen.findByRole("alert");
    expect(summary).toHaveTextContent("击穿扫描必须设置大于 0 的电流限值。");
    expect(summary).toHaveFocus();
  });

  it("does not call a failed typical-value replay successful", async () => {
    render(<App />);
    await openExample();
    vi.mocked(fetch).mockResolvedValueOnce(
      new Response(
        JSON.stringify({
          ...compileResult,
          status: "REPLAY_LIMIT_FAILED",
          replay: {
            ...compileResult.replay,
            results: compileResult.replay.results.map((item) => ({ ...item, passed: false })),
          },
        }),
        { status: 200 },
      ),
    );

    fireEvent.click(screen.getByRole("button", { name: "校验并生成程序包" }));

    expect(await screen.findByRole("heading", { name: "典型值回放完成，存在未通过项" })).toBeInTheDocument();
    expect(screen.queryByText("公开典型值回放完成，程序包已生成")).not.toBeInTheDocument();
  });
});
