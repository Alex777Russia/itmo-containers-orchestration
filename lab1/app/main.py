import os
from pathlib import Path

import pandas as pd
from fastapi import Depends, FastAPI, Header, HTTPException

DATA_DIR = Path(os.environ.get("DATA_DIR", "/data"))
MAX_DATASET_BYTES = 20 * 1024 * 1024

app = FastAPI(
    title="dataset-profiler",
    version="0.1.0",
    summary="Профилирование CSV-файлов из смонтированного каталога",
)


def check_token(authorization: str | None = Header(default=None)) -> None:
    expected = os.environ.get("PROFILER_TOKEN")
    if not expected:
        return
    if authorization != f"Bearer {expected}":
        raise HTTPException(status_code=401, detail="missing or invalid bearer token")


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


@app.get("/")
def root() -> dict:
    return {
        "service": "dataset-profiler",
        "data_dir": str(DATA_DIR),
        "endpoints": [
            "/health",
            "/datasets",
            "/datasets/{name}/profile",
            "/docs",
        ],
    }


@app.get("/health")
def health() -> dict:
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
    return {
        "dataset": path.name,
        "rows": int(frame.shape[0]),
        "columns": [column_profile(frame[column]) for column in frame.columns],
    }
