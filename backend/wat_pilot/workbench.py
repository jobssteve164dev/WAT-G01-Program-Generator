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


class PlanItem(WorkbenchModel):
    name: Name
    conditions: str = Field(default="", max_length=500)
    unit: str = Field(default="", max_length=24)
    lower_limit: FiniteFloat | None = None
    upper_limit: FiniteFloat | None = None

    @model_validator(mode="after")
    def check_limits(self):
        if self.lower_limit is not None and self.upper_limit is not None and self.lower_limit > self.upper_limit:
            raise ValueError("限值下限不能大于上限。")
        return self


class PlanInput(WorkbenchModel):
    name: Name
    description: str = Field(default="", max_length=500)
    tests: list[PlanItem] = Field(default_factory=list, max_length=64)


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
            """)


PLAN_COLUMNS = "id, product_id, name, revision, updated_at, archived, json_array_length(tests) AS test_count"


def _product(conn: sqlite3.Connection, product_id: UUID) -> Product:
    row = conn.execute("SELECT id, model, manufacturer, description FROM products WHERE id = ?", (str(product_id),)).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="产品不存在。")
    return Product(**dict(row))


def _read_plan(conn: sqlite3.Connection, plan_id: UUID) -> Plan:
    row = conn.execute(f"SELECT {PLAN_COLUMNS}, description, tests FROM plans WHERE id = ?", (str(plan_id),)).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="测试计划不存在。")
    fields = dict(row)
    fields["tests"] = json.loads(fields["tests"])
    return Plan(**fields)


def _serialized_tests(plan: PlanInput) -> str:
    return json.dumps([item.model_dump() for item in plan.tests], ensure_ascii=False, allow_nan=False)


def _same_content(stored: Plan, intended: PlanInput, archived: bool) -> bool:
    return (stored.name, stored.description, stored.tests, stored.archived) == (
        intended.name, intended.description, intended.tests, archived,
    )


class WorkbenchRoute(APIRoute):
    def get_route_handler(self):
        route = super().get_route_handler()

        async def validate_request(request):
            try:
                return await route(request)
            except RequestValidationError as error:
                raise HTTPException(status_code=422, detail="请检查填写内容、数值与限值。") from error

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
        with store.connect() as conn, conn:
            saved = conn.execute(
                """UPDATE plans SET name = ?, description = ?, tests = ?, revision = revision + 1,
                       updated_at = ?, archived = ? WHERE id = ? AND revision = ?""",
                (plan.name, plan.description, _serialized_tests(plan), datetime.now(UTC).isoformat(),
                 plan.archived, str(plan_id), plan.revision),
            )
            current = _read_plan(conn, plan_id)
            matching_retry = current.revision == plan.revision + 1 and _same_content(current, plan, plan.archived)
            if not saved.rowcount and not matching_retry:
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
