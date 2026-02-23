"""
blackroad-product-catalog: Product catalog with SKUs, pricing, and inventory management.
SQLite persistence at ~/.blackroad/product-catalog.db
"""

from __future__ import annotations

import argparse
import csv
import io
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import List, Optional

# ── ANSI colours ─────────────────────────────────────────────────────────────
GREEN   = "\033[92m"
CYAN    = "\033[96m"
YELLOW  = "\033[93m"
RED     = "\033[91m"
MAGENTA = "\033[95m"
BOLD    = "\033[1m"
DIM     = "\033[2m"
RESET   = "\033[0m"

DB_PATH = Path.home() / ".blackroad" / "product-catalog.db"


# ── Models ────────────────────────────────────────────────────────────────────
@dataclass
class Product:
    sku: str
    name: str
    category: str
    price: float
    cost: float = 0.0
    inventory: int = 0
    unit: str = "ea"
    description: str = ""
    active: bool = True
    id: Optional[int] = None
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())
    updated_at: str = field(default_factory=lambda: datetime.now().isoformat())

    def margin_pct(self) -> float:
        if self.price <= 0:
            return 0.0
        return round((self.price - self.cost) / self.price * 100, 2)

    def inventory_status(self) -> str:
        if self.inventory <= 0:
            return "OUT_OF_STOCK"
        if self.inventory < 10:
            return "LOW_STOCK"
        return "IN_STOCK"

    def inventory_color(self) -> str:
        status = self.inventory_status()
        return {
            "OUT_OF_STOCK": RED,
            "LOW_STOCK":    YELLOW,
            "IN_STOCK":     GREEN,
        }.get(status, RESET)


# ── Core logic ────────────────────────────────────────────────────────────────
class ProductCatalog:
    """Manage product SKUs, pricing tiers, and inventory levels."""

    def __init__(self, db_path: Path = DB_PATH):
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS products (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    sku         TEXT UNIQUE NOT NULL,
                    name        TEXT NOT NULL,
                    category    TEXT NOT NULL,
                    price       REAL NOT NULL,
                    cost        REAL DEFAULT 0,
                    inventory   INTEGER DEFAULT 0,
                    unit        TEXT DEFAULT 'ea',
                    description TEXT DEFAULT '',
                    active      INTEGER DEFAULT 1,
                    created_at  TEXT NOT NULL,
                    updated_at  TEXT NOT NULL
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_products_sku ON products(sku)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_products_category ON products(category)")
            conn.commit()

    def add_product(self, sku: str, name: str, category: str,
                    price: float, cost: float = 0.0, inventory: int = 0,
                    unit: str = "ea", description: str = "") -> Product:
        p = Product(sku=sku.upper(), name=name, category=category,
                    price=price, cost=cost, inventory=inventory,
                    unit=unit, description=description)
        with self._connect() as conn:
            cur = conn.execute(
                """INSERT INTO products
                   (sku, name, category, price, cost, inventory, unit,
                    description, active, created_at, updated_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                (p.sku, p.name, p.category, p.price, p.cost, p.inventory,
                 p.unit, p.description, int(p.active), p.created_at, p.updated_at),
            )
            p.id = cur.lastrowid
            conn.commit()
        return p

    def list_products(self, category: Optional[str] = None,
                      active_only: bool = True, limit: int = 50) -> List[Product]:
        query = "SELECT * FROM products WHERE 1=1"
        params: list = []
        if active_only:
            query += " AND active = 1"
        if category:
            query += " AND category = ?"
            params.append(category)
        query += " ORDER BY category, name LIMIT ?"
        params.append(limit)
        with self._connect() as conn:
            rows = conn.execute(query, params).fetchall()
        return [self._row_to_product(r) for r in rows]

    def search(self, query: str) -> List[Product]:
        pattern = f"%{query}%"
        with self._connect() as conn:
            rows = conn.execute(
                """SELECT * FROM products
                   WHERE sku LIKE ? OR name LIKE ? OR category LIKE ? OR description LIKE ?
                   ORDER BY name""",
                (pattern, pattern, pattern, pattern),
            ).fetchall()
        return [self._row_to_product(r) for r in rows]

    def update_inventory(self, sku: str, delta: int) -> Optional[Product]:
        now = datetime.now().isoformat()
        with self._connect() as conn:
            conn.execute(
                "UPDATE products SET inventory = MAX(0, inventory + ?), updated_at = ? WHERE sku = ?",
                (delta, now, sku.upper()),
            )
            conn.commit()
            row = conn.execute("SELECT * FROM products WHERE sku = ?",
                               (sku.upper(),)).fetchone()
        return self._row_to_product(row) if row else None

    def export_csv(self, output_path: Optional[Path] = None) -> str:
        products = self.list_products(active_only=False, limit=100000)
        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow(["SKU", "Name", "Category", "Price", "Cost",
                         "Margin%", "Inventory", "Unit", "Status", "Active"])
        for p in products:
            writer.writerow([
                p.sku, p.name, p.category,
                f"{p.price:.2f}", f"{p.cost:.2f}", f"{p.margin_pct():.1f}",
                p.inventory, p.unit, p.inventory_status(), p.active,
            ])
        csv_content = buf.getvalue()
        if output_path:
            Path(output_path).write_text(csv_content)
        return csv_content

    def get_catalog_stats(self) -> dict:
        with self._connect() as conn:
            total = conn.execute("SELECT COUNT(*) FROM products WHERE active=1").fetchone()[0]
            out_of_stock = conn.execute(
                "SELECT COUNT(*) FROM products WHERE active=1 AND inventory=0"
            ).fetchone()[0]
            low_stock = conn.execute(
                "SELECT COUNT(*) FROM products WHERE active=1 AND inventory > 0 AND inventory < 10"
            ).fetchone()[0]
            categories = conn.execute(
                "SELECT category, COUNT(*) as cnt FROM products WHERE active=1 GROUP BY category"
            ).fetchall()
            value = conn.execute(
                "SELECT SUM(price * inventory) FROM products WHERE active=1"
            ).fetchone()[0] or 0
        return {
            "total": total,
            "out_of_stock": out_of_stock,
            "low_stock": low_stock,
            "in_stock": total - out_of_stock - low_stock,
            "inventory_value": round(value, 2),
            "categories": {r["category"]: r["cnt"] for r in categories},
        }

    @staticmethod
    def _row_to_product(row: sqlite3.Row) -> Product:
        return Product(
            id=row["id"], sku=row["sku"], name=row["name"],
            category=row["category"], price=row["price"], cost=row["cost"],
            inventory=row["inventory"], unit=row["unit"],
            description=row["description"], active=bool(row["active"]),
            created_at=row["created_at"], updated_at=row["updated_at"],
        )


# ── Terminal rendering ────────────────────────────────────────────────────────
def _print_header(title: str) -> None:
    print(f"\n{BOLD}{CYAN}{'─' * 70}{RESET}")
    print(f"{BOLD}{CYAN}  {title}{RESET}")
    print(f"{BOLD}{CYAN}{'─' * 70}{RESET}\n")


def _print_product(p: Product) -> None:
    inv_color = p.inventory_color()
    margin_color = GREEN if p.margin_pct() >= 30 else YELLOW if p.margin_pct() >= 10 else RED
    print(f"  {BOLD}{CYAN}{p.sku:<14}{RESET}  {GREEN}{p.name}{RESET}")
    print(f"  {'':14}  cat: {YELLOW}{p.category}{RESET}  "
          f"price: {BOLD}${p.price:.2f}{RESET}  "
          f"margin: {margin_color}{p.margin_pct():.1f}%{RESET}  "
          f"stock: {inv_color}{p.inventory} {p.unit}{RESET}  "
          f"[{inv_color}{p.inventory_status()}{RESET}]")


def _print_status(catalog: ProductCatalog) -> None:
    stats = catalog.get_catalog_stats()
    _print_header("🛍️  Product Catalog — Status")
    print(f"  {YELLOW}Active products  :{RESET}  {stats['total']}")
    print(f"  {GREEN}In stock         :{RESET}  {stats['in_stock']}")
    print(f"  {YELLOW}Low stock        :{RESET}  {stats['low_stock']}")
    print(f"  {RED}Out of stock     :{RESET}  {stats['out_of_stock']}")
    print(f"  {YELLOW}Inventory value  :{RESET}  ${stats['inventory_value']:,.2f}")
    if stats["categories"]:
        print(f"\n  {BOLD}Categories:{RESET}")
        for cat, cnt in sorted(stats["categories"].items()):
            bar = "█" * min(cnt, 25)
            print(f"    {cat:<20} {CYAN}{bar}{RESET} {cnt}")
    print()


# ── CLI ───────────────────────────────────────────────────────────────────────
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="product-catalog",
        description="BlackRoad Product Catalog — SKU & inventory manager",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_list = sub.add_parser("list", help="List products")
    p_list.add_argument("-c", "--category")
    p_list.add_argument("--all", dest="all_products", action="store_true")
    p_list.add_argument("-n", "--limit", type=int, default=30)

    p_add = sub.add_parser("add", help="Add a product")
    p_add.add_argument("sku")
    p_add.add_argument("name")
    p_add.add_argument("category")
    p_add.add_argument("price", type=float)
    p_add.add_argument("--cost", type=float, default=0.0)
    p_add.add_argument("--inventory", type=int, default=0)
    p_add.add_argument("--unit", default="ea")
    p_add.add_argument("--description", default="")

    p_inv = sub.add_parser("update", help="Adjust inventory")
    p_inv.add_argument("sku")
    p_inv.add_argument("delta", type=int, help="Positive to add, negative to subtract")

    p_search = sub.add_parser("search", help="Search products")
    p_search.add_argument("query")

    sub.add_parser("status", help="Catalog statistics")

    p_exp = sub.add_parser("export", help="Export catalog to CSV")
    p_exp.add_argument("-o", "--output", default="product_catalog.csv")

    return parser


def main(argv=None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)
    catalog = ProductCatalog()

    if args.command == "list":
        products = catalog.list_products(
            category=args.category,
            active_only=not args.all_products,
            limit=args.limit,
        )
        _print_header(f"🛍️  Products  ({len(products)} shown)")
        if not products:
            print(f"  {DIM}No products found.{RESET}\n")
        for p in products:
            _print_product(p)
            print()

    elif args.command == "add":
        p = catalog.add_product(
            args.sku, args.name, args.category, args.price,
            args.cost, args.inventory, args.unit, args.description,
        )
        print(f"\n{GREEN}✓ Product added:{RESET} [{p.sku}] {p.name}  "
              f"${p.price:.2f}  stock: {p.inventory}\n")

    elif args.command == "update":
        p = catalog.update_inventory(args.sku, args.delta)
        if p:
            direction = "+" if args.delta >= 0 else ""
            print(f"\n{GREEN}✓ {p.sku}{RESET} inventory: "
                  f"{direction}{args.delta} → {p.inventory} {p.unit}"
                  f"  [{p.inventory_color()}{p.inventory_status()}{RESET}]\n")
        else:
            print(f"\n{RED}✗ SKU '{args.sku}' not found{RESET}\n")

    elif args.command == "search":
        results = catalog.search(args.query)
        _print_header(f"🔍  Search: '{args.query}'  ({len(results)} results)")
        for p in results:
            _print_product(p)
            print()

    elif args.command == "status":
        _print_status(catalog)

    elif args.command == "export":
        catalog.export_csv(Path(args.output))
        print(f"\n{GREEN}✓ Exported to:{RESET} {args.output}\n")


if __name__ == "__main__":
    main()
