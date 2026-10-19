"""Order Service: creates and tracks orders.

Owns all order data. Before confirming an order it queries the Inventory
Service, a separate process, over HTTP.
"""

import logging
import os
import uuid
from datetime import datetime, timezone
from urllib.parse import quote

import requests
from flask import Flask, jsonify, request

PORT = int(os.environ.get("ORDER_PORT", "8000"))
INVENTORY_URL = os.environ.get("INVENTORY_URL", "http://127.0.0.1:8001")
# Never wait on a dependency indefinitely: a hung Inventory Service must not
# hang every order request along with it.
INVENTORY_TIMEOUT_SECONDS = float(os.environ.get("INVENTORY_TIMEOUT", "3"))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [order-service] %(levelname)s %(message)s",
)
log = logging.getLogger("order-service")

app = Flask(__name__)

# Order data is owned by this service alone. In memory for this assignment.
orders = {}


def error_response(status, code, message):
    return jsonify(error=code, message=message), status


def publish_event(event_type, payload):
    """Emit a domain event.

    Stand-in for publishing to a message broker that the Notification Service
    would subscribe to. Order Service does not wait for or know about consumers.
    """
    log.info("EVENT %s %s", event_type, payload)


@app.get("/health")
def health():
    return jsonify(service="order", status="ok")


@app.post("/orders")
def create_order():
    """Command: submit a new order."""
    body = request.get_json(silent=True) or {}
    item_id = body.get("item_id")
    quantity = body.get("quantity", 1)

    if not isinstance(item_id, str) or not item_id.strip():
        return error_response(400, "invalid_request", "item_id must be a non-empty string.")
    if isinstance(quantity, bool) or not isinstance(quantity, int) or quantity < 1:
        return error_response(400, "invalid_request", "quantity must be a positive integer.")

    # Query Inventory Service for availability. Its stock data is reachable
    # only through its API.
    try:
        resp = requests.get(
            f"{INVENTORY_URL}/inventory/{quote(item_id, safe='')}",
            params={"quantity": quantity},
            timeout=INVENTORY_TIMEOUT_SECONDS,
        )
    except requests.exceptions.Timeout:
        log.error("Inventory Service timed out after %ss; order not created", INVENTORY_TIMEOUT_SECONDS)
        return error_response(
            504,
            "inventory_timeout",
            "Inventory Service did not respond in time. The order was not created; please try again later.",
        )
    except requests.exceptions.ConnectionError:
        log.error("Inventory Service unreachable at %s; order not created", INVENTORY_URL)
        return error_response(
            503,
            "inventory_unavailable",
            "Inventory Service is unavailable. The order was not created; please try again later.",
        )

    if resp.status_code == 404:
        return error_response(404, "item_not_found", f"Item '{item_id}' does not exist.")

    availability = None
    if resp.status_code == 200:
        try:
            availability = resp.json().get("status")
        except (ValueError, AttributeError):
            pass
    if availability not in ("available", "unavailable"):
        log.error("Unexpected response from Inventory Service: HTTP %s %r", resp.status_code, resp.text[:200])
        return error_response(
            502,
            "inventory_bad_response",
            "Inventory Service returned an unexpected response. The order was not created.",
        )

    order = {
        "order_id": uuid.uuid4().hex[:8],
        "item_id": item_id,
        "quantity": quantity,
        "status": "CONFIRMED" if availability == "available" else "REJECTED",
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    orders[order["order_id"]] = order

    if availability == "unavailable":
        log.info("Order %s rejected: %s x%d unavailable", order["order_id"], item_id, quantity)
        return jsonify({**order, "reason": "item_unavailable"}), 409

    log.info("Order %s confirmed: %s x%d", order["order_id"], item_id, quantity)
    publish_event("OrderCreated", {k: order[k] for k in ("order_id", "item_id", "quantity", "created_at")})
    return jsonify(order), 201


@app.get("/orders/<order_id>")
def get_order(order_id):
    """Query: look up an order this service owns."""
    order = orders.get(order_id)
    if order is None:
        return error_response(404, "order_not_found", f"Order '{order_id}' does not exist.")
    return jsonify(order)


if __name__ == "__main__":
    log.info("Starting on port %d (Inventory Service at %s)", PORT, INVENTORY_URL)
    app.run(host="127.0.0.1", port=PORT)
