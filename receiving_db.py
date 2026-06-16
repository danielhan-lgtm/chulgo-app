import sqlite3
import os

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "receiving.db")


def _conn():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with _conn() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS receiving_records (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                put_sno TEXT UNIQUE NOT NULL,
                put_depot_nm TEXT,
                vendor_nm TEXT,
                put_req_dt TEXT,
                put_compt_dtm TEXT,
                put_type_nm TEXT,
                item_cnt INTEGER,
                tot_put_qty INTEGER,
                raw_data TEXT,
                status TEXT DEFAULT 'pending',
                boxhero_tx_id INTEGER,
                approved_at TEXT,
                created_at TEXT DEFAULT (datetime('now', 'localtime'))
            );
            CREATE TABLE IF NOT EXISTS receiving_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                put_sno TEXT NOT NULL,
                prod_cd TEXT,
                sale_prod_nm TEXT,
                put_qty INTEGER,
                put_detail_sno TEXT,
                raw_data TEXT,
                FOREIGN KEY (put_sno) REFERENCES receiving_records(put_sno)
            );
            CREATE TABLE IF NOT EXISTS product_mapping (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ourbox_prod_cd TEXT UNIQUE NOT NULL,
                ourbox_prod_nm TEXT,
                boxhero_item_id INTEGER NOT NULL,
                boxhero_item_nm TEXT,
                boxhero_sku TEXT,
                created_at TEXT DEFAULT (datetime('now', 'localtime'))
            );
            CREATE TABLE IF NOT EXISTS name_mapping (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ob_name TEXT NOT NULL,
                bh_sku TEXT NOT NULL,
                bh_name TEXT,
                match_score REAL DEFAULT 0,
                match_method TEXT DEFAULT 'manual',
                confirmed INTEGER DEFAULT 0,
                created_at TEXT DEFAULT (datetime('now', 'localtime')),
                UNIQUE(ob_name, bh_sku)
            );
            CREATE INDEX IF NOT EXISTS idx_name_mapping_ob ON name_mapping(ob_name);
            CREATE INDEX IF NOT EXISTS idx_name_mapping_bh ON name_mapping(bh_sku);

            CREATE TABLE IF NOT EXISTS channel_mapping (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ob_channel TEXT NOT NULL,
                bh_keyword TEXT NOT NULL,
                confirmed INTEGER DEFAULT 1,
                created_at TEXT DEFAULT (datetime('now', 'localtime')),
                UNIQUE(ob_channel, bh_keyword)
            );
            CREATE INDEX IF NOT EXISTS idx_channel_mapping_ob ON channel_mapping(ob_channel);
            CREATE INDEX IF NOT EXISTS idx_channel_mapping_bh ON channel_mapping(bh_keyword);

            CREATE TABLE IF NOT EXISTS set_bom (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                set_sku TEXT NOT NULL,
                set_name TEXT DEFAULT '',
                component_sku TEXT NOT NULL,
                component_name TEXT DEFAULT '',
                qty_per_set REAL NOT NULL DEFAULT 1,
                note TEXT DEFAULT '',
                created_at TEXT DEFAULT (datetime('now', 'localtime')),
                UNIQUE(set_sku, component_sku)
            );
            CREATE INDEX IF NOT EXISTS idx_set_bom_set ON set_bom(set_sku);
            CREATE INDEX IF NOT EXISTS idx_set_bom_comp ON set_bom(component_sku);

            CREATE TABLE IF NOT EXISTS matched_pairs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                sku TEXT NOT NULL,
                bh_name TEXT,
                ob_name TEXT,
                bh_date TEXT NOT NULL,
                ob_date TEXT,
                bh_qty INTEGER NOT NULL,
                ob_qty INTEGER NOT NULL,
                qty_diff INTEGER DEFAULT 0,
                status TEXT,
                ob_put_sno TEXT,
                match_method TEXT,
                from_date TEXT,
                to_date TEXT,
                confirmed_at TEXT DEFAULT (datetime('now', 'localtime'))
            );
            CREATE INDEX IF NOT EXISTS idx_matched_pairs_sku ON matched_pairs(sku, bh_date);

            CREATE TABLE IF NOT EXISTS stock_reports (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                report_date TEXT NOT NULL,
                location_ids TEXT DEFAULT '',
                total INTEGER DEFAULT 0,
                diff_count INTEGER DEFAULT 0,
                need_trace_count INTEGER DEFAULT 0,
                ok_count INTEGER DEFAULT 0,
                summary_json TEXT,
                rows_json TEXT,
                created_at TEXT DEFAULT (datetime('now', 'localtime')),
                UNIQUE(report_date, location_ids)
            );
            CREATE INDEX IF NOT EXISTS idx_stock_reports_date ON stock_reports(report_date);
        """)
        _migrate_name_mapping_many_to_many(conn)
        _migrate_matched_pairs_ob_name(conn)


def _migrate_matched_pairs_ob_name(conn):
    """matched_pairs 테이블에 ob_name 컬럼 없으면 추가."""
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='matched_pairs'"
    ).fetchone()
    if not row or not row["sql"]:
        return
    if "ob_name" not in row["sql"]:
        try:
            conn.execute("ALTER TABLE matched_pairs ADD COLUMN ob_name TEXT")
        except Exception:
            pass


def _migrate_name_mapping_many_to_many(conn):
    """구버전 name_mapping(ob_name UNIQUE) → 다대다(UNIQUE(ob_name,bh_sku)) 마이그레이션.
    기존 테이블 DDL에 'ob_name TEXT UNIQUE'가 있으면 데이터 보존하며 재생성."""
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='name_mapping'"
    ).fetchone()
    if not row or not row["sql"]:
        return
    ddl = row["sql"]
    # 이미 신규 스키마(복합 UNIQUE)면 스킵
    if "UNIQUE(ob_name, bh_sku)" in ddl or "UNIQUE(ob_name,bh_sku)" in ddl:
        return
    # 구버전: ob_name 단독 UNIQUE → 재생성
    if "ob_name TEXT UNIQUE" in ddl or "ob_name TEXT  UNIQUE" in ddl:
        conn.executescript("""
            ALTER TABLE name_mapping RENAME TO name_mapping_old;
            CREATE TABLE name_mapping (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ob_name TEXT NOT NULL,
                bh_sku TEXT NOT NULL,
                bh_name TEXT,
                match_score REAL DEFAULT 0,
                match_method TEXT DEFAULT 'manual',
                confirmed INTEGER DEFAULT 0,
                created_at TEXT DEFAULT (datetime('now', 'localtime')),
                UNIQUE(ob_name, bh_sku)
            );
            INSERT OR IGNORE INTO name_mapping
                (ob_name, bh_sku, bh_name, match_score, match_method, confirmed, created_at)
                SELECT ob_name, bh_sku, bh_name, match_score, match_method, confirmed, created_at
                FROM name_mapping_old;
            DROP TABLE name_mapping_old;
            CREATE INDEX IF NOT EXISTS idx_name_mapping_ob ON name_mapping(ob_name);
            CREATE INDEX IF NOT EXISTS idx_name_mapping_bh ON name_mapping(bh_sku);
        """)


def insert_record(record: dict):
    with _conn() as conn:
        conn.execute("""
            INSERT OR IGNORE INTO receiving_records
                (put_sno, put_depot_nm, vendor_nm, put_req_dt, put_compt_dtm,
                 put_type_nm, item_cnt, tot_put_qty, raw_data, status)
            VALUES
                (:put_sno, :put_depot_nm, :vendor_nm, :put_req_dt, :put_compt_dtm,
                 :put_type_nm, :item_cnt, :tot_put_qty, :raw_data, 'pending')
        """, record)


def insert_items(put_sno: str, items: list):
    with _conn() as conn:
        conn.executemany("""
            INSERT OR IGNORE INTO receiving_items
                (put_sno, prod_cd, sale_prod_nm, put_qty, put_detail_sno, raw_data)
            VALUES (:put_sno, :prod_cd, :sale_prod_nm, :put_qty, :put_detail_sno, :raw_data)
        """, items)


def get_all() -> list:
    with _conn() as conn:
        rows = conn.execute(
            "SELECT * FROM receiving_records ORDER BY created_at DESC LIMIT 200"
        ).fetchall()
        return [dict(r) for r in rows]


def get_items(put_sno: str) -> list:
    with _conn() as conn:
        rows = conn.execute("""
            SELECT ri.*, pm.boxhero_item_id, pm.boxhero_item_nm, pm.boxhero_sku
            FROM receiving_items ri
            LEFT JOIN product_mapping pm ON ri.prod_cd = pm.ourbox_prod_cd
            WHERE ri.put_sno = ?
        """, (put_sno,)).fetchall()
        return [dict(r) for r in rows]


def update_status(put_sno: str, status: str, boxhero_tx_id=None):
    with _conn() as conn:
        conn.execute("""
            UPDATE receiving_records
            SET status = ?, boxhero_tx_id = ?, approved_at = datetime('now', 'localtime')
            WHERE put_sno = ?
        """, (status, boxhero_tx_id, put_sno))


def exists(put_sno: str) -> bool:
    with _conn() as conn:
        row = conn.execute(
            "SELECT id FROM receiving_records WHERE put_sno = ?", (put_sno,)
        ).fetchone()
        return row is not None


def upsert_mapping(mapping: dict):
    with _conn() as conn:
        conn.execute("""
            INSERT INTO product_mapping
                (ourbox_prod_cd, ourbox_prod_nm, boxhero_item_id, boxhero_item_nm, boxhero_sku)
            VALUES
                (:ourbox_prod_cd, :ourbox_prod_nm, :boxhero_item_id, :boxhero_item_nm, :boxhero_sku)
            ON CONFLICT(ourbox_prod_cd) DO UPDATE SET
                boxhero_item_id = excluded.boxhero_item_id,
                boxhero_item_nm = excluded.boxhero_item_nm,
                boxhero_sku = excluded.boxhero_sku
        """, mapping)


def get_mappings() -> list:
    with _conn() as conn:
        rows = conn.execute(
            "SELECT * FROM product_mapping ORDER BY ourbox_prod_nm"
        ).fetchall()
        return [dict(r) for r in rows]


def delete_mapping(ourbox_prod_cd: str):
    with _conn() as conn:
        conn.execute(
            "DELETE FROM product_mapping WHERE ourbox_prod_cd = ?", (ourbox_prod_cd,)
        )


# ── 이름 기반 매핑 (name_mapping) ────────────────────────────────

def upsert_name_mapping(ob_name: str, bh_sku: str, bh_name: str = "",
                         score: float = 0.0, method: str = "manual", confirmed: int = 0):
    """(ob_name, bh_sku) 쌍 단위 저장. 다대다 연결 허용."""
    with _conn() as conn:
        conn.execute("""
            INSERT INTO name_mapping (ob_name, bh_sku, bh_name, match_score, match_method, confirmed)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(ob_name, bh_sku) DO UPDATE SET
                bh_name = excluded.bh_name,
                match_score = excluded.match_score,
                match_method = excluded.match_method,
                confirmed = excluded.confirmed
        """, (ob_name, bh_sku, bh_name, score, method, confirmed))


def get_name_mappings() -> list:
    with _conn() as conn:
        rows = conn.execute(
            "SELECT * FROM name_mapping ORDER BY confirmed DESC, match_score DESC, ob_name"
        ).fetchall()
        return [dict(r) for r in rows]


def confirm_name_mapping_by_id(mapping_id: int):
    with _conn() as conn:
        conn.execute("UPDATE name_mapping SET confirmed=1 WHERE id=?", (mapping_id,))


def delete_name_mapping_by_id(mapping_id: int):
    with _conn() as conn:
        conn.execute("DELETE FROM name_mapping WHERE id=?", (mapping_id,))


def delete_name_mapping_pair(ob_name: str, bh_sku: str):
    with _conn() as conn:
        conn.execute("DELETE FROM name_mapping WHERE ob_name=? AND bh_sku=?", (ob_name, bh_sku))


# 하위호환 — 기존 호출부(ob_name 단위) 유지
def confirm_name_mapping(ob_name: str):
    with _conn() as conn:
        conn.execute("UPDATE name_mapping SET confirmed=1 WHERE ob_name=?", (ob_name,))


def delete_name_mapping(ob_name: str):
    with _conn() as conn:
        conn.execute("DELETE FROM name_mapping WHERE ob_name=?", (ob_name,))


def get_name_mapping_pairs() -> list:
    """확정 우선의 (ob_name, bh_sku, bh_name) 쌍 목록. 그룹 합산용."""
    return [
        {"ob_name": r["ob_name"], "bh_sku": r["bh_sku"], "bh_name": r.get("bh_name", "")}
        for r in get_name_mappings() if r["ob_name"] and r["bh_sku"]
    ]


def get_name_mapping_dict() -> dict:
    """ob_name → bh_sku (하위호환, 1:1 가정). 다대다는 get_name_mapping_pairs 사용."""
    rows = get_name_mappings()
    return {r["ob_name"]: r["bh_sku"] for r in rows if r["bh_sku"]}


def get_product_mapping_pairs() -> list:
    """(ob_prod_cd, ob_name, bh_sku, bh_name) 쌍. boxhero_sku 있는 행만. 그룹 합산용.

    product_mapping은 OB 상품코드(prod_cd)↔BH SKU 직접 매핑이라
    name_mapping(상품명 기반)보다 정확도가 높다.
    """
    return [
        {"ob_prod_cd": str(r["ourbox_prod_cd"]).strip(),
         "ob_name": (r.get("ourbox_prod_nm", "") or "").strip(),
         "bh_sku": str(r["boxhero_sku"]).strip(),
         "bh_name": (r.get("boxhero_item_nm", "") or "").strip()}
        for r in get_mappings()
        if r.get("ourbox_prod_cd") and r.get("boxhero_sku")
    ]


# ── 채널 매핑 (channel_mapping) ──────────────────────────────────

def upsert_channel_mapping(ob_channel: str, bh_keyword: str, confirmed: int = 1):
    """(ob_channel, bh_keyword) 쌍 저장. 다대다 허용."""
    with _conn() as conn:
        conn.execute("""
            INSERT INTO channel_mapping (ob_channel, bh_keyword, confirmed)
            VALUES (?, ?, ?)
            ON CONFLICT(ob_channel, bh_keyword) DO UPDATE SET confirmed = excluded.confirmed
        """, (ob_channel, bh_keyword, confirmed))


def get_channel_mappings() -> list:
    with _conn() as conn:
        rows = conn.execute(
            "SELECT * FROM channel_mapping ORDER BY confirmed DESC, ob_channel"
        ).fetchall()
        return [dict(r) for r in rows]


def get_channel_mapping_pairs() -> list:
    return [
        {"ob_channel": r["ob_channel"], "bh_keyword": r["bh_keyword"]}
        for r in get_channel_mappings() if r["ob_channel"] and r["bh_keyword"]
    ]


def delete_channel_mapping_pair(ob_channel: str, bh_keyword: str):
    with _conn() as conn:
        conn.execute("DELETE FROM channel_mapping WHERE ob_channel=? AND bh_keyword=?",
                     (ob_channel, bh_keyword))


def delete_channel_mapping_by_id(mapping_id: int):
    with _conn() as conn:
        conn.execute("DELETE FROM channel_mapping WHERE id=?", (mapping_id,))


# ── stock_reports CRUD (주간 재고 비교 리포트) ────────────────────────────

def save_stock_report(report_date: str, location_ids: str, result: dict):
    """재고 비교 스냅샷을 저장 (같은 날짜·위치는 덮어쓰기)."""
    import json as _json
    summary = {
        "total": result.get("total", 0),
        "ok_count": result.get("ok_count", 0),
        "diff_count": result.get("diff_count", 0),
        "need_trace_count": result.get("need_trace_count", 0),
        "only_bh": result.get("only_bh", 0),
        "only_ob": result.get("only_ob", 0),
    }
    with _conn() as conn:
        conn.execute(
            "DELETE FROM stock_reports WHERE report_date=? AND location_ids=?",
            (report_date, location_ids)
        )
        conn.execute("""
            INSERT INTO stock_reports
                (report_date, location_ids, total, diff_count, need_trace_count, ok_count, summary_json, rows_json)
            VALUES (?,?,?,?,?,?,?,?)
        """, (
            report_date, location_ids,
            summary["total"], summary["diff_count"], summary["need_trace_count"], summary["ok_count"],
            _json.dumps(summary, ensure_ascii=False),
            _json.dumps(result.get("rows", []), ensure_ascii=False),
        ))


def list_stock_reports(limit: int = 52) -> list:
    """저장된 주간 리포트 목록 (최신순, rows 제외 요약만)."""
    import json as _json
    with _conn() as conn:
        rows = conn.execute(
            """SELECT id, report_date, location_ids, total, diff_count, need_trace_count, ok_count, summary_json, created_at
               FROM stock_reports ORDER BY report_date DESC, id DESC LIMIT ?""",
            (limit,)
        ).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            try: d["summary"] = _json.loads(d.pop("summary_json") or "{}")
            except Exception: d["summary"] = {}
            out.append(d)
        return out


def get_stock_report(report_id: int) -> dict:
    """특정 리포트 상세 (rows 포함)."""
    import json as _json
    with _conn() as conn:
        r = conn.execute("SELECT * FROM stock_reports WHERE id=?", (report_id,)).fetchone()
        if not r:
            return {}
        d = dict(r)
        try: d["rows"] = _json.loads(d.pop("rows_json") or "[]")
        except Exception: d["rows"] = []
        try: d["summary"] = _json.loads(d.pop("summary_json") or "{}")
        except Exception: d["summary"] = {}
        return d


# ── matched_pairs CRUD ─────────────────────────────────────────────────────

def save_matched_pairs(pairs: list, from_date: str = "", to_date: str = ""):
    """매칭 확정 결과를 저장. 동일 기간 기존 데이터를 먼저 삭제 후 재저장."""
    with _conn() as conn:
        if from_date and to_date:
            conn.execute(
                "DELETE FROM matched_pairs WHERE from_date=? AND to_date=?",
                (from_date, to_date)
            )
        for p in pairs:
            if not str(p.get("sku") or "").strip():
                continue  # sku 없으면 저장 스킵 (compare에서 오매칭 방지)
            conn.execute("""
                INSERT INTO matched_pairs
                    (sku, bh_name, ob_name, bh_date, ob_date, bh_qty, ob_qty, qty_diff,
                     status, ob_put_sno, match_method, from_date, to_date)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
            """, (
                p.get("sku", ""), p.get("bh_name", ""), p.get("ob_name", ""),
                p.get("bh_date", ""), p.get("ob_date", ""),
                int(p.get("bh_qty", 0)), int(p.get("ob_qty", 0)),
                int(p.get("qty_diff", 0)), p.get("status", ""),
                p.get("ob_put_sno", ""), p.get("match_method", ""),
                from_date, to_date,
            ))


def get_matched_pairs(from_date: str, to_date: str) -> list:
    """기간 ±14일 범위 확정 매칭 쌍 조회.
    비교조회 날짜와 매칭확정 날짜가 다를 수 있으므로 ±14일 확장 적용."""
    from datetime import datetime as _dt, timedelta as _td
    try:
        fd = (_dt.strptime(from_date, "%Y-%m-%d") - _td(days=14)).strftime("%Y-%m-%d")
        td = (_dt.strptime(to_date, "%Y-%m-%d") + _td(days=14)).strftime("%Y-%m-%d")
    except Exception:
        fd, td = from_date, to_date
    with _conn() as conn:
        rows = conn.execute(
            """SELECT * FROM matched_pairs
               WHERE bh_date >= ? AND bh_date <= ?
               ORDER BY bh_date, sku""",
            (fd, td)
        ).fetchall()
        return [dict(r) for r in rows]


def clear_matched_pairs(from_date: str, to_date: str):
    with _conn() as conn:
        conn.execute(
            "DELETE FROM matched_pairs WHERE from_date=? AND to_date=?",
            (from_date, to_date)
        )


# ── set_bom CRUD ──────────────────────────────────────────────────────────────

def get_set_boms() -> list:
    """세트 BOM 구성표 전체 조회."""
    with _conn() as conn:
        rows = conn.execute(
            "SELECT * FROM set_bom ORDER BY set_name, component_name"
        ).fetchall()
        return [dict(r) for r in rows]


def upsert_set_bom(set_sku: str, set_name: str, component_sku: str,
                   component_name: str, qty_per_set: float, note: str = ""):
    """세트 BOM 저장 (중복 시 업데이트)."""
    with _conn() as conn:
        conn.execute(
            """INSERT INTO set_bom (set_sku, set_name, component_sku, component_name, qty_per_set, note)
               VALUES (?, ?, ?, ?, ?, ?)
               ON CONFLICT(set_sku, component_sku) DO UPDATE SET
                   set_name=excluded.set_name,
                   component_name=excluded.component_name,
                   qty_per_set=excluded.qty_per_set,
                   note=excluded.note""",
            (set_sku, set_name, component_sku, component_name, qty_per_set, note)
        )


def delete_set_bom(bom_id: int):
    """세트 BOM 항목 삭제."""
    with _conn() as conn:
        conn.execute("DELETE FROM set_bom WHERE id=?", (bom_id,))


# 앱 시작 시 DB 초기화
init_db()
