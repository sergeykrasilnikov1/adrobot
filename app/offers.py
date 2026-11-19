import hashlib
import json

from fastapi import HTTPException


def remote_offers(flow):
    return [
        {
            "offer_id": int(offer["offer_id"]),
            "share": int(offer["share"]),
            "state": offer.get("state") or "active",
        }
        for offer in flow.get("offers") or []
        if offer.get("state") in (None, "active", "disabled")
    ]


def fingerprint(offers):
    ordered = sorted(offers, key=lambda offer: offer["offer_id"])
    return hashlib.sha256(json.dumps(ordered, sort_keys=True).encode()).hexdigest()


def redistribute(rows):
    active = [row for row in rows if row["state"] == "active"]
    flexible = [row for row in active if not row["pinned"]]
    remaining = 100 - sum(row["share"] for row in active if row["pinned"])
    if active and (remaining < len(flexible) or (not flexible and remaining != 0)):
        raise HTTPException(422, "Закреплённые доли не позволяют распределить 100%.")
    if flexible:
        share, extra = divmod(remaining, len(flexible))
        for index, row in enumerate(flexible):
            row["share"] = share + (index < extra)


def editable_offers(remote, saved):
    previous = {row["offer_id"]: row for row in saved}
    live_ids = {row["offer_id"] for row in remote}
    return [
        {**row, "pinned": previous.get(row["offer_id"], {}).get("pinned", False)}
        for row in remote
    ] + [row.copy() for row in saved if row["state"] == "removed" and row["offer_id"] not in live_ids]
