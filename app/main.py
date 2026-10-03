# /// script
# requires-python = ">=3.11"
# dependencies = ["fastapi", "uvicorn", "jinja2", "python-multipart"]
# ///
from __future__ import annotations

import csv
import io
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.db import DEFAULT_DB, dashboard_counts, get_lead, init_db, list_leads, update_status, upsert_lead

ROOT = Path(__file__).resolve().parent.parent
app = FastAPI(title="Mini Outbound Engine")
app.mount("/static", StaticFiles(directory=ROOT / "static"), name="static")
templates = Jinja2Templates(directory=ROOT / "templates")


@app.on_event("startup")
def startup() -> None:
    init_db(DEFAULT_DB)


@app.get("/", response_class=HTMLResponse)
def dashboard(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="dashboard.html",
        context={"counts": dashboard_counts(), "leads": list_leads()},
    )


@app.get("/leads/{lead_id}", response_class=HTMLResponse)
def lead_detail(request: Request, lead_id: int):
    lead = get_lead(lead_id)
    if lead is None:
        raise HTTPException(status_code=404, detail="Lead not found")
    return templates.TemplateResponse(
        request=request,
        name="lead.html",
        context={"lead": lead},
    )


@app.post("/leads/import")
def import_leads(file: UploadFile = File(...)):
    if not file.filename or not file.filename.lower().endswith(".csv"):
        raise HTTPException(status_code=400, detail="Upload a CSV file")
    content = file.file.read().decode("utf-8-sig")
    rows = csv.DictReader(io.StringIO(content))
    if not {"name", "website"}.issubset(rows.fieldnames or set()):
        raise HTTPException(status_code=400, detail="CSV requires name and website columns")
    imported = 0
    for row in rows:
        if row.get("name", "").strip() and row.get("website", "").strip():
            upsert_lead(row)
            imported += 1
    return RedirectResponse(url=f"/?imported={imported}", status_code=303)


@app.post("/drafts/{draft_id}/{status}")
def set_draft_status(draft_id: int, status: str):
    if status not in {"approved", "rejected", "needs_approval"}:
        raise HTTPException(status_code=400, detail="Invalid draft status")
    update_status("drafts", draft_id, status)
    return RedirectResponse(url="/", status_code=303)


@app.post("/replies/{reply_id}/{status}")
def set_reply_status(reply_id: int, status: str):
    if status not in {"approved", "rejected", "needs_approval"}:
        raise HTTPException(status_code=400, detail="Invalid reply status")
    update_status("replies", reply_id, status)
    return RedirectResponse(url="/", status_code=303)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app.main:app", host="127.0.0.1", port=8000, reload=False)
