"""Entity registry — defines what QB entities the API supports and their capabilities."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class EntityDef:
    """Definition of a QuickBooks entity and its supported operations."""

    name: str  # QB name, e.g. "Customer"
    rest_path: str  # URL segment, e.g. "customers"
    supports_add: bool = False
    supports_mod: bool = False
    supports_query: bool = True
    supports_delete: bool = False
    is_transaction: bool = False  # True → TxnID + TxnDel; False → ListID + ListDel
    supports_iterator: bool = False  # qbXML DTD includes iterator attribute
    id_field: str = "ListID"
    description: str = ""
    # Transactions only. Whether the query takes RefNumberFilter (the `name`
    # parameter) and IncludeLineItems (get by id). TimeTracking has neither.
    has_ref_number: bool = True
    has_line_items: bool = True
    # Transactions only. The element the `entity_name` parameter becomes:
    # most transactions take EntityFilter (with sub-jobs); TimeTracking takes
    # TimeTrackingEntityFilter, which only matches an exact FullName.
    entity_filter: str = "EntityFilter"

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "path": f"/api/v1/{self.rest_path}",
            "id_field": self.id_field,
            "operations": {
                "list": self.supports_query,
                "get": self.supports_query,
                "create": self.supports_add,
                "update": self.supports_mod,
                "delete": self.supports_delete,
            },
            "is_transaction": self.is_transaction,
            "description": self.description,
        }


# ---------------------------------------------------------------------------
# Master registry
# ---------------------------------------------------------------------------

ENTITIES: dict[str, EntityDef] = {
    "Account": EntityDef(
        "Account",
        "accounts",
        supports_add=True,
        supports_mod=True,
        supports_query=True,
        supports_delete=True,
        description="Chart of accounts",
    ),
    "Customer": EntityDef(
        "Customer",
        "customers",
        supports_add=True,
        supports_mod=True,
        supports_query=True,
        supports_delete=True,
        supports_iterator=True,
        description="Customers and jobs",
    ),
    "Vendor": EntityDef(
        "Vendor",
        "vendors",
        supports_add=True,
        supports_mod=True,
        supports_query=True,
        supports_delete=True,
        supports_iterator=True,
        description="Vendors / suppliers",
    ),
    "Employee": EntityDef(
        "Employee",
        "employees",
        supports_add=True,
        supports_mod=True,
        supports_query=True,
        supports_delete=True,
        description="Employees",
    ),
    "Item": EntityDef(
        "Item",
        "items",
        supports_add=False,
        supports_mod=False,
        supports_query=True,
        supports_delete=False,
        supports_iterator=True,
        description="All item types (service, inventory, non-inventory, etc.)",
    ),
    "ItemService": EntityDef(
        "ItemService",
        "items/service",
        supports_add=True,
        supports_mod=True,
        supports_query=True,
        supports_delete=True,
        supports_iterator=True,
        description="Service items",
    ),
    "ItemInventory": EntityDef(
        "ItemInventory",
        "items/inventory",
        supports_add=True,
        supports_mod=True,
        supports_query=True,
        supports_delete=True,
        supports_iterator=True,
        description="Inventory items",
    ),
    "ItemNonInventory": EntityDef(
        "ItemNonInventory",
        "items/non-inventory",
        supports_add=True,
        supports_mod=True,
        supports_query=True,
        supports_delete=True,
        supports_iterator=True,
        description="Non-inventory items",
    ),
    "Invoice": EntityDef(
        "Invoice",
        "invoices",
        supports_add=True,
        supports_mod=True,
        supports_query=True,
        supports_delete=True,
        is_transaction=True,
        supports_iterator=True,
        id_field="TxnID",
        description="Sales invoices",
    ),
    "Bill": EntityDef(
        "Bill",
        "bills",
        supports_add=True,
        supports_mod=True,
        supports_query=True,
        supports_delete=True,
        is_transaction=True,
        supports_iterator=True,
        id_field="TxnID",
        description="Vendor bills",
    ),
    "ReceivePayment": EntityDef(
        "ReceivePayment",
        "payments",
        supports_add=True,
        supports_mod=True,
        supports_query=True,
        supports_delete=True,
        is_transaction=True,
        supports_iterator=True,
        id_field="TxnID",
        description="Customer payments received",
    ),
    "JournalEntry": EntityDef(
        "JournalEntry",
        "journal-entries",
        supports_add=True,
        supports_mod=True,
        supports_query=True,
        supports_delete=True,
        is_transaction=True,
        supports_iterator=True,
        id_field="TxnID",
        description="General journal entries",
    ),
    "Estimate": EntityDef(
        "Estimate",
        "estimates",
        supports_add=True,
        supports_mod=True,
        supports_query=True,
        supports_delete=True,
        is_transaction=True,
        supports_iterator=True,
        id_field="TxnID",
        description="Estimates / quotes",
    ),
    "SalesReceipt": EntityDef(
        "SalesReceipt",
        "sales-receipts",
        supports_add=True,
        supports_mod=True,
        supports_query=True,
        supports_delete=True,
        is_transaction=True,
        supports_iterator=True,
        id_field="TxnID",
        description="Sales receipts (immediate payment)",
    ),
    "CreditMemo": EntityDef(
        "CreditMemo",
        "credit-memos",
        supports_add=True,
        supports_mod=True,
        supports_query=True,
        supports_delete=True,
        is_transaction=True,
        supports_iterator=True,
        id_field="TxnID",
        description="Customer credit memos",
    ),
    "PurchaseOrder": EntityDef(
        "PurchaseOrder",
        "purchase-orders",
        supports_add=True,
        supports_mod=True,
        supports_query=True,
        supports_delete=True,
        is_transaction=True,
        supports_iterator=True,
        id_field="TxnID",
        description="Purchase orders",
    ),
    "Class": EntityDef(
        "Class",
        "classes",
        supports_add=True,
        supports_mod=True,
        supports_query=True,
        supports_delete=True,
        description="Tracking classes",
    ),
    "Terms": EntityDef(
        "Terms",
        "terms",
        supports_add=True,
        supports_mod=True,
        supports_query=True,
        supports_delete=True,
        description="Payment terms (Net 30, etc.)",
    ),
    "SalesRep": EntityDef(
        "SalesRep",
        "sales-reps",
        supports_add=True,
        supports_mod=True,
        supports_query=True,
        supports_delete=True,
        description="Sales representatives",
    ),
    "PaymentMethod": EntityDef(
        "PaymentMethod",
        "payment-methods",
        supports_add=True,
        supports_mod=False,
        supports_query=True,
        supports_delete=True,
        description="Payment methods (Check, Cash, etc.)",
    ),
    "Check": EntityDef(
        "Check",
        "checks",
        supports_add=True,
        supports_mod=True,
        supports_query=True,
        supports_delete=True,
        is_transaction=True,
        supports_iterator=True,
        id_field="TxnID",
        description="Written checks",
    ),
    "Deposit": EntityDef(
        "Deposit",
        "deposits",
        supports_add=True,
        supports_mod=True,
        supports_query=True,
        supports_delete=True,
        is_transaction=True,
        supports_iterator=True,
        id_field="TxnID",
        description="Bank deposits",
    ),
    "OtherName": EntityDef(
        "OtherName",
        "other-names",
        supports_add=True,
        supports_mod=True,
        supports_query=True,
        supports_delete=True,
        description="Other names (people and companies that aren't customers, vendors or employees)",
    ),
    "PayrollItemWage": EntityDef(
        "PayrollItemWage",
        "payroll-items/wage",
        supports_add=True,
        supports_mod=False,
        supports_query=True,
        supports_delete=True,
        description="Wage payroll items (hourly, salary, overtime...)",
    ),
    "TimeTracking": EntityDef(
        "TimeTracking",
        "time-tracking",
        supports_add=True,
        supports_mod=True,
        supports_query=True,
        supports_delete=True,
        is_transaction=True,
        id_field="TxnID",
        has_ref_number=False,
        has_line_items=False,
        entity_filter="TimeTrackingEntityFilter",
        description="Timesheet entries: a duration for an employee, vendor or other name on a date",
    ),
    "BillPaymentCheck": EntityDef(
        "BillPaymentCheck",
        "bill-payments",
        supports_add=True,
        supports_mod=False,
        supports_query=True,
        supports_delete=True,
        is_transaction=True,
        supports_iterator=True,
        id_field="TxnID",
        description="Bill payments by check",
    ),
}


def get_entity(name: str) -> EntityDef | None:
    """Look up entity definition by QB name (case-insensitive)."""
    return ENTITIES.get(name) or next(
        (e for e in ENTITIES.values() if e.name.lower() == name.lower()),
        None,
    )


def get_entity_by_path(path: str) -> EntityDef | None:
    """Look up entity definition by REST path segment."""
    return next(
        (e for e in ENTITIES.values() if e.rest_path == path),
        None,
    )
