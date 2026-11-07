-- One record per CVE id. Each source keeps its latest row whole: nvd_json from NVD, kev_json from CISA KEV (NULL until it appears there).
CREATE TABLE cve_records (
    subject_id TEXT PRIMARY KEY,
    cve        TEXT NOT NULL UNIQUE,
    nvd_json   TEXT,
    kev_json   TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
