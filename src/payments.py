"""
RepoLM — Stripe payment integration (token packs)
"""
from __future__ import annotations

import os
import stripe
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

import db as database
import db_async
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

SUBSCRIPTIONS = {
    "pro_monthly": {
        "name": "Pro Monthly", "plan": "pro", "tokens_per_month": 200,
        "price_cents": 1900, "interval": "month",
        "price_id": os.environ.get("STRIPE_PRICE_PRO_SUB", ""),
    },
    "pro_annual": {
        "name": "Pro Annual", "plan": "pro", "tokens_per_month": 200,
        "price_cents": 18000, "interval": "year",
        "price_id": os.environ.get("STRIPE_PRICE_PRO_ANNUAL", ""),
    },
    "team_monthly": {
        "name": "Team Monthly", "plan": "team", "tokens_per_month": 500,
        "price_cents": 4900, "interval": "month",
        "price_id": os.environ.get("STRIPE_PRICE_TEAM_SUB", ""),
    },
    "team_annual": {
        "name": "Team Annual", "plan": "team", "tokens_per_month": 500,
        "price_cents": 46800, "interval": "year",
        "price_id": os.environ.get("STRIPE_PRICE_TEAM_ANNUAL", ""),
    },
}


@router.post("/api/checkout")
async def create_checkout(request: Request):
    user = await get_current_user(request)
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

    sub = await db_async.get_subscription(user["id"])
    customer_id = sub.get("stripe_customer_id") if sub else None
    if not customer_id:
        customer = stripe.Customer.create(
            email=user.get("email") or "",
            metadata={"user_id": str(user["id"]), "username": user.get("username", "")},
        )
        customer_id = customer.id
        await db_async.update_subscription(user["id"], stripe_customer_id=customer_id)

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


@router.post("/api/subscribe")
async def create_subscription(request: Request):
    """Create a Stripe subscription checkout session."""
    user = await get_current_user(request)
    if not user:
        return JSONResponse({"error": "Not authenticated"}, 401)
    if not STRIPE_SECRET_KEY:
        return JSONResponse({"error": "Payments not configured"}, 503)

    body = await request.json()
    plan_key = body.get("plan", "pro_monthly")
    plan = SUBSCRIPTIONS.get(plan_key)
    if not plan or not plan["price_id"]:
        return JSONResponse({"error": "Invalid or unconfigured plan"}, 400)

    sub = await db_async.get_subscription(user["id"])
    customer_id = sub.get("stripe_customer_id") if sub else None
    if not customer_id:
        customer = stripe.Customer.create(
            email=user.get("email") or "",
            metadata={"user_id": str(user["id"])},
        )
        customer_id = customer.id
        await db_async.update_subscription(user["id"], stripe_customer_id=customer_id)

    host = request.headers.get("host", "localhost")
    scheme = "https" if "localhost" not in host else "http"
    base_url = f"{scheme}://{host}"

    session = stripe.checkout.Session.create(
        customer=customer_id,
        mode="subscription",
        line_items=[{"price": plan["price_id"], "quantity": 1}],
        success_url=f"{base_url}/app?checkout=success&plan={plan['plan']}",
        cancel_url=f"{base_url}/app?checkout=cancel",
        metadata={"user_id": str(user["id"]), "plan_key": plan_key},
    )
    return {"url": session.url}


@router.post("/api/cancel-subscription")
async def cancel_subscription(request: Request):
    """Cancel the user's active subscription."""
    user = await get_current_user(request)
    if not user:
        return JSONResponse({"error": "Not authenticated"}, 401)
    sub = await db_async.get_subscription(user["id"])
    if not sub or not sub.get("subscription_id"):
        return JSONResponse({"error": "No active subscription"}, 400)
    try:
        stripe.Subscription.modify(sub["subscription_id"], cancel_at_period_end=True)
        return {"ok": True, "message": "Subscription will cancel at end of billing period"}
    except Exception as e:
        return JSONResponse({"error": str(e)}, 500)


@router.get("/api/my/subscription")
async def get_subscription_info(request: Request):
    """Get the current user's subscription info."""
    user = await get_current_user(request)
    if not user:
        return JSONResponse({"error": "Not authenticated"}, 401)
    sub = await db_async.get_subscription(user["id"])
    if not sub:
        return {"plan": "free", "status": "none"}
    return {
        "plan": sub.get("plan", "free"),
        "status": sub.get("subscription_status", "none"),
        "subscription_id": sub.get("subscription_id"),
    }


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
        plan_key = data.get("metadata", {}).get("plan_key")

        if user_id and pack_key:
            pack = PACKS.get(pack_key)
            if pack:
                uid = int(user_id)
                await db_async.add_tokens(uid, pack["tokens"], f"Purchased {pack['name']} pack ({pack['tokens']} tokens)")
                def _set_purchased():
                    database.set_has_purchased(uid)
                await db_async.execute_raw(_set_purchased)

        elif user_id and plan_key:
            plan = SUBSCRIPTIONS.get(plan_key)
            if plan:
                uid = int(user_id)
                sub_id = data.get("subscription")
                await db_async.update_subscription(
                    uid,
                    plan=plan["plan"],
                    subscription_status="active",
                    subscription_id=sub_id,
                )
                await db_async.add_tokens(uid, plan["tokens_per_month"], f"Subscription: {plan['name']} — first month tokens")
                def _set_purchased():
                    database.set_has_purchased(uid)
                await db_async.execute_raw(_set_purchased)

    elif event_type == "invoice.paid":
        sub_id = data.get("subscription")
        customer_id = data.get("customer")
        if sub_id and customer_id:
            def _handle_invoice():
                with database.db() as conn:
                    row = conn.execute("SELECT id, plan FROM users WHERE stripe_customer_id=?", (customer_id,)).fetchone()
                    if row:
                        uid = row["id"]
                        plan_name = row["plan"] or "pro"
                        tokens = 200
                        for sk, sv in SUBSCRIPTIONS.items():
                            if sv["plan"] == plan_name:
                                tokens = sv["tokens_per_month"]
                                break
                        billing_reason = data.get("billing_reason", "")
                        if billing_reason == "subscription_cycle":
                            database.add_tokens(uid, tokens, f"Monthly refresh: {plan_name} plan ({tokens} tokens)")
            await db_async.execute_raw(_handle_invoice)

    elif event_type == "customer.subscription.updated":
        sub_id = data.get("id")
        status = data.get("status")
        customer_id = data.get("customer")
        if sub_id and customer_id:
            def _update_sub():
                with database.db() as conn:
                    row = conn.execute("SELECT id FROM users WHERE stripe_customer_id=?", (customer_id,)).fetchone()
                    if row:
                        database.update_subscription(row["id"], subscription_status=status)
            await db_async.execute_raw(_update_sub)

    elif event_type == "customer.subscription.deleted":
        sub_id = data.get("id")
        customer_id = data.get("customer")
        if customer_id:
            def _delete_sub():
                with database.db() as conn:
                    row = conn.execute("SELECT id FROM users WHERE stripe_customer_id=?", (customer_id,)).fetchone()
                    if row:
                        database.update_subscription(row["id"], plan="free", subscription_status="canceled", subscription_id=None)
            await db_async.execute_raw(_delete_sub)

    elif event_type == "invoice.payment_failed":
        customer_id = data.get("customer")
        if customer_id:
            def _mark_past_due():
                with database.db() as conn:
                    row = conn.execute("SELECT id FROM users WHERE stripe_customer_id=?", (customer_id,)).fetchone()
                    if row:
                        database.update_subscription(row["id"], subscription_status="past_due")
            await db_async.execute_raw(_mark_past_due)

    return {"ok": True}


@router.get("/api/my/tokens")
async def get_tokens(request: Request):
    user = await get_current_user(request)
    if not user:
        return JSONResponse({"error": "Not authenticated"}, 401)
    balance = await db_async.get_token_balance(user["id"])
    purchased = await db_async.has_ever_purchased(user["id"])

    def _get_txns():
        return database.get_token_transactions(user["id"], 20)
    transactions = await db_async.execute_raw(_get_txns)
    return {"tokens": balance, "has_purchased": purchased, "transactions": transactions}


@router.get("/api/packs")
async def get_packs():
    return {k: {"name": v["name"], "tokens": v["tokens"], "price_cents": v["price_cents"]} for k, v in PACKS.items()}
