"""
API: 系统设置 — 读写 .env，以及 PGR 配置
"""
from __future__ import annotations
from pathlib import Path
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
import yaml

router = APIRouter(prefix="/api/settings", tags=["settings"])

ENV_PATH = Path(".env")
PGR_PATH = Path(__file__).parent.parent.parent / "config" / "purchasing_group_mapping.yaml"


# ── .env 读写 ──────────────────────────────────────────────────────

def _read_env() -> dict[str, str]:
    if not ENV_PATH.exists():
        return {}
    result = {}
    for line in ENV_PATH.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, _, v = line.partition("=")
            result[k.strip()] = v.strip()
    return result


def _write_env(data: dict[str, str]):
    lines = [f"{k}={v}" for k, v in data.items()]
    ENV_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")


@router.get("")
def get_settings_api():
    env = _read_env()
    # 脱敏所有包含 KEY/SECRET/PASSWORD 的字段
    safe = {
        k: ("***" if any(s in k.upper() for s in ("KEY", "SECRET", "PASSWORD", "TOKEN")) else v)
        for k, v in env.items()
    }
    return safe


class EnvPatch(BaseModel):
    """通用 .env 更新：支持任意 key/value 对"""
    updates: dict[str, str]   # {"ANTHROPIC_API_KEY": "sk-...", "LLM_MODEL": "claude-...", ...}


@router.patch("")
def update_settings(body: EnvPatch):
    env = _read_env()
    for k, v in body.updates.items():
        env[k.upper()] = v
    _write_env(env)
    return {"ok": True, "updated": list(body.updates.keys())}


# ── PGR 配置读写 ───────────────────────────────────────────────────

@router.get("/pgr")
def get_pgr():
    """获取采购组人员配置"""
    if not PGR_PATH.exists():
        return {}
    with open(PGR_PATH, encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    return {k: v for k, v in data.items() if isinstance(v, dict)}


class PGREntry(BaseModel):
    name:  str
    email: str


@router.put("/pgr/{pg_key}")
def upsert_pgr(pg_key: str, body: PGREntry):
    """新增或更新单条 PGR 配置"""
    pg_key = pg_key.upper()
    if not PGR_PATH.exists():
        PGR_PATH.write_text("", encoding="utf-8")
    with open(PGR_PATH, encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    data[pg_key] = {"name": body.name, "email": body.email}
    with open(PGR_PATH, "w", encoding="utf-8") as f:
        yaml.dump(data, f, allow_unicode=True, default_flow_style=False)
    return {"ok": True, "pg_key": pg_key}


@router.delete("/pgr/{pg_key}")
def delete_pgr(pg_key: str):
    pg_key = pg_key.upper()
    if not PGR_PATH.exists():
        raise HTTPException(404, "配置文件不存在")
    with open(PGR_PATH, encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    if pg_key not in data:
        raise HTTPException(404, f"PGR {pg_key} 不存在")
    del data[pg_key]
    with open(PGR_PATH, "w", encoding="utf-8") as f:
        yaml.dump(data, f, allow_unicode=True, default_flow_style=False)
    return {"ok": True}


# ── PGR 从 xlsx 导入 ─────────────────────────────────────────────

from pathlib import Path as _Path
import pandas as pd


@router.post("/pgr/import_xlsx")
def import_pgr_from_xlsx(path: str = ""):
    """从 PGR.xlsx 导入采购组配置到 YAML"""
    if not path:
        path = str(_Path(__file__).parent.parent.parent.parent / "PGR.xlsx")
    p = _Path(path)
    if not p.exists():
        raise HTTPException(404, f"文件不存在: {path}")
    try:
        df = pd.read_excel(p, dtype=str)
    except Exception as e:
        raise HTTPException(400, f"无法读取 Excel: {e}")

    required = {"New PGr.", "Buyer", "Email"}
    cols = set(str(c).strip() for c in df.columns)
    if not required.issubset(cols):
        raise HTTPException(400, f"缺少必要列: {required - cols}")

    if not PGR_PATH.exists():
        PGR_PATH.write_text("", encoding="utf-8")
    with open(PGR_PATH, encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}

    imported = 0
    for _, row in df.iterrows():
        code = str(row.get("New PGr.", "")).strip().upper()
        if not code:
            continue
        name = str(row.get("Buyer", "")).strip()
        email = str(row.get("Email", "")).strip() if pd.notna(row.get("Email")) else ""
        data[code] = {"name": name, "email": email}
        imported += 1

    with open(PGR_PATH, "w", encoding="utf-8") as f:
        yaml.dump(data, f, allow_unicode=True, default_flow_style=False)

    return {"ok": True, "imported": imported, "total": len(data)}
