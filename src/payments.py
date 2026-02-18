"""
RepoLM — Stripe payment integration
"""
from __future__ import annotations

import os
import stripe
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, RedirectResponse

import db as database
from auth import get_current_user

router = APIRouter()

STRIPE_SECRET_KEY = os.environ.get("STRIPE_SECRET_KEY", "")
STRIPE_WEBHOOK_SECRET = os.environ.get("STRIPE_WEBHOOK_SECRET", "")
STRIPE_PRICE_ID = os.environ.get("STRIPE_PRICE_ID", "")

if STRIPE_SECRET_KEY:
    stripe.api_key = STRIPE_SECRET_KEY


@router.post("/api/checkout")
async def create_checkout(request: Request):
    user = get_current_user(request)
    if not user:
        return JSONResponse({"error": "Not authenticated"}, 401)
    if not STRIPE_SECRET_KEY or not STRIPE_PRICE_ID:
        return JSONResponse({"error": "Payments not configured"}, 503)

    sub = database.get_subscription(user["id"])
    if sub and sub.get("plan") == "pro" and sub.get("subscription_status") == "active":
        return JSONResponse({"error": "Already subscribed"}, 400)

    # Get or create Stripe customer
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
        mode="subscription",
        line_items=[{"price": STRIPE_PRICE_ID, "quantity": 1}],
        success_url=f"{base_url}/app?checkout=success",
        cancel_url=f"{base_url}/app?checkout=cancel",
        metadata={"user_id": str(user["id"])},
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
        customer_id = data.get("customer")
        subscription_id = data.get("subscription")
        user_id = data.get("metadata", {}).get("user_id")
        if user_id:
            database.update_subscription(
                int(user_id),
                stripe_customer_id=customer_id,
                subscription_id=subscription_id,
                subscription_status="active",
                plan="pro",
            )

    elif event_type == "customer.subscription.deleted":
        subscription_id = data.get("id")
        # Find user by subscription_id
        user_id = _find_user_by_subscription(subscription_id)
        if user_id:
            database.update_subscription(
                user_id,
                subscription_status="canceled",
                plan="free",
            )

    elif event_type == "invoice.payment_failed":
        subscription_id = data.get("subscription")
        user_id = _find_user_by_subscription(subscription_id)
        if user_id:
            database.update_subscription(
                user_id,
                subscription_status="past_due",
            )

    return {"ok": True}


def _find_user_by_subscription(subscription_id: str):
    """Find user_id by subscription_id in DB."""
    if not subscription_id:
        return None
    with database.db() as conn:
        row = conn.execute(
            "SELECT id FROM users WHERE subscription_id=?", (subscription_id,)
        ).fetchone()
        return row["id"] if row else None


@router.get("/api/my/subscription")
async def get_subscription(request: Request):
    user = get_current_user(request)
    if not user:
        return JSONResponse({"error": "Not authenticated"}, 401)
    sub = database.get_subscription(user["id"])
    return {
        "plan": sub.get("plan", "free") if sub else "free",
        "subscription_status": sub.get("subscription_status", "none") if sub else "none",
        "repos_this_month": sub.get("repos_this_month", 0) if sub else 0,
    }


@router.post("/api/cancel")
async def cancel_subscription(request: Request):
    user = get_current_user(request)
    if not user:
        return JSONResponse({"error": "Not authenticated"}, 401)
    if not STRIPE_SECRET_KEY:
        return JSONResponse({"error": "Payments not configured"}, 503)

    sub = database.get_subscription(user["id"])
    if not sub or not sub.get("subscription_id"):
        return JSONResponse({"error": "No active subscription"}, 400)

    try:
        stripe.Subscription.delete(sub["subscription_id"])
    except Exception as e:
        return JSONResponse({"error": str(e)}, 500)

    database.update_subscription(
        user["id"],
        subscription_status="canceled",
        plan="free",
    )
    return {"ok": True}
