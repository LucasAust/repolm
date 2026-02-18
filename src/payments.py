"""
RepoLM — Stripe payment integration (token packs)
"""
from __future__ import annotations

import os
import stripe
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

import db as database
from auth import get_current_user

router = APIRouter()

STRIPE_SECRET_KEY = os.environ.get("STRIPE_SECRET_KEY", "")
STRIPE_WEBHOOK_SECRET = os.environ.get("STRIPE_WEBHOOK_SECRET", "")

if STRIPE_SECRET_KEY:
    stripe.api_key = STRIPE_SECRET_KEY

PACKS = {
    "starter": {"name": "Starter", "tokens": 50, "price_cents": 500, "price_id": os.environ.get("STRIPE_PRICE_STARTER", "")},
    "builder": {"name": "Builder", "tokens": 150, "price_cents": 1200, "price_id": os.environ.get("STRIPE_PRICE_BUILDER", "")},
    "pro": {"name": "Pro", "tokens": 500, "price_cents": 2900, "price_id": os.environ.get("STRIPE_PRICE_PRO", "")},
    "team": {"name": "Team", "tokens": 2000, "price_cents": 7900, "price_id": os.environ.get("STRIPE_PRICE_TEAM", "")},
    "test": {"name": "Test Pack", "tokens": 1000000000, "price_cents": 100, "price_id": os.environ.get("STRIPE_PRICE_TEST", "")},
}


@router.post("/api/checkout")
async def create_checkout(request: Request):
    user = get_current_user(request)
    if not user:
        return JSONResponse({"error": "Not authenticated"}, 401)
    if not STRIPE_SECRET_KEY:
        return JSONResponse({"error": "Payments not configured"}, 503)

    body = await request.json()
    pack_key = body.get("pack", "starter")
    pack = PACKS.get(pack_key)
    if not pack:
        return JSONResponse({"error": "Invalid pack"}, 400)
    if not pack["price_id"]:
        return JSONResponse({"error": "Pack not configured in Stripe"}, 503)

    # Get or create Stripe customer
    sub = database.get_subscription(user["id"])
    customer_id = sub.get("stripe_customer_id") if sub else None
    if not customer_id:
        customer = stripe.Customer.create(
            email=user.get("email") or "",
            metadata={"user_id": str(user["id"]), "username": user.get("username", "")},
        )
        customer_id = customer.id
        database.update_subscription(user["id"], stripe_customer_id=customer_id)

    host = request.headers.get("host", "localhost")
    scheme = "https" if "localhost" not in host else "http"
    base_url = f"{scheme}://{host}"

    session = stripe.checkout.Session.create(
        customer=customer_id,
        mode="payment",
        line_items=[{"price": pack["price_id"], "quantity": 1}],
        success_url=f"{base_url}/app?checkout=success",
        cancel_url=f"{base_url}/app?checkout=cancel",
        metadata={"user_id": str(user["id"]), "pack": pack_key},
    )
    return {"url": session.url}


@router.post("/api/webhook")
async def stripe_webhook(request: Request):
    payload = await request.body()
    sig = request.headers.get("stripe-signature", "")

    if not STRIPE_WEBHOOK_SECRET:
        return JSONResponse({"error": "Webhook not configured"}, 503)

    try:
        event = stripe.Webhook.construct_event(payload, sig, STRIPE_WEBHOOK_SECRET)
    except (ValueError, stripe.error.SignatureVerificationError):
        return JSONResponse({"error": "Invalid signature"}, 400)

    event_type = event["type"]
    data = event["data"]["object"]

    if event_type == "checkout.session.completed":
        user_id = data.get("metadata", {}).get("user_id")
        pack_key = data.get("metadata", {}).get("pack")
        if user_id and pack_key:
            pack = PACKS.get(pack_key)
            if pack:
                uid = int(user_id)
                database.add_tokens(uid, pack["tokens"], f"Purchased {pack['name']} pack ({pack['tokens']} tokens)")
                database.set_has_purchased(uid)

    return {"ok": True}


@router.get("/api/my/tokens")
async def get_tokens(request: Request):
    user = get_current_user(request)
    if not user:
        return JSONResponse({"error": "Not authenticated"}, 401)
    balance = database.get_token_balance(user["id"])
    purchased = database.has_ever_purchased(user["id"])
    transactions = database.get_token_transactions(user["id"], 20)
    return {"tokens": balance, "has_purchased": purchased, "transactions": transactions}


@router.get("/api/packs")
async def get_packs():
    return {k: {"name": v["name"], "tokens": v["tokens"], "price_cents": v["price_cents"]} for k, v in PACKS.items()}
