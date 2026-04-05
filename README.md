# X Electronics — Warehouse Management System

A purpose-built warehouse management system for X Electronics, built on the [Frappe Framework](https://frappeframework.com). Designed around a stateless ledger architecture that guarantees stock balance accuracy without storing derived state.

---

## Table of Contents

1. [Project Overview](#1-project-overview)
2. [Architecture](#2-architecture)
3. [Data Model](#3-data-model)
4. [Business Logic](#4-business-logic)
5. [Reports](#5-reports)
6. [Test Suite](#6-test-suite)
7. [Installation](#7-installation)
8. [Usage Guide](#8-usage-guide)
9. [Project Structure](#9-project-structure)
10. [Design Decisions](#10-design-decisions)
11. [Contributing](#11-contributing)

---

## 1. Project Overview

X Electronics WMS provides the following core functionality:

- **Warehouse management** with a hierarchical tree structure (group warehouses containing leaf warehouses)
- **Item / product master** as the basis for all stock movements
- **Stock Entry transactions** — Receipt (goods in), Consume (goods out), Transfer (goods between warehouses)
- **Stock Ledger** — an immutable, append-only audit trail of every stock movement
- **Moving average valuation** — the industry-standard costing method, computed statelessly from the ledger
- **Stock Ledger Report** — every movement line with a running balance
- **Stock Balance Report** — point-in-time balance per item and warehouse, with warehouse tree consolidation

### What makes this different from ERPNext stock

ERPNext stores a running balance (`qty_after_transaction`) on every Stock Ledger Entry. If one entry is corrupted or out of sequence, every entry after it is wrong, and a full recalculation job is required.

This system stores **no running balances**. Every balance, valuation rate, and stock value is derived on demand from a `SUM()` over the ledger. There is no state to corrupt and no recalculation job to run.

---

## 2. Architecture

### Data flow

```
User creates Stock Entry (Receipt / Consume / Transfer)
                │
                │  on_submit()
                ▼
    StockEntry.make_ledger_entries()
                │
                │  One SLE per warehouse leg
                ▼
    Stock Ledger Entry  ──────────────────────────────────────┐
    (immutable, docstatus=1)                                  │
                                                              │
                    ┌─────────────────────────────────────────┘
                    │
          ┌─────────┴──────────┐
          ▼                    ▼
  Stock Ledger Report    Stock Balance Report
  (window functions)     (aggregation + tree)
```

### Cancellation flow

```
User cancels Stock Entry
                │
                │  on_cancel()
                ▼
    StockEntry.cancel_ledger_entries()
                │
                │  Sets docstatus → 2 on each linked SLE
                ▼
    Cancelled SLEs are excluded from all queries
    (every query filters WHERE docstatus = 1)
```

### Valuation flow (moving average)

```
New Receipt arrives: 10 units @ 200
                │
                ▼
    Query current ledger state:
        SUM(qty_change)  → current_qty   = 20
        SUM(stock_value) → current_value = 3000
                │
                ▼
    Compute new moving average:
        new_qty   = 20 + 10           = 30
        new_value = 3000 + (10 × 200) = 5000
        new_rate  = 5000 / 30         = 166.67
                │
                ▼
    Write SLE with:
        qty_change     = +10
        valuation_rate = 166.67
        stock_value    = 2000   (qty_change × incoming_rate)
```

---

## 3. Data Model

### Warehouse

The warehouse DocType uses Frappe's built-in tree (nested sets) structure. Every warehouse node has `lft` and `rgt` values managed automatically by Frappe, enabling single-query tree traversal.

| Field | Type | Description |
|---|---|---|
| `warehouse_name` | Data | Human-readable name. Also used as the document name (autoname). |
| `parent_warehouse` | Link → Warehouse | Parent node in the tree. Empty for root warehouses. |
| `is_group` | Check | If checked, this warehouse is a container and cannot be used in transactions. |
| `lft` | Int | Left boundary in nested set. Managed by Frappe. Do not edit manually. |
| `rgt` | Int | Right boundary in nested set. Managed by Frappe. Do not edit manually. |

**Tree example:**

```
All Warehouses
└── Nairobi (group)
    ├── Main Store       ← leaf, can receive stock
    ├── Spare Parts      ← leaf, can receive stock
    └── Bonded (group)
        └── Holding Bay  ← leaf, can receive stock
```

Selecting "Nairobi" in the Stock Balance report automatically includes Main Store, Spare Parts, and Holding Bay via a single nested-set SQL query.

---

### Item

The item master is intentionally minimal. X Electronics WMS is a stock movement system, not an ERP. Item enrichment (pricing, suppliers, categories) belongs in a separate system or can be extended later.

| Field | Type | Description |
|---|---|---|
| `item_code` | Data | Unique identifier. Used as the document name (autoname). |
| `item_name` | Data | Display name shown in reports. |
| `stock_uom` | Data | Unit of measure (e.g. Nos, Pcs, Kg). |
| `is_stock_item` | Check | Marks the item as a physical stock item. |

---

### Stock Entry

The user-facing transaction document. A Stock Entry has a type and one or more item rows. It is not valid until submitted. Submission triggers ledger entry creation.

| Field | Type | Description |
|---|---|---|
| `stock_entry_type` | Select | One of: Receipt, Consume, Transfer. |
| `posting_date` | Date | The business date of the transaction. |
| `posting_time` | Time | The business time of the transaction. |
| `items` | Table → Stock Entry Detail | One or more item rows. |

**Stock Entry Detail** (child table):

| Field | Type | Description |
|---|---|---|
| `item` | Link → Item | The item being moved. |
| `qty` | Float | Quantity. Must be > 0. |
| `rate` | Float | Required for Receipt only. The purchase rate per unit. |
| `s_warehouse` | Link → Warehouse | Source warehouse. Required for Consume and Transfer. |
| `t_warehouse` | Link → Warehouse | Target warehouse. Required for Receipt and Transfer. |

**Type rules enforced by the controller:**

| Type | s_warehouse | t_warehouse | rate |
|---|---|---|---|
| Receipt | Must be empty | Required | Required |
| Consume | Required | Must be empty | Not used |
| Transfer | Required | Required | Not used |

---

### Stock Ledger Entry

The heart of the system. Never created by users — only by `StockEntry.make_ledger_entries()` on submit. Never edited — Read Only is set on the DocType. Cancelled by `StockEntry.cancel_ledger_entries()` on cancel.

| Field | Type | Description |
|---|---|---|
| `item` | Link → Item | The item this entry belongs to. |
| `warehouse` | Link → Warehouse | The warehouse this entry belongs to. |
| `posting_datetime` | Datetime | Combined posting date and time from the Stock Entry. |
| `qty_change` | Float | Positive for incoming stock, negative for outgoing. |
| `valuation_rate` | Float | Moving average rate at the time of this entry. |
| `stock_value` | Float | Value change = qty_change × rate. Negative for outgoing. |
| `voucher_type` | Data | The DocType that created this entry (always "Stock Entry"). |
| `voucher_no` | Data | The name of the Stock Entry that created this entry. |

**Key invariants:**

- `docstatus = 1` means active (counts in all balance queries)
- `docstatus = 2` means cancelled (excluded from all balance queries)
- `docstatus = 0` never occurs (SLEs are submitted immediately on creation)
- `SUM(qty_change)` for an item+warehouse = current stock quantity
- `SUM(stock_value)` for an item+warehouse = current stock value
- `SUM(stock_value) / SUM(qty_change)` = current moving average rate

---

## 4. Business Logic

All business logic lives in one file: `stock_entry/stock_entry.py`.

### Validation (`validate`)

Runs on every save (draft and submit). Performs two checks:

**1. Item row validation** — enforces the type rules described in the Data Model section. Throws a `ValidationError` for:
- Empty items list
- Qty ≤ 0
- Missing or wrong warehouse fields for the entry type
- Missing rate on Receipt
- Negative rate on Receipt

**2. Warehouse validation** — checks every warehouse referenced in the entry. Throws a `ValidationError` if any warehouse has `is_group = 1`. Group warehouses are structural containers; stock cannot enter or leave them directly.

### Ledger creation (`on_submit → make_ledger_entries`)

Called once when the Stock Entry is submitted. For each item row:

- If `t_warehouse` is set → creates an **incoming SLE** (positive qty_change) at the target warehouse
- If `s_warehouse` is set → creates an **outgoing SLE** (negative qty_change) at the source warehouse

For a Transfer, both SLEs are created. For Receipt, only the incoming. For Consume, only the outgoing.

### Moving average valuation (`_create_sle`)

For each SLE, the controller queries the current ledger state for that item+warehouse using a single SQL `SUM()` query. It then applies the moving average formula:

**Incoming stock:**
```
new_rate  = (current_value + incoming_qty × incoming_rate) / (current_qty + incoming_qty)
new_value = incoming_qty × incoming_rate
```

**Outgoing stock:**
```
rate  = current_value / current_qty   (current moving average — unchanged)
value = qty_change × rate             (negative number)
```

The rate for a Transfer's incoming leg equals the rate at the source warehouse — value is preserved across the transfer.

### Cancellation (`on_cancel → cancel_ledger_entries`)

Fetches all SLEs linked to this Stock Entry by `voucher_no` and cancels each one. Frappe's `cancel()` sets `docstatus = 2`. All balance queries filter `WHERE docstatus = 1`, so cancelled SLEs are immediately excluded from every report and balance calculation. The full audit trail remains in the database.

---

## 5. Reports

Both reports are Frappe Script Reports. They query the `tabStock Ledger Entry` table directly via SQL and return results to Frappe's standard report renderer.

### Stock Ledger Report

Shows every stock movement line for the selected filters, with a running balance computed by SQL window functions.

**Filters:**

| Filter | Type | Description |
|---|---|---|
| From Date | Date | Restricts entries to `posting_datetime >= from_date`. |
| To Date | Date | Restricts entries to `posting_datetime <= to_date`. |
| Item | Link | Show movements for one item only. |
| Warehouse | Link | Show movements for one warehouse only. |
| Voucher No | Data | Show movements from one Stock Entry only. |

**Columns:**

| Column | Description |
|---|---|
| Date | `posting_datetime` from the SLE. |
| Item / Item Name | Linked item. |
| Warehouse | Linked warehouse. |
| Voucher Type / No | The originating Stock Entry. |
| Qty Change | The signed quantity change for this movement. |
| Balance Qty | Running total of `qty_change` up to this row (window function). |
| Valuation Rate | Moving average rate recorded at the time of this entry. |
| Stock Value | Signed value change for this movement. |
| Balance Value | Running total of `stock_value` up to this row (window function). |

**SQL pattern (simplified):**

```sql
SELECT
    sle.qty_change,
    SUM(sle.qty_change)  OVER w AS balance_qty,
    sle.stock_value,
    SUM(sle.stock_value) OVER w AS balance_value
FROM `tabStock Ledger Entry` sle
WHERE sle.docstatus = 1
WINDOW w AS (
    PARTITION BY sle.item, sle.warehouse
    ORDER BY sle.posting_datetime, sle.creation
    ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
)
```

The window function partitions by item+warehouse and orders by posting time. This gives an accurate per-item-per-warehouse running balance without any stored state.

---

### Stock Balance Report

Shows the balance (qty, value, valuation rate) for each item+warehouse combination as of a given date. Supports warehouse tree consolidation — selecting a group warehouse shows the combined balance of all leaf children.

**Filters:**

| Filter | Type | Description |
|---|---|---|
| As On Date | Date | Required. Shows balance as of end of this date. |
| Item | Link | Filter to one item. |
| Warehouse | Link | Filter to one warehouse or group (see tree consolidation below). |

**Columns:**

| Column | Description |
|---|---|
| Item / Item Name | The item. |
| Warehouse | The specific leaf warehouse. |
| Balance Qty | `SUM(qty_change)` up to the selected date. |
| Valuation Rate | `SUM(stock_value) / SUM(qty_change)`. Computed in Python to avoid SQL division issues. |
| Stock Value | `SUM(stock_value)` up to the selected date. |

Rows with `balance_qty = 0` are excluded (stock that came in and went out completely).

**Warehouse tree consolidation:**

When a group warehouse is selected, the report uses Frappe's nested set columns (`lft`, `rgt`) to find all leaf children in a single query:

```sql
SELECT name FROM `tabWarehouse`
WHERE lft >= %(lft)s AND rgt <= %(rgt)s AND is_group = 0
```

This returns every non-group warehouse that is a descendant of the selected warehouse — at any depth in the tree — without recursion.

---

## 6. Test Suite

The test suite has 44 tests organised into 6 layers across 4 test files. Every test creates its own data, cleans up after itself in `tearDown`, and leaves the database exactly as it found it.

### Test organisation

```
doctype/
├── warehouse/
│   └── test_warehouse.py          ← Warehouse tree structure and group restriction
├── item/
│   └── test_item.py               ← Item creation and uniqueness
├── stock_entry/
│   └── test_stock_entry.py        ← Validation, business logic, moving average, cancellation
└── stock_ledger_entry/
    └── test_stock_ledger_entry.py ← Immutability and report correctness

utils/
└── test_helpers.py                ← Shared factory functions (no test cases)
```

### Test layers

**Layer 1 — Validation (11 tests)**
Verifies that invalid inputs are rejected before any database write. Every test in this layer expects a `ValidationError`. None should produce a Stock Ledger Entry.

**Layer 2 — Business logic (14 tests)**
Verifies that submitting a Stock Entry produces the correct ledger entries. Checks SLE count, sign of `qty_change`, balance changes, and stock value.

**Layer 3 — Moving average (5 tests)**
Verifies the mathematical correctness of moving average valuation across multiple receipts and a consume. The known test case:

```
Batch 1: 10 @ 100  →  rate = 100.00
Batch 2: 10 @ 200  →  rate = (10×100 + 10×200) / 20 = 150.00
Batch 3:  5 @ 120  →  rate = (20×150 +  5×120) / 25 = 144.00
Consume 5          →  rate = 144.00  (unchanged)
```

Also verifies the accounting identity: `stock_value = balance_qty × valuation_rate` always holds.

**Layer 4 — Cancellation (4 tests)**
Verifies that cancelling a Stock Entry fully reverses its ledger impact. Checks that SLEs are set to `docstatus=2`, no active SLEs remain, and both qty and value balances return to their pre-entry values.

**Layer 5 — Immutability (1 test)**
Verifies that directly saving a Stock Ledger Entry raises an exception. The Read Only flag on the DocType enforces this.

**Layer 6 — Reports (9 tests)**
Verifies that both Script Reports return arithmetically correct data. Covers running balances, valuation rate, point-in-time filtering, and warehouse tree consolidation.

### Shared test helpers (`utils/test_helpers.py`)

All factory functions and cleanup utilities are centralised here and imported by every test file.

| Helper | Purpose |
|---|---|
| `make_warehouse(name, parent, is_group)` | Create or return a warehouse. Idempotent. |
| `make_item(item_code)` | Create or return an item. Idempotent. |
| `make_receipt(item, warehouse, qty, rate)` | Create and submit a Receipt. Returns the document. |
| `make_consume(item, warehouse, qty)` | Create and submit a Consume. Returns the document. |
| `make_transfer(item, src, dst, qty)` | Create and submit a Transfer. Returns the document. |
| `get_balance(item, warehouse)` | Return `(qty, value)` from the live ledger. |
| `get_valuation_rate(item, warehouse)` | Return current moving average rate. |
| `cancel_and_delete_stock_entry(doc)` | Cancel (if submitted) and delete a Stock Entry. Safe to call in any state. |
| `delete_test_records(pairs)` | Delete a list of `(doctype, name)` records. Safe if they do not exist. |

### Running tests

Run the entire suite:

```bash
bench --site warehouse.localhost run-tests --app x_electronics_wms
```

Run a single test file:

```bash
bench --site warehouse.localhost run-tests \
    --module x_electronics_wms.x_electronics_warehouse_management_system.doctype.stock_entry.test_stock_entry
```

Run a single test class:

```bash
bench --site warehouse.localhost run-tests \
    --module x_electronics_wms.x_electronics_warehouse_management_system.doctype.stock_entry.test_stock_entry \
    --test TestMovingAverage
```

---

## 7. Installation

### Prerequisites

| Requirement | Version | Notes |
|---|---|---|
| Python | 3.10+ | Frappe requires Python 3.10 or later |
| Node.js | 18+ | Required for Frappe's frontend build |
| MariaDB | 10.6+ | MySQL 8.0 also supported |
| Redis | 6+ | Used by Frappe for caching and queuing |
| Frappe Framework | 15+ | The application platform |
| bench | Latest | Frappe's CLI tool |

### Step 1 — Install bench

If bench is not already installed:

```bash
pip install frappe-bench
```

### Step 2 — Create a new bench

```bash
bench init frappe_bench --frappe-branch version-15
cd frappe_bench
```

### Step 3 — Create a site

```bash
bench new-site warehouse.localhost \
    --mariadb-root-password your_root_password \
    --admin-password your_admin_password
```

### Step 4 — Get the app

Clone the app into the bench's `apps` directory:

```bash
bench get-app https://github.com/your-org/x_electronics_wms.git
```

Or if you are setting up from a local copy:

```bash
# From inside frappe_bench/
cp -r /path/to/x_electronics_wms apps/x_electronics_wms
```

### Step 5 — Install the app on the site

```bash
bench --site warehouse.localhost install-app x_electronics_wms
```

### Step 6 — Enable developer mode

Developer mode is required to create and modify DocTypes:

```bash
bench --site warehouse.localhost set-config developer_mode 1
bench restart
```

### Step 7 — Run migrations

```bash
bench --site warehouse.localhost migrate
```

### Step 8 — Create the DocTypes

The five DocTypes must be created via the Frappe Desk UI in the following order (dependencies first):

1. **Warehouse** — Is Tree ✅, Autoname: `field:warehouse_name`
2. **Item** — Autoname: `field:item_code`
3. **Stock Entry Detail** — Is Child Table ✅
4. **Stock Entry** — Is Submittable ✅
5. **Stock Ledger Entry** — Is Submittable ✅, Read Only ✅

Refer to the [Data Model](#3-data-model) section for the exact field definitions of each DocType.

### Step 9 — Create the Reports

Both reports must be registered in the Desk UI before their Python files will be loaded. Go to **Desk → Report → New** for each:

| Report Name | Report Type | Reference DocType | Module |
|---|---|---|---|
| Stock Ledger | Script Report | Stock Ledger Entry | X Electronics Warehouse Management System |
| Stock Balance | Script Report | Stock Ledger Entry | X Electronics Warehouse Management System |

After saving each report in the Desk, the `.json` definition file is created automatically in the correct folder.

### Step 10 — Clear cache and restart

```bash
bench clear-cache
bench restart
```

### Step 11 — Verify installation

Run the test suite to confirm everything is working:

```bash
bench --site warehouse.localhost run-tests --app x_electronics_wms
```

Expected output: **44 tests, 0 failures**.

---

## 8. Usage Guide

### Creating a Receipt (goods arriving)

1. Go to **Stock Entry → New**
2. Set **Stock Entry Type** to `Receipt`
3. Set **Posting Date** (and optionally Posting Time)
4. In the Items table, add one row per item:
   - Select the **Item**
   - Enter the **Qty** received
   - Enter the **Rate** (purchase price per unit) — required for Receipt
   - Select the **Target Warehouse** where the goods will be stored
5. **Save** → review → **Submit**

On submit, one Stock Ledger Entry is created per item row. The moving average valuation rate for that item in that warehouse is updated automatically.

---

### Creating a Consume (goods leaving)

1. Go to **Stock Entry → New**
2. Set **Stock Entry Type** to `Consume`
3. Set **Posting Date**
4. In the Items table:
   - Select the **Item**
   - Enter the **Qty** consumed
   - Select the **Source Warehouse** from which stock is taken
   - Leave Rate and Target Warehouse empty
5. **Save** → **Submit**

The valuation rate used for the outgoing SLE is the current moving average rate of the item in the source warehouse.

---

### Creating a Transfer (goods moving between warehouses)

1. Go to **Stock Entry → New**
2. Set **Stock Entry Type** to `Transfer`
3. Set **Posting Date**
4. In the Items table:
   - Select the **Item**
   - Enter the **Qty** to move
   - Select the **Source Warehouse**
   - Select the **Target Warehouse**
   - Leave Rate empty
5. **Save** → **Submit**

Two SLEs are created: one negative at the source, one positive at the destination. The valuation rate at the destination matches the source — no value is created or destroyed by a transfer.

---

### Cancelling a Stock Entry

Open the submitted Stock Entry and click **Cancel**. This:

1. Calls `on_cancel` on the controller
2. Fetches all linked SLEs
3. Sets each SLE's `docstatus` to `2` (cancelled)
4. The cancelled SLEs are immediately excluded from all balance queries

The audit trail is preserved — cancelled SLEs remain in the database and can be viewed, but they have no effect on balances or reports.

---

### Running the Stock Ledger Report

Go to **Reports → Stock Ledger**. Apply any combination of filters:

- Use **From Date / To Date** to narrow a date range
- Use **Item** to see movements for a single product
- Use **Warehouse** to see movements in a single storage location
- Use **Voucher No** to trace a specific Stock Entry's effect

The **Balance Qty** and **Balance Value** columns show the running total at each point in time, computed by SQL window functions — the same result as if you had summed every row manually.

---

### Running the Stock Balance Report

Go to **Reports → Stock Balance**. The **As On Date** filter is required.

- Leave Item and Warehouse empty for a full inventory snapshot
- Select a **group warehouse** to see stock consolidated across all its children
- Select a **leaf warehouse** to see stock in that specific location only

The report excludes items with a net balance of zero (items that entered and left completely).

---

## 9. Project Structure

```
x_electronics_wms/
├── pyproject.toml
├── README.md
└── x_electronics_wms/
    ├── hooks.py                                   ← Frappe app hooks
    ├── modules.txt                                ← Module registration
    │
    └── x_electronics_warehouse_management_system/ ← Main module
        │
        ├── utils/
        │   ├── __init__.py
        │   └── test_helpers.py                    ← Shared test factory functions
        │
        ├── doctype/
        │   │
        │   ├── warehouse/
        │   │   ├── warehouse.json                 ← DocType definition
        │   │   ├── warehouse.py                   ← Controller (empty — Frappe handles tree)
        │   │   └── test_warehouse.py              ← Warehouse tests
        │   │
        │   ├── item/
        │   │   ├── item.json
        │   │   ├── item.py
        │   │   └── test_item.py                   ← Item tests
        │   │
        │   ├── stock_entry/
        │   │   ├── stock_entry.json
        │   │   ├── stock_entry.py                 ← Main controller (all business logic)
        │   │   ├── stock_entry.js                 ← Frontend helpers (optional)
        │   │   └── test_stock_entry.py            ← Validation, logic, valuation, cancellation tests
        │   │
        │   ├── stock_entry_detail/
        │   │   ├── stock_entry_detail.json
        │   │   └── stock_entry_detail.py
        │   │
        │   └── stock_ledger_entry/
        │       ├── stock_ledger_entry.json
        │       ├── stock_ledger_entry.py
        │       └── test_stock_ledger_entry.py     ← Immutability + report tests
        │
        └── report/
            │
            ├── stock_ledger/
            │   ├── stock_ledger.json              ← Report registration
            │   ├── stock_ledger.py                ← SQL query with window functions
            │   └── stock_ledger.js                ← Filter definitions
            │
            └── stock_balance/
                ├── stock_balance.json
                ├── stock_balance.py               ← SQL query with tree consolidation
                └── stock_balance.js
```

---

## 10. Design Decisions

### Stateless ledger — why no stored balance?

The most important design decision in this system is the absence of any stored balance field. Here is why:

**The problem with stored balances** — If you store `qty_after_transaction` on every SLE (as ERPNext does), the value of each row depends on the value of the row before it. This creates a chain of dependencies. When a single entry is corrupted, amended, or inserted out of sequence, every subsequent entry for that item+warehouse is wrong. Fixing this requires a full revaluation job that rewrites the `qty_after_transaction` of every SLE in order — a process that can take hours on large datasets and must be run while the system is idle.

**The stateless solution** — When every balance is `SUM(qty_change)`, there are no chains of dependency. Each SLE is independent. A corrupted entry can be cancelled and reissued without affecting any other entry. A revaluation job is never needed. Balances are always exactly correct because they are computed from the raw data, not from a cached intermediate result.

**The cost** — `SUM()` queries are slightly more expensive than reading a single stored value. In practice, with a proper index on `(item, warehouse, docstatus)`, these queries complete in single-digit milliseconds even on ledgers with hundreds of thousands of entries. The correctness guarantee is worth far more than the marginal query cost.

---

### Moving average valuation — why not FIFO?

Moving average (also called Weighted Average Cost or WAC) was chosen over FIFO for the following reasons:

**Simplicity** — Moving average requires one SQL query to compute the current state. FIFO requires tracking individual batches and consuming them in order, which requires substantially more complex logic and more storage.

**Correctness under cancellation** — With FIFO, cancelling an old receipt can invalidate the entire batch queue. With moving average, cancellation simply removes the entry's qty and value contribution from the running sum — no reordering required.

**Industry suitability** — For electronics distribution, where items from multiple suppliers and purchase orders are interchangeable, moving average is the standard and the most accurate representation of true inventory cost.

---

### Why is the Stock Ledger Entry Read Only?

The SLE is the system's source of truth. If users could edit SLEs directly, they could change the historical record — making the ledger meaningless as an audit trail. Corrections are made by cancelling the originating Stock Entry and creating a new one. This means every change is visible in the ledger as a cancellation event followed by a new entry, which is exactly the level of traceability a warehouse system should provide.

---

### Why Frappe nested sets for the warehouse tree?

Frappe's tree DocType stores `lft` and `rgt` boundary values on every node (nested set model). Finding all descendants of a node requires a single SQL range query:

```sql
WHERE lft >= :lft AND rgt <= :rgt AND is_group = 0
```

The alternative — recursive queries or application-level tree walking — requires either a database that supports recursive CTEs or multiple round trips to the database. The nested set approach is O(1) queries regardless of tree depth, at the cost of slightly more expensive writes (inserts and moves must update `lft`/`rgt` for sibling nodes). For a warehouse tree that changes rarely and is read constantly in reports, this is the right tradeoff.

---

## 11. Contributing

### Branch naming

| Branch type | Pattern | Example |
|---|---|---|
| Feature | `feature/description` | `feature/batch-tracking` |
| Bug fix | `fix/description` | `fix/negative-stock-validation` |
| Report | `feature/report-name` | `feature/report-aging-stock` |

### Coding standards

**Python:**
- Follow PEP 8
- All controller methods must have docstrings explaining what they do and why
- Never store derived state — if a value can be computed from the ledger, compute it
- All database queries must filter `docstatus = 1` unless intentionally including cancelled records
- Never write raw SQL in test files — use the helper functions in `test_helpers.py`

**Tests:**
- Every new feature must have tests before the feature is merged
- Every test must clean up after itself in `tearDown`
- All test record names must start with `_Test` — this is the convention that makes them identifiable in the database
- Tests must not depend on each other — each test must be runnable in isolation
- No test should take more than 2 seconds — if a test is slow, it is doing too much

**Commit messages:**
- Use the imperative mood: "Add batch tracking" not "Added batch tracking"
- Reference the layer being changed: `[validation] Reject negative rate on Receipt`
- Never commit a failing test

### Adding a new Stock Entry type

If a new transaction type is needed (e.g. `Return`, `Adjustment`):

1. Add the new value to the `stock_entry_type` Select field in the DocType
2. Add a validation block for it in `StockEntry.validate_items()`
3. Add ledger creation logic for it in `StockEntry.make_ledger_entries()` — the method already handles `t_warehouse` and `s_warehouse` separately, so most types only need warehouse validation rules
4. Add a test class for the new type in `test_stock_entry.py` covering validation and business logic
5. Update this README

### Adding a new report

1. Create the Report record in Desk (Report Type: Script Report, Reference DocType: Stock Ledger Entry)
2. Add `report_name.py` and `report_name.js` to the auto-created folder under `report/`
3. Add a test class in `test_stock_ledger_entry.py` with at least: a test that the report returns rows, a test that the key aggregate column is correct, and a test that date filters work correctly
4. Document the report in the [Reports](#5-reports) section of this README

---

## Appendix — Key SQL Patterns

### Current balance for an item in a warehouse

```sql
SELECT
    COALESCE(SUM(qty_change), 0)  AS qty,
    COALESCE(SUM(stock_value), 0) AS value
FROM `tabStock Ledger Entry`
WHERE
    item      = 'ITEM-001'
    AND warehouse = 'Main Store'
    AND docstatus = 1
```

### Current moving average rate

```sql
SELECT
    COALESCE(SUM(stock_value), 0) / NULLIF(COALESCE(SUM(qty_change), 0), 0) AS rate
FROM `tabStock Ledger Entry`
WHERE
    item      = 'ITEM-001'
    AND warehouse = 'Main Store'
    AND docstatus = 1
```

### All leaf warehouses under a group

```sql
SELECT name
FROM `tabWarehouse`
WHERE
    lft      >= (SELECT lft FROM `tabWarehouse` WHERE name = 'Nairobi')
    AND rgt  <= (SELECT rgt FROM `tabWarehouse` WHERE name = 'Nairobi')
    AND is_group = 0
```

### Running balance (window function)

```sql
SELECT
    posting_datetime,
    qty_change,
    SUM(qty_change) OVER (
        PARTITION BY item, warehouse
        ORDER BY posting_datetime, creation
        ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
    ) AS balance_qty
FROM `tabStock Ledger Entry`
WHERE docstatus = 1
```

---

*Built with [Frappe Framework](https://frappeframework.com) · X Electronics · 2025*
