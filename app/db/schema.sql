-- ChaseBase SQLite Schema (per-project database)

CREATE TABLE IF NOT EXISTS materials (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    po_number               TEXT NOT NULL,
    item_no                 TEXT NOT NULL,
    wbs_element             TEXT,
    project_no              TEXT,
    station_no              TEXT,
    part_no                 TEXT,
    description             TEXT,
    quantity                REAL,
    unit                    TEXT,
    supplier                TEXT,
    purchasing_group        TEXT,
    order_date              DATE,
    original_eta            DATE,
    current_eta             DATE,
    current_eta_source      TEXT,
    supplier_eta            DATE,
    supplier_eta_source     TEXT,
    supplier_feedback_time  DATETIME,
    supplier_remarks        TEXT,
    supplier_remarks_source TEXT,
    buyer_name              TEXT,
    buyer_email             TEXT,
    status                  TEXT DEFAULT 'open',
    is_focus                BOOLEAN DEFAULT 0,
    focus_reason            TEXT,
    chase_count             INTEGER DEFAULT 0,
    last_chased_at          DATETIME,
    last_feedback_chase_count INTEGER,
    escalation_flag         BOOLEAN DEFAULT 0,
    plant                   TEXT,
    supplier_code           TEXT,
    statical_delivery_date  DATE,
    manufacturer            TEXT,
    manufacturer_part_no    TEXT,
    open_quantity_gr        REAL,
    net_order_price         REAL,
    currency                TEXT,
    net_order_value         REAL,
    position_text1          TEXT,
    position_text2          TEXT,
    extra_json              TEXT,
    created_at              DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at              DATETIME DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(po_number, item_no)
);

CREATE TABLE IF NOT EXISTS field_updates (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    material_id   INTEGER NOT NULL REFERENCES materials(id),
    field_name    TEXT NOT NULL,
    old_value     TEXT,
    new_value     TEXT,
    source        TEXT NOT NULL,
    source_ref    TEXT,
    operator      TEXT,
    confirmed     BOOLEAN DEFAULT 1,
    timestamp     DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS chase_log (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    material_ids_json TEXT NOT NULL,
    to_address        TEXT,
    cc                TEXT,
    subject           TEXT,
    body              TEXT,
    method            TEXT,
    outlook_entry_id  TEXT,
    sent_at           DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS inbound_emails (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    outlook_entry_id    TEXT UNIQUE,
    from_address        TEXT,
    subject             TEXT,
    body                TEXT,
    received_at         DATETIME,
    parsed_marker       TEXT,
    matched_material_id INTEGER REFERENCES materials(id),
    llm_extracted_json  TEXT,
    status              TEXT DEFAULT 'new',
    confirmed_at        DATETIME,
    operator_decision   TEXT
);

CREATE TABLE IF NOT EXISTS imports (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    file_path    TEXT,
    file_hash    TEXT,
    rows_added   INTEGER,
    rows_updated INTEGER,
    rows_skipped INTEGER,
    errors_json  TEXT,
    imported_at  DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_mat_po_item  ON materials(po_number, item_no);
CREATE INDEX IF NOT EXISTS idx_mat_buyer    ON materials(buyer_email);
CREATE INDEX IF NOT EXISTS idx_mat_focus    ON materials(is_focus);
CREATE INDEX IF NOT EXISTS idx_mat_status   ON materials(status);
CREATE INDEX IF NOT EXISTS idx_mat_project  ON materials(project_no);
CREATE INDEX IF NOT EXISTS idx_mat_station  ON materials(station_no);
CREATE INDEX IF NOT EXISTS idx_mat_pg       ON materials(purchasing_group);

CREATE TABLE IF NOT EXISTS project_settings (
    key        TEXT PRIMARY KEY,
    value      TEXT,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

-- 时间节点表（Dashboard 交期预测用）
CREATE TABLE IF NOT EXISTS time_nodes (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    label       TEXT NOT NULL,
    node_date   DATE NOT NULL,
    color       TEXT DEFAULT '#2563eb',
    sort_order  INTEGER DEFAULT 0,
    created_at  DATETIME DEFAULT CURRENT_TIMESTAMP
);
