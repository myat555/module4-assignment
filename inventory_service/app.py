"""Inventory Service: tracks item availability.

Owns all stock data. Other services ask about availability through this
HTTP API; nothing outside this process reads or writes the stock directly.
"""

import logging
import os

from flask import Flask, jsonify, request

PORT = int(os.environ.get("INVENTORY_PORT", "8001"))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [inventory-service] %(levelname)s %(message)s",
)
log = logging.getLogger("inventory-service")

app = Flask(__name__)

# Sample data: units on hand per item. In memory for this assignment.
stock = {
    "keyboard": 10,
    "mouse": 25,
    "headphones": 2,
    "monitor": 0,
}


@app.get("/health")
def health():
    return jsonify(service="inventory", status="ok")


@app.get("/inventory/<item_id>")
def check_availability(item_id):
    """Query: answers whether `quantity` units are in stock. Read-only."""
    quantity = request.args.get("quantity", default=1, type=int)
    if quantity < 1:
        return jsonify(error="invalid_request", message="quantity must be a positive integer"), 400
    if item_id not in stock:
        return jsonify(error="item_not_found", message=f"Item '{item_id}' does not exist."), 404

    status = "available" if stock[item_id] >= quantity else "unavailable"
    log.info("Availability check: %s x%d -> %s", item_id, quantity, status)
    return jsonify(item_id=item_id, quantity=quantity, status=status)


if __name__ == "__main__":
    log.info("Starting on port %d", PORT)
    app.run(host="127.0.0.1", port=PORT)
