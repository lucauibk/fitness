"""Shopping-Topf und Wunschliste (Logik in app/rewards.py)."""
import base64
import binascii
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from .. import db, rewards
from ..common import FIELD_NAMES, current_profile, owned

router = APIRouter(prefix="/api")
FIELD_NAMES.update({"rate": "Betrag pro Training", "price": "Preis", "link": "Link",
                    "image": "Foto"})

IMAGE_PREFIX = "data:image/jpeg;base64,"
# Das Frontend verkleinert auf 480 px – das sind gut 40 KB. Die Grenze laesst Luft,
# verhindert aber, dass ein Originalfoto in jeder "Heute"-Antwort mitreist.
MAX_IMAGE = 200_000


class RateIn(BaseModel):
    rate: Optional[float] = Field(None, ge=0.5, le=100, allow_inf_nan=False)


class ItemIn(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=80)
    price: Optional[float] = Field(None, ge=1, le=10000, allow_inf_nan=False)
    link: Optional[str] = Field(None, max_length=500)
    image: Optional[str] = Field(None, max_length=MAX_IMAGE)
    position: Optional[int] = Field(None, ge=0, le=999)


def _clean(body: ItemIn):
    data = body.model_dump(exclude_unset=True)
    if "name" in data:
        data["name"] = (data["name"] or "").strip()
        if not data["name"]:
            raise HTTPException(400, "Bitte einen Namen eingeben.")
    if data.get("link"):
        link = data["link"].strip()
        # Nur echte Weblinks: ein "javascript:"-Link waere sonst ein Klick weit entfernt.
        if not link.lower().startswith(("https://", "http://")):
            raise HTTPException(400, "Link: bitte eine Adresse mit https:// eingeben.")
        data["link"] = link
    elif "link" in data:
        data["link"] = None
    if data.get("image"):
        img = data["image"]
        if not img.startswith(IMAGE_PREFIX):
            raise HTTPException(400, "Foto: ungültiges Format.")
        try:
            base64.b64decode(img[len(IMAGE_PREFIX):], validate=True)
        except (binascii.Error, ValueError) as e:
            raise HTTPException(400, "Foto: ungültige Eingabe.") from e
    elif "image" in data:
        data["image"] = None
    return data


@router.get("/rewards")
def get_rewards(profile_id: int = Depends(current_profile)):
    return rewards.status(profile_id)


@router.put("/rewards/rate")
def put_rate(body: RateIn, profile_id: int = Depends(current_profile)):
    rewards.set_rate(profile_id, body.rate)
    return rewards.status(profile_id)


@router.post("/wishlist")
def add_item(body: ItemIn, profile_id: int = Depends(current_profile)):
    data = _clean(body)
    if not data.get("name") or not data.get("price"):
        raise HTTPException(400, "Bitte Name und Preis eingeben.")
    if data.get("position") is None:
        data["position"] = db.one("SELECT COUNT(*) AS n FROM wishlist WHERE profile_id=?",
                                  (profile_id,))["n"]
    db.insert_row("wishlist", {**data, "profile_id": profile_id, "created_at": db.now()})
    return rewards.status(profile_id)


@router.patch("/wishlist/{item_id}")
def update_item(item_id: int, body: ItemIn, profile_id: int = Depends(current_profile)):
    owned("wishlist", item_id, profile_id, "Dieser Wunsch")
    db.update_row("wishlist", item_id, _clean(body))
    return rewards.status(profile_id)


@router.post("/wishlist/{item_id}/buy")
def buy_item(item_id: int, profile_id: int = Depends(current_profile)):
    item = owned("wishlist", item_id, profile_id, "Dieser Wunsch")
    if item["bought_at"]:
        return rewards.status(profile_id)
    bal = rewards.balance(db.get_row("profiles", profile_id))
    if bal < item["price"]:
        missing = f"{item['price'] - bal:.2f}".replace(".", ",")
        raise HTTPException(400, f"Im Topf fehlen noch {missing} €.")
    db.update_row("wishlist", item_id, {"bought_at": db.now()})
    return rewards.status(profile_id)


@router.post("/wishlist/{item_id}/unbuy")
def unbuy_item(item_id: int, profile_id: int = Depends(current_profile)):
    """Versehentlich gekauft: das Geld kommt zurueck in den Topf."""
    owned("wishlist", item_id, profile_id, "Dieser Wunsch")
    db.update_row("wishlist", item_id, {"bought_at": None})
    return rewards.status(profile_id)


@router.delete("/wishlist/{item_id}")
def delete_item(item_id: int, profile_id: int = Depends(current_profile)):
    owned("wishlist", item_id, profile_id, "Dieser Wunsch")
    db.delete_row("wishlist", item_id)
    return rewards.status(profile_id)
