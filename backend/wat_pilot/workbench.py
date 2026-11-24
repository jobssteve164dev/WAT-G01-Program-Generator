import json
import sqlite3
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated
from urllib.parse import quote
from uuid import UUID, uuid4

from fastapi import APIRouter, HTTPException
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from pydantic import ConfigDict, Field, FiniteFloat, StringConstraints, model_validator

from .models import StrictModel


Name = Annotated[str, StringConstraints(min_length=1, max_length=80)]


class WorkbenchModel(StrictModel):
    model_config = ConfigDict(str_strip_whitespace=True)


class ProductInput(WorkbenchModel):
    model: Name
    manufacturer: str = Field(default="", max_length=80)
    description: str = Field(default="", max_length=240)


class Product(ProductInput):
    id: UUID


class ProductCreate(ProductInput):
    id: UUID | None = None


class PinMapping(WorkbenchModel):
    id: UUID
    terminal: Name
    wafer_pad: str = Field(default="", max_length=80)
    probe_pin: str = Field(default="", max_length=80)
    tester_pin: str = Field(default="", max_length=80)
    tester_port: str = Field(default="", max_length=80)


class PinSetup(WorkbenchModel):
    probe_card: str = Field(default="", max_length=80)
    tester: str = Field(default="", max_length=80)
    mappings: list[PinMapping] = Field(default_factory=list, max_length=64)


class PlanItem(WorkbenchModel):
    name: Name
    conditions: str = Field(default="", max_length=500)
    unit: str = Field(default="", max_length=24)
    lower_limit: FiniteFloat | None = None
    upper_limit: FiniteFloat | None = None
    pin_ids: list[UUID] = Field(default_factory=list, max_length=64, exclude_if=lambda ids: not ids)

    @model_validator(mode="after")
    def check_limits(self):
        if self.lower_limit is not None and self.upper_limit is not None and self.lower_limit > self.upper_limit:
            raise ValueError("限值下限不能大于上限。")
        return self


class PlanInput(WorkbenchModel):
    name: Name
    description: str = Field(default="", max_length=500)
    tests: list[PlanItem] = Field(default_factory=list, max_length=64)
    pin_setup: PinSetup = Field(default_factory=PinSetup)

    @model_validator(mode="after")
    def check_pin_references(self):
        mappings = self.pin_setup.mappings
        ids = {mapping.id for mapping in mappings}
        if len(ids) != len(mappings) or len({mapping.terminal.casefold() for mapping in mappings}) != len(mappings):
            raise ValueError("针脚名称不能重复。")
        for item in self.tests:
            if len(set(item.pin_ids)) != len(item.pin_ids) or not ids.issuperset(item.pin_ids):
                raise ValueError("测项只能引用本计划中定义的针脚，每个针脚只能选一次。")
        return self


class PlanCreate(PlanInput):
    id: UUID | None = None


class PlanUpdate(PlanInput):
    revision: int = Field(ge=1)
    archived: bool = False


class PlanSummary(WorkbenchModel):
    id: UUID
    product_id: UUID
    name: Name
    revision: int
    updated_at: str
    archived: bool
    test_count: int


class Plan(PlanInput, PlanSummary):
    pass


class WorkbenchStore:
    def __init__(self, path: Path):
        self.path = path

    def connect(self):
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return closing(conn)

    def initialize(self):
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        with self.connect() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS products (
                    id TEXT PRIMARY KEY,
                    model TEXT NOT NULL,
                    model_key TEXT NOT NULL UNIQUE,
                    manufacturer TEXT NOT NULL,
                    description TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS plans (
                    id TEXT PRIMARY KEY,
                    product_id TEXT NOT NULL REFERENCES products(id),
                    name TEXT NOT NULL,
                    description TEXT NOT NULL,
                    tests TEXT NOT NULL,
                    revision INTEGER NOT NULL,
                    updated_at TEXT NOT NULL,
                    archived INTEGER NOT NULL
                );
                CREATE INDEX IF NOT EXISTS product_plans ON plans (product_id, archived, updated_at DESC);
                CREATE TABLE IF NOT EXISTS plan_pin_setups (
                    plan_id TEXT PRIMARY KEY REFERENCES plans(id),
                    plan_revision INTEGER NOT NULL,
                    setup TEXT NOT NULL,
                    test_pins TEXT NOT NULL
                );
            """)


PLAN_COLUMNS = "id, product_id, name, revision, updated_at, archived, json_array_length(tests) AS test_count"


def _product(conn: sqlite3.Connection, product_id: UUID) -> Product:
    row = conn.execute("SELECT id, model, manufacturer, description FROM products WHERE id = ?", (str(product_id),)).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="产品不存在。")
    return Product(**dict(row))


def _read_plan(conn: sqlite3.Connection, plan_id: UUID) -> Plan:
    row = conn.execute(
        f"""SELECT {PLAN_COLUMNS}, description, tests, setup, test_pins FROM plans
            LEFT JOIN plan_pin_setups ON plan_id = id AND plan_revision = revision WHERE id = ?""",
        (str(plan_id),),
    ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="测试计划不存在。")
    fields = dict(row)
    fields["tests"] = json.loads(fields["tests"])
    setup = fields.pop("setup")
    test_pins = fields.pop("test_pins")
    if setup is not None:
        fields["pin_setup"] = json.loads(setup)
        for item, pin_ids in zip(fields["tests"], json.loads(test_pins), strict=True):
            item["pin_ids"] = pin_ids
    return Plan(**fields)


def _serialized_tests(plan: PlanInput) -> str:
    return json.dumps([item.model_dump(exclude={"pin_ids"}) for item in plan.tests], ensure_ascii=False, allow_nan=False)


def _write_pin_setup(conn: sqlite3.Connection, plan_id: UUID, revision: int, plan: PlanInput):
    if plan.pin_setup == PinSetup():
        return
    conn.execute(
        """INSERT INTO plan_pin_setups (plan_id, plan_revision, setup, test_pins) VALUES (?, ?, ?, ?)
           ON CONFLICT(plan_id) DO UPDATE SET plan_revision = excluded.plan_revision,
               setup = excluded.setup, test_pins = excluded.test_pins""",
        (str(plan_id), revision, plan.pin_setup.model_dump_json(),
         json.dumps([[str(pin) for pin in item.pin_ids] for item in plan.tests])),
    )


def _same_content(stored: Plan, intended: PlanInput, archived: bool) -> bool:
    return (stored.name, stored.description, stored.tests, stored.pin_setup, stored.archived) == (
        intended.name, intended.description, intended.tests, intended.pin_setup, archived,
    )


class WorkbenchRoute(APIRoute):
    def get_route_handler(self):
        route = super().get_route_handler()

        async def validate_request(request):
            try:
                return await route(request)
            except RequestValidationError as error:
                detail = "请检查填写内容、数值与限值。"
                cause = error.errors()[0].get("ctx", {}).get("error")
                if isinstance(cause, ValueError):
                    detail = str(cause)
                raise HTTPException(status_code=422, detail=detail) from error

        return validate_request


def workbench_routes(store: WorkbenchStore) -> APIRouter:
    router = APIRouter(prefix="/api", route_class=WorkbenchRoute)

    @router.get("/products", response_model=list[Product])
    def list_products():
        with store.connect() as conn:
            return [dict(row) for row in conn.execute("SELECT id, model, manufacturer, description FROM products ORDER BY model_key")]

    @router.post("/products", status_code=201, response_model=Product)
    def create_product(product: ProductCreate):
        product_id = product.id or uuid4()
        with store.connect() as conn, conn:
            saved = conn.execute(
                "INSERT INTO products VALUES (?, ?, ?, ?, ?) ON CONFLICT DO NOTHING",
                (str(product_id), product.model, product.model.casefold(), product.manufacturer, product.description),
            )
            if not saved.rowcount:
                matching_retry = conn.execute(
                    "SELECT 1 FROM products WHERE id = ? AND model = ? AND manufacturer = ? AND description = ?",
                    (str(product_id), product.model, product.manufacturer, product.description),
                ).fetchone()
                if not matching_retry:
                    raise HTTPException(status_code=409, detail="该产品型号或新建记录已存在。")
        return Product(id=product_id, **product.model_dump(exclude={"id"}))

    @router.put("/products/{product_id}", response_model=Product)
    def update_product(product_id: UUID, product: ProductInput):
        with store.connect() as conn, conn:
            try:
                saved = conn.execute(
                    "UPDATE products SET model = ?, model_key = ?, manufacturer = ?, description = ? WHERE id = ?",
                    (product.model, product.model.casefold(), product.manufacturer, product.description, str(product_id)),
                )
            except sqlite3.IntegrityError as error:
                raise HTTPException(status_code=409, detail="该产品型号已存在。") from error
            if not saved.rowcount:
                raise HTTPException(status_code=404, detail="产品不存在。")
        return Product(id=product_id, **product.model_dump())

    @router.get("/products/{product_id}/plans", response_model=list[PlanSummary])
    def list_plans(product_id: UUID, archived: bool = False):
        with store.connect() as conn:
            _product(conn, product_id)
            return [dict(row) for row in conn.execute(
                f"SELECT {PLAN_COLUMNS} FROM plans WHERE product_id = ? AND archived = ? ORDER BY updated_at DESC, id",
                (str(product_id), archived),
            )]

    @router.post("/products/{product_id}/plans", status_code=201, response_model=Plan)
    def create_plan(product_id: UUID, plan: PlanCreate):
        plan_id = plan.id or uuid4()
        with store.connect() as conn, conn:
            _product(conn, product_id)
            saved = conn.execute(
                "INSERT INTO plans VALUES (?, ?, ?, ?, ?, 1, ?, 0) ON CONFLICT(id) DO NOTHING",
                (str(plan_id), str(product_id), plan.name, plan.description, _serialized_tests(plan), datetime.now(UTC).isoformat()),
            )
            if saved.rowcount:
                _write_pin_setup(conn, plan_id, 1, plan)
            current = _read_plan(conn, plan_id)
            if not saved.rowcount and (current.product_id != product_id or not _same_content(current, plan, False)):
                raise HTTPException(status_code=409, detail="此计划已保存为另一版本。请重新打开已保存的计划后修改。")
            return current

    @router.get("/plans/{plan_id}", response_model=Plan)
    def get_plan(plan_id: UUID):
        with store.connect() as conn:
            return _read_plan(conn, plan_id)

    @router.put("/plans/{plan_id}", response_model=Plan)
    def update_plan(plan_id: UUID, plan: PlanUpdate):
        includes_pin_setup = "pin_setup" in plan.model_fields_set
        with store.connect() as conn, conn:
            saved = conn.execute(
                """UPDATE plans SET name = ?, description = ?, tests = ?, revision = revision + 1,
                       updated_at = ?, archived = ? WHERE id = ? AND revision = ?
                       AND (? OR NOT EXISTS (SELECT 1 FROM plan_pin_setups WHERE plan_id = id AND plan_revision = revision))""",
                (plan.name, plan.description, _serialized_tests(plan), datetime.now(UTC).isoformat(),
                 plan.archived, str(plan_id), plan.revision, includes_pin_setup),
            )
            if saved.rowcount:
                _write_pin_setup(conn, plan_id, plan.revision + 1, plan)
            current = _read_plan(conn, plan_id)
            matching_retry = current.revision == plan.revision + 1 and _same_content(current, plan, plan.archived)
            if not saved.rowcount and not matching_retry:
                if not includes_pin_setup and current.pin_setup != PinSetup():
                    raise HTTPException(status_code=409, detail="此计划包含针脚映射，请刷新工作台后再保存。")
                raise HTTPException(status_code=409, detail="此计划已被更新。请保留当前修改，再重新打开最新版本。")
            return current

    @router.get("/plans/{plan_id}/export")
    def export_plan(plan_id: UUID):
        with store.connect() as conn:
            plan = _read_plan(conn, plan_id)
            product = _product(conn, plan.product_id)
        filename = quote(f"{product.model}-{plan.name}.json", safe="")
        return JSONResponse(
            {"schema_version": 1, "deployable_to_tester": False,
             "product": product.model_dump(mode="json"), "plan": plan.model_dump(mode="json")},
            headers={"Content-Disposition": f"attachment; filename=wat-test-plan.json; filename*=UTF-8''{filename}"},
        )

    return router
