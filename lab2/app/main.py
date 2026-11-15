import json
import os
from pathlib import Path
from urllib.parse import quote_plus

import pandas as pd
import psycopg2
from fastapi import Depends, FastAPI, Header, HTTPException, Query
from psycopg2.extras import Json

DATA_DIR = Path(os.environ.get("DATA_DIR", "/data"))
MAX_DATASET_BYTES = 20 * 1024 * 1024

app = FastAPI(
    title="dataset-profiler",
    version="0.2.0",
    summary="Профилирование CSV и сохранение результата в PostgreSQL",
)


def check_token(authorization: str | None = Header(default=None)) -> None:
    expected = os.environ.get("PROFILER_TOKEN")
    if not expected:
        return
    if authorization != f"Bearer {expected}":
        raise HTTPException(status_code=401, detail="missing or invalid bearer token")


def database_url() -> str:
    try:
        user = quote_plus(os.environ["POSTGRES_USER"])
        password = quote_plus(os.environ["POSTGRES_PASSWORD"])
        database = quote_plus(os.environ["POSTGRES_DB"])
    except KeyError as exc:
        missing = exc.args[0]
        raise HTTPException(
            status_code=500,
            detail=f"database setting {missing} is not set",
        ) from exc
    host = os.environ.get("POSTGRES_HOST", "postgres")
    port = os.environ.get("POSTGRES_PORT", "5432")
    return f"postgresql://{user}:{password}@{host}:{port}/{database}"


def db_conn():
    try:
        return psycopg2.connect(database_url())
    except HTTPException:
        raise
    except psycopg2.Error as exc:
        raise HTTPException(status_code=503, detail="database unavailable") from exc


def dataset_path(name: str) -> Path:
    if Path(name).name != name or not name.endswith(".csv"):
        raise HTTPException(
            status_code=400,
            detail="dataset name must be a single *.csv file name",
        )
    root = DATA_DIR.resolve()
    path = (DATA_DIR / name).resolve()
    if path != root and root not in path.parents:
        raise HTTPException(status_code=400, detail="dataset path escapes the data directory")
    if not path.is_file():
        raise HTTPException(status_code=404, detail="dataset not found")
    if path.stat().st_size > MAX_DATASET_BYTES:
        raise HTTPException(status_code=413, detail="dataset is larger than 20MB")
    return path


def json_number(value: object) -> float | None:
    if value is None or pd.isna(value):
        return None
    return round(float(value), 6)


def column_profile(series: pd.Series) -> dict:
    profile = {
        "name": str(series.name),
        "dtype": str(series.dtype),
        "count": int(series.count()),
        "nulls": int(series.isna().sum()),
        "unique": int(series.nunique(dropna=True)),
    }
    if pd.api.types.is_numeric_dtype(series):
        profile.update(
            {
                "mean": json_number(series.mean()),
                "std": json_number(series.std()),
                "min": json_number(series.min()),
                "max": json_number(series.max()),
            }
        )
    return profile


def as_json(value):
    if isinstance(value, str):
        return json.loads(value)
    return value


def save_profile(dataset: str, rows: int, columns: list[dict]) -> dict:
    conn = db_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO profiles (dataset_name, rows, column_count, summary)
                VALUES (%s, %s, %s, %s)
                RETURNING id, created_at
                """,
                (dataset, rows, len(columns), Json(columns)),
            )
            profile_id, created_at = cur.fetchone()
        conn.commit()
    except HTTPException:
        conn.rollback()
        raise
    except psycopg2.Error as exc:
        conn.rollback()
        raise HTTPException(status_code=503, detail="database unavailable") from exc
    finally:
        conn.close()
    return {"profile_id": int(profile_id), "saved_at": created_at.isoformat()}


@app.get("/")
def root() -> dict:
    return {
        "service": "dataset-profiler",
        "data_dir": str(DATA_DIR),
        "endpoints": [
            "/health",
            "/datasets",
            "/datasets/{name}/profile",
            "/profiles",
            "/docs",
        ],
    }


@app.get("/health")
def health() -> dict:
    conn = db_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT 1")
            cur.fetchone()
        conn.commit()
    except HTTPException:
        conn.rollback()
        raise
    except psycopg2.Error as exc:
        conn.rollback()
        raise HTTPException(status_code=503, detail="database unavailable") from exc
    finally:
        conn.close()
    return {"status": "ok"}


@app.get("/datasets", dependencies=[Depends(check_token)])
def list_datasets() -> dict:
    if not DATA_DIR.exists():
        return {"datasets": []}
    names = sorted(path.name for path in DATA_DIR.glob("*.csv") if path.is_file())
    return {"datasets": names}


@app.get("/datasets/{name}/profile", dependencies=[Depends(check_token)])
def profile_dataset(name: str) -> dict:
    path = dataset_path(name)
    try:
        frame = pd.read_csv(path)
    except pd.errors.EmptyDataError:
        raise HTTPException(status_code=400, detail="dataset is empty") from None
    except pd.errors.ParserError as exc:
        raise HTTPException(status_code=400, detail=f"cannot parse csv: {exc}") from None
    except OSError as exc:
        raise HTTPException(
            status_code=500,
            detail=f"cannot read dataset: {exc.strerror or exc}",
        ) from None
    columns = [column_profile(frame[column]) for column in frame.columns]
    rows = int(frame.shape[0])
    saved = save_profile(path.name, rows, columns)
    return {
        "dataset": path.name,
        "rows": rows,
        "columns": columns,
        **saved,
    }


@app.get("/profiles", dependencies=[Depends(check_token)])
def list_profiles(limit: int = Query(default=20, ge=1, le=100)) -> dict:
    conn = db_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, dataset_name, rows, column_count, summary, created_at
                FROM profiles
                ORDER BY id DESC
                LIMIT %s
                """,
                (limit,),
            )
            rows = cur.fetchall()
        conn.commit()
    except HTTPException:
        conn.rollback()
        raise
    except psycopg2.Error as exc:
        conn.rollback()
        raise HTTPException(status_code=503, detail="database unavailable") from exc
    finally:
        conn.close()
    return {
        "profiles": [
            {
                "id": int(profile_id),
                "dataset": dataset_name,
                "rows": row_count,
                "column_count": column_count,
                "summary": as_json(summary),
                "created_at": created_at.isoformat(),
            }
            for profile_id, dataset_name, row_count, column_count, summary, created_at in rows
        ]
    }
