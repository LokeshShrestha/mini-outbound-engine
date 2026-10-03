# /// script
# requires-python = ">=3.11"
# dependencies = ["fastapi", "uvicorn", "jinja2", "python-multipart", "requests", "beautifulsoup4"]
# ///
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if __package__ in {None, ""} and str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.db import (
    DEFAULT_DB,
    dashboard_counts,
    get_lead,
    init_db,
    list_drafts,
    list_leads,
    list_replies,
    update_status,
    upsert_lead,
)
from app.discovery import DiscoveryError, discover_companies
from app.workflows import WorkflowError, generate_drafts, process_reply, score_new_leads

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
        context={
            "counts": dashboard_counts(),
            "leads": list_leads(),
            "drafts": list_drafts(),
            "replies": list_replies(),
            "message": request.query_params.get("message", ""),
            "error": request.query_params.get("error", ""),
        },
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


@app.post("/leads/discover")
def discover_leads(
    company_type: str = Form(...),
    location: str = Form(...),
    keywords: str = Form(""),
    limit: int = Form(25),
):
    try:
        companies = discover_companies(company_type, location, keywords, limit)
    except (DiscoveryError, ValueError, RuntimeError) as error:
        return RedirectResponse(url=f"/?error={str(error)}", status_code=303)
    for company in companies:
        upsert_lead(company)
    return RedirectResponse(
        url=f"/?message=Found {len(companies)} companies near {location}",
        status_code=303,
    )


@app.post("/pipeline/score")
def score_pipeline():
    try:
        processed = score_new_leads()
    except WorkflowError as error:
        return RedirectResponse(url=f"/?error={str(error)}", status_code=303)
    return RedirectResponse(url=f"/?message=Scored {processed} new leads", status_code=303)


@app.post("/pipeline/drafts")
def draft_pipeline(min_score: int = Form(60)):
    try:
        created = generate_drafts(min_score)
    except WorkflowError as error:
        return RedirectResponse(url=f"/?error={str(error)}", status_code=303)
    return RedirectResponse(url=f"/?message=Created {created} drafts", status_code=303)


@app.post("/replies/process")
def reply_pipeline(
    email: str = Form(""),
    name: str = Form(""),
    reply_text: str = Form(...),
    deal_id: str = Form(""),
    update_crm: bool = Form(False),
):
    if not reply_text.strip():
        return RedirectResponse(url="/?error=Reply text is required", status_code=303)
    try:
        label = process_reply(email, name, reply_text, deal_id, dry_run=not update_crm)
    except (WorkflowError, RuntimeError) as error:
        return RedirectResponse(url=f"/?error={str(error)}", status_code=303)
    return RedirectResponse(url=f"/?message=Reply classified as {label}", status_code=303)


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
