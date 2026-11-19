import logging
import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Lock
from typing import Literal
from uuid import uuid4

import pycountry
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, PositiveInt, field_validator
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm.exc import StaleDataError

from app.db import FlowState, Session
from app.keitaro import tracker
from app.offers import editable_offers, fingerprint, redistribute, remote_offers

app = FastAPI(title="Adrobot")
STATIC = Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory=STATIC), name="static")
write_lock = Lock()
logger = logging.getLogger(__name__)


class CampaignInput(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    country: str
    offer_id: PositiveInt
    domain_id: PositiveInt
    group_id: PositiveInt
    traffic_source_id: PositiveInt

    @field_validator("name", mode="before")
    @classmethod
    def strip_name(cls, value):
        return value.strip() if isinstance(value, str) else value

    @field_validator("country")
    @classmethod
    def country_code(cls, value):
        code = value.strip().upper()
        if not pycountry.countries.get(alpha_2=code):
            raise ValueError("Укажите двухбуквенный код страны ISO 3166-1.")
        return code


class Edit(BaseModel):
    action: Literal["add", "remove", "restore", "pin", "unpin", "cancel", "publish"]
    revision: int = Field(ge=0)
    offer_id: PositiveInt | None = None
    share: int | None = Field(default=None, ge=1, le=100)


@app.get("/", include_in_schema=False)
def index():
    return FileResponse(STATIC / "index.html")


@app.get("/api/catalog")
def catalog():
    paths = {
        "domains": "domains",
        "groups": "groups?type=campaigns",
        "sources": "traffic_sources",
        "offers": "offers",
    }
    with tracker() as api, ThreadPoolExecutor(max_workers=len(paths)) as pool:
        results = {key: pool.submit(api.get, path) for key, path in paths.items()}
        return {
            key: [
                {"id": item["id"], "name": item["name"], "state": item.get("state")}
                for item in result.result()
                if item.get("state") != "deleted"
            ]
            for key, result in results.items()
        }


@app.get("/api/campaigns")
def campaigns(offset: int = 0):
    with tracker() as api:
        items = api.get("campaigns", params={"offset": max(offset, 0), "limit": 100})
        return [
            {key: row.get(key) for key in ("id", "name", "state")}
            for row in items
        ]


@app.post("/api/campaigns", status_code=201)
def create_campaign(data: CampaignInput):
    with tracker() as api:
        offer = api.get(f"offers/{data.offer_id}")
        if offer.get("state") in ("deleted", "disabled"):
            raise HTTPException(422, "Выбранный оффер недоступен.")
        campaign_fields = data.model_dump(exclude={"country", "offer_id"})
        campaign_fields["group_id"] = str(data.group_id)
        campaign = api.post("campaigns", json={
            **campaign_fields, "alias": f"adrobot-{uuid4().hex[:16]}",
            "type": "position", "state": "disabled",
        })
        campaign_id = campaign["id"]
        common = {"campaign_id": campaign_id, "state": "active", "collect_clicks": True}
        try:
            api.post("streams", json={
                **common, "name": f"{data.country} → Google", "position": 1,
                "type": "regular", "schema": "redirect", "action_type": "http",
                "action_payload": "https://www.google.com/",
                "filters": [{"name": "country", "mode": "accept", "payload": [data.country]}],
            })
            api.post("streams", json={
                **common, "name": "Офферы", "position": 2,
                "type": "default", "schema": "landings", "action_type": "http",
                "offers": [{"offer_id": data.offer_id, "share": 100, "state": "active"}],
            })
            api.post(f"campaigns/{campaign_id}/enable")
        except HTTPException as error:
            # Do not delete on a timeout: the last request may already have succeeded.
            logger.warning("Inspect campaign %s after an incomplete creation: %s", campaign_id, error.detail)
            raise HTTPException(error.status_code, f"Проверьте кампанию #{campaign_id} в Keitaro. {error.detail}") from error
    return {"id": campaign_id, "name": data.name}


def flow_view(flow, saved):
    remote = remote_offers(flow)
    current = fingerprint(remote)
    conflict = bool(saved and saved.draft is not None and saved.baseline != current)
    preferences = saved.preferences if saved and saved.baseline == current else []
    return {
        **{key: flow.get(key) for key in ("id", "name", "schema", "position", "type", "state")},
        "offers": saved.draft if saved and saved.draft is not None else editable_offers(remote, preferences),
        "dirty": saved is not None and saved.draft is not None,
        "conflict": conflict,
        "revision": saved.revision if saved else 0,
    }


@app.get("/api/campaigns/{campaign_id}")
def campaign_detail(campaign_id: PositiveInt):
    with tracker() as api, Session() as db:
        campaign = api.get(f"campaigns/{campaign_id}")
        flows = api.get(f"campaigns/{campaign_id}/streams")
        url = os.environ["KEITARO_URL"].rstrip("/")
        return {
            "id": campaign_id, "name": campaign["name"],
            "url": f"{url}/admin/#!/campaigns/{campaign_id}",
            "flows": [
                flow_view(flow, db.get(FlowState, flow["id"]))
                for flow in sorted(flows, key=lambda flow: (flow.get("position") or 0, flow["id"]))
                if flow.get("state") != "deleted"
            ],
        }


@app.get("/api/campaigns/{campaign_id}/stats")
def campaign_stats(campaign_id: PositiveInt):
    with tracker() as api:
        return api.post("report/build", json={
            "range": {"interval": "today", "timezone": os.getenv("KEITARO_TIMEZONE", "Europe/Moscow")},
            "dimensions": ["stream_id", "offer_id"],
            "measures": ["clicks", "conversions", "revenue", "profit"],
            "filters": [{"name": "campaign_id", "operator": "EQUALS", "expression": campaign_id}],
        })


def apply_edit(rows, edit, api):
    row = next((item for item in rows if item["offer_id"] == edit.offer_id), None)
    if edit.action == "add":
        if row:
            raise HTTPException(409, "Оффер уже есть в списке.")
        offer = api.get(f"offers/{edit.offer_id}")
        if offer.get("state") in ("disabled", "deleted"):
            raise HTTPException(422, "Оффер недоступен.")
        rows.append({"offer_id": edit.offer_id, "share": 0, "state": "active", "pinned": False})
    elif row is None:
        raise HTTPException(404, "Оффер отсутствует в потоке.")
    elif edit.action == "remove":
        row.update(state="removed", share=0, pinned=False)
    elif edit.action == "restore":
        if row["state"] == "active":
            raise HTTPException(422, "Оффер уже активен.")
        offer = api.get(f"offers/{edit.offer_id}")
        if offer.get("state") in ("disabled", "deleted"):
            raise HTTPException(422, "Оффер недоступен.")
        row.update(state="active", pinned=False)
    elif edit.action == "pin" and row["state"] == "active" and edit.share is not None:
        row.update(pinned=True, share=edit.share)
    elif edit.action == "unpin" and row["state"] == "active":
        row["pinned"] = False
    else:
        raise HTTPException(422, "Операция недоступна для этого оффера.")
    redistribute(rows)


@app.post("/api/campaigns/{campaign_id}/flows/{stream_id}/edit")
def edit_flow(campaign_id: PositiveInt, stream_id: PositiveInt, edit: Edit):
    if edit.action not in ("publish", "cancel") and edit.offer_id is None:
        raise HTTPException(422, "Укажите ID оффера.")
    with write_lock, tracker() as api, Session() as db:
        flow = api.get(f"streams/{stream_id}")
        if int(flow["campaign_id"]) != campaign_id:
            raise HTTPException(404, "Поток отсутствует в кампании.")
        if flow["schema"] != "landings" or flow.get("state") == "deleted":
            raise HTTPException(422, "Поток не поддерживает редактирование офферов.")

        saved = db.get(FlowState, stream_id)
        view = flow_view(flow, saved)
        if view["revision"] != edit.revision:
            raise HTTPException(409, "Черновик изменился. Обновите кампанию.")
        rows = [row.copy() for row in view["offers"]]
        remote = remote_offers(flow)
        baseline = fingerprint(remote)
        if saved is None:
            saved = FlowState(stream_id=stream_id, baseline=baseline, preferences=[])
            db.add(saved)
        if edit.action == "cancel":
            if saved.baseline != baseline:
                saved.preferences = []
            saved.baseline, saved.draft = baseline, None
        else:
            desired = [
                {key: row[key] for key in ("offer_id", "share", "state")}
                for row in rows if row["state"] != "removed"
            ]
            # A retry can finalize a publish whose response was lost.
            already_published = edit.action == "publish" and fingerprint(desired) == baseline
            if view["conflict"] and not already_published:
                raise HTTPException(409, "Офферы изменились в Keitaro. Отмените черновик и загрузите поток.")
            if edit.action == "publish":
                if not view["dirty"]:
                    return view
                if not already_published:
                    api.request("PUT", f"streams/{stream_id}", json={"offers": desired})
                    flow = api.get(f"streams/{stream_id}")
                    baseline = fingerprint(remote_offers(flow))
                    if baseline != fingerprint(desired):
                        raise HTTPException(502, "Keitaro сохранил другой состав или доли. Проверьте поток.")
                saved.baseline, saved.draft, saved.preferences = baseline, None, rows
            else:
                apply_edit(rows, edit, api)
                saved.baseline, saved.draft = baseline, rows
        try:
            db.commit()
        except (StaleDataError, IntegrityError) as error:
            raise HTTPException(409, "Черновик изменился. Обновите кампанию.") from error
        return flow_view(flow, saved)
