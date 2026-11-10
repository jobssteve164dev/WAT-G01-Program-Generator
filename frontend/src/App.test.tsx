import { cleanup, fireEvent, render, screen } from "@testing-library/react";
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


describe("WAT pilot", () => {
  beforeEach(() => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response(JSON.stringify(example), { status: 200 }))
    );
  });

  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
  });

  it("loads the public automotive MOSFET example as the primary working surface", async () => {
    render(<App />);

    expect(await screen.findByRole("heading", { name: "WAT 程序生成工作台" })).toBeInTheDocument();
    expect(await screen.findByText("BUK7M6R0-40H")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "校验并生成程序包" })).toBeEnabled();
    expect(screen.getByText("Package on-state resistance")).toBeInTheDocument();
    expect(screen.getByText(/要求 20 A/)).toBeInTheDocument();
  });

  it("generates a reviewable package and keeps tester deployment visibly locked", async () => {
    render(<App />);
    await screen.findByText("BUK7M6R0-40H");
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
    await screen.findByText("BUK7M6R0-40H");
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
    await screen.findByText("BUK7M6R0-40H");
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
