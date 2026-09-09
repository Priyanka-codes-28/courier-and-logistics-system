from flask import Blueprint, render_template, request, redirect, url_for, session, flash, jsonify
from models import Shipment, User, DeliveryAgent, Notification, DeliveryProof, Warehouse, Payment, ShipmentLocation, SystemSettings
import os
import secrets
from werkzeug.utils import secure_filename

shipments_bp = Blueprint("shipments", __name__)
UPLOAD_FOLDER = os.path.join("static", "uploads")


def login_required_role(*roles):
    """Returns True if logged in and (no roles given OR role matches one of roles)."""
    if "user_id" not in session:
        return False
    if roles and session.get("user_role") not in roles:
        return False
    return True


@shipments_bp.route("/payments/<tracking_id>/pay", methods=["GET", "POST"])
def pay(tracking_id):
    """Simulated payment page — no real gateway is connected. Submitting
    the fake card form marks the payment 'paid' immediately. No real
    money moves and the card fields are never stored, only validated
    for shape (correct number of digits) then discarded."""
    if "user_id" not in session:
        flash("Please log in to make a payment.", "danger")
        return redirect(url_for("auth.login"))

    shipment = Shipment.find_by_tracking_id(tracking_id)
    if not shipment:
        flash("Shipment not found.", "danger")
        return redirect(url_for("shipments.my_shipments"))

    if shipment["sender_id"] != session["user_id"]:
        flash("You can only pay for your own shipments.", "danger")
        return redirect(url_for("shipments.my_shipments"))

    payment = Payment.find_by_shipment(shipment["id"])
    if not payment:
        flash("No payment record found for this shipment.", "danger")
        return redirect(url_for("shipments.my_shipments"))

    if payment["status"] == "paid":
        flash("This shipment has already been paid for.", "success")
        return redirect(url_for("shipments.my_shipments"))

    if request.method == "POST":
        card_number = request.form.get("card_number", "").replace(" ", "")
        expiry = request.form.get("expiry", "").strip()
        cvv = request.form.get("cvv", "").strip()
        name_on_card = request.form.get("name_on_card", "").strip()

        if not name_on_card:
            flash("Please enter the name on the card.", "danger")
            return redirect(url_for("shipments.pay", tracking_id=tracking_id))

        if not card_number.isdigit() or len(card_number) != 16:
            flash("Card number must be exactly 16 digits.", "danger")
            return redirect(url_for("shipments.pay", tracking_id=tracking_id))

        if not cvv.isdigit() or len(cvv) != 3:
            flash("CVV must be exactly 3 digits.", "danger")
            return redirect(url_for("shipments.pay", tracking_id=tracking_id))

        if not expiry or len(expiry) != 5 or expiry[2] != "/":
            flash("Expiry must be in MM/YY format.", "danger")
            return redirect(url_for("shipments.pay", tracking_id=tracking_id))

        # Simulated success — real gateways would call out to an API here.
        # Only the card's last 4 digits are kept as a reference, nothing else.
        fake_transaction_ref = f"SIM{secrets.token_hex(4).upper()}"
        Payment.mark_paid(payment["id"], payment_method=f"Card ending {card_number[-4:]}", transaction_ref=fake_transaction_ref)

        flash(f"Payment of ₹{payment['amount']} successful for {tracking_id}. Reference: {fake_transaction_ref}", "success")
        return redirect(url_for("shipments.my_shipments"))

    return render_template("pay.html", shipment=shipment, payment=payment)


@shipments_bp.route("/shipments/create", methods=["GET", "POST"])
def create_shipment():
    if "user_id" not in session:
        flash("Please log in to create a shipment.", "danger")
        return redirect(url_for("auth.login"))

    if request.method == "POST":
        sender_name = request.form.get("sender_name", "").strip()
        sender_phone = request.form.get("sender_phone", "").strip()
        sender_address = request.form.get("sender_address", "").strip()
        receiver_name = request.form.get("receiver_name", "").strip()
        receiver_address = request.form.get("receiver_address", "").strip()
        receiver_phone = request.form.get("receiver_phone", "").strip()
        package_type = request.form.get("package_type", "").strip()
        package_description = request.form.get("package_description", "").strip()
        weight = request.form.get("weight", "").strip()

        # ---- Validation (Phase 13) ----
        required = {
            "Sender Name": sender_name, "Sender Phone": sender_phone,
            "Pickup Address": sender_address, "Receiver Name": receiver_name,
            "Receiver Phone": receiver_phone, "Delivery Address": receiver_address,
        }
        missing = [label for label, value in required.items() if not value]
        if missing:
            flash(f"Please fill in: {', '.join(missing)}.", "danger")
            return redirect(url_for("shipments.create_shipment"))

        if not sender_phone.isdigit() or len(sender_phone) < 10:
            flash("Sender phone must be at least 10 digits.", "danger")
            return redirect(url_for("shipments.create_shipment"))

        if not receiver_phone.isdigit() or len(receiver_phone) < 10:
            flash("Receiver phone must be at least 10 digits.", "danger")
            return redirect(url_for("shipments.create_shipment"))

        weight_value = None
        if weight:
            try:
                weight_value = float(weight)
                if weight_value <= 0:
                    raise ValueError
            except ValueError:
                flash("Package weight must be a positive number.", "danger")
                return redirect(url_for("shipments.create_shipment"))

        # Assign a warehouse so this shipment is actually visible to
        # warehouse staff. There's only one warehouse in the system
        # right now (same stand-in pattern used elsewhere), so every
        # new shipment routes through it.
        warehouse = Warehouse.get_first()

        tracking_id = Shipment.create(
            sender_id=session["user_id"],
            receiver_name=receiver_name,
            receiver_address=receiver_address,
            receiver_phone=receiver_phone,
            package_type=package_type or None,
            package_description=package_description or None,
            weight=weight_value,
            sender_name=sender_name,
            sender_address=sender_address,
            sender_phone=sender_phone,
            origin_warehouse_id=warehouse["id"] if warehouse else None,
        )

        # Create the pending payment record. Per your requirement, payment
        # happens separately after shipment creation, not during it.
        new_shipment_for_billing = Shipment.find_by_tracking_id(tracking_id)
        fee = Payment.calculate_fee(weight_value)
        Payment.create(new_shipment_for_billing["id"], fee)

        # Auto-assign an available agent as the PICKUP agent right away —
        # this is only the pickup leg, not the final delivery. The final
        # delivery agent is assigned separately later, by warehouse staff,
        # once the shipment reaches Ready for Dispatch.
        #
        # Respects the Automatic Agent Assignment setting: if turned OFF,
        # agent_id stays None and the shipment simply falls into the same
        # 'no agent available yet' waiting state already handled below —
        # no manual-assignment path is introduced.
        settings = SystemSettings.load()
        agent_id = DeliveryAgent.find_first_available() if settings.get("auto_assign_agents", True) else None
        if agent_id:
            new_shipment = Shipment.find_by_tracking_id(tracking_id)
            Shipment.create_assignment(new_shipment["id"], agent_id, agent_role="pickup")
            DeliveryAgent.set_busy_by_agent_id(agent_id)
            agent_user_id = DeliveryAgent.get_user_id(agent_id)
            if agent_user_id and settings.get("in_app_notifications_enabled", True):
                Notification.create(
                    user_id=agent_user_id,
                    shipment_id=new_shipment["id"],
                    message=f"New pickup assigned: shipment {tracking_id}.",
                )
            # Real status transition: Created -> Awaiting Pickup, now that
            # a pickup agent is actually attached to this shipment.
            Shipment.update_status(
                new_shipment["id"], "Awaiting Pickup",
                location="Pickup agent assigned", updated_by=session["user_id"],
            )
            flash(f"Shipment created! Your Tracking ID is {tracking_id}. A pickup agent has been assigned. Payment of ₹{fee} is due.", "success")
        else:
            flash(f"Shipment created! Your Tracking ID is {tracking_id}. No pickup agents are available right now — it'll be assigned once one is. Payment of ₹{fee} is due.", "success")

        return redirect(url_for("shipments.my_shipments"))

    # Pre-fill sender name/phone from the logged-in account as a convenience —
    # customer can still edit them before submitting.
    sender = User.find_by_id(session["user_id"])
    return render_template("create_shipment.html", sender=sender)


@shipments_bp.route("/shipments/my")
def my_shipments():
    if "user_id" not in session:
        flash("Please log in to view your shipments.", "danger")
        return redirect(url_for("auth.login"))

    shipments = Shipment.list_by_sender(session["user_id"])
    return render_template("my_shipments.html", shipments=shipments)


@shipments_bp.route("/track", methods=["GET", "POST"])
def track():
    tracking_id = request.values.get("tracking_id", "").strip()
    shipment = None
    history = []
    live_location_text = None
    eta_label = None
    pickup_agent_name = None
    delivery_agent_name = None
    origin_warehouse_json = None
    last_updated_at = None

    if tracking_id:
        shipment = Shipment.find_by_tracking_id(tracking_id)
        if shipment:
            history = Shipment.get_status_history(shipment["id"])
            # ---- Live Tracking Map: initial values (JS then polls
            # /api/tracking/<id>/location to keep these fresh without
            # a page reload) ----
            live_location_text = Shipment.get_latest_location(shipment["id"])
            eta_label = Shipment.eta_label(shipment["status"])
            pickup_agent_name = Shipment.get_agent_name_by_role(shipment["id"], "pickup")
            delivery_agent_name = Shipment.get_agent_name_by_role(shipment["id"], "delivery")
            # "Last Updated" reuses the same shipment_status_history data
            # already powering the Tracking History list below — the most
            # recent row in that (already-fetched) list is the latest
            # tracking/status/location update, so no extra query needed.
            last_updated_at = history[-1]["timestamp"] if history else None
            if shipment.get("origin_warehouse_id"):
                wh = Warehouse.find_by_id(shipment["origin_warehouse_id"])
                if wh and wh.get("latitude") is not None and wh.get("longitude") is not None:
                    origin_warehouse_json = {
                        "name": wh["name"],
                        "latitude": float(wh["latitude"]),
                        "longitude": float(wh["longitude"]),
                    }
        else:
            flash(f"No shipment found with Tracking ID '{tracking_id}'.", "danger")

    progress = Shipment.progress_percent(shipment["status"]) if shipment else 0

    return render_template(
        "track.html",
        tracking_id=tracking_id,
        shipment=shipment,
        history=history,
        progress=progress,
        live_location_text=live_location_text,
        eta_label=eta_label,
        pickup_agent_name=pickup_agent_name,
        delivery_agent_name=delivery_agent_name,
        origin_warehouse_json=origin_warehouse_json,
        last_updated_at=last_updated_at,
    )


@shipments_bp.route("/shipments/<tracking_id>/update-status", methods=["GET", "POST"])
def update_status(tracking_id):
    # Only delivery agents, warehouse staff, and admins can advance shipment status.
    if not login_required_role("delivery_agent", "warehouse_staff", "admin"):
        flash("You don't have permission to update shipment status.", "danger")
        return redirect(url_for("auth.login"))

    shipment = Shipment.find_by_tracking_id(tracking_id)
    if not shipment:
        flash("Shipment not found.", "danger")
        return redirect(url_for("auth.dashboard"))

    next_status = Shipment.next_status(shipment["status"])

    if request.method == "POST":
        location = request.form.get("location", "").strip()
        remarks = request.form.get("remarks", "").strip()
        chosen_status = request.form.get("status", next_status)

        # Phase 3: reject invalid jumps server-side, regardless of what
        # the form claims — this is the actual enforcement point, not
        # just relying on the UI only ever offering the next stage.
        if not Shipment.is_valid_transition(shipment["status"], chosen_status):
            flash(
                f"Invalid status change: cannot move from '{shipment['status']}' to '{chosen_status}'.",
                "danger",
            )
            return redirect(url_for("shipments.update_status", tracking_id=tracking_id))

        Shipment.update_status(
            shipment["id"], chosen_status,
            location=location or f"Status updated to {chosen_status}",
            remarks=remarks or None,
            updated_by=session["user_id"],
        )
        # Keep the assignment's own status in sync too — otherwise agents
        # stay counted as "busy" forever, even after delivering.
        Shipment.sync_assignment_status(shipment["id"], chosen_status)

        # ---- Automatic agent lifecycle hooks ----
        if chosen_status == "In Transit":
            # Pickup agent's leg is done — free them up for a new pickup.
            pickup_agent_id = Shipment.get_agent_id_by_role(shipment["id"], "pickup")
            if pickup_agent_id:
                DeliveryAgent.set_free_by_agent_id(pickup_agent_id)

        elif chosen_status == "Ready for Dispatch":
            # This replaces the old manual "Assign Delivery Agent" button —
            # the backend tries to auto-assign one right now. If none is
            # available, the shipment simply stays here, waiting, and will
            # be picked up automatically once someone frees up.
            assigned = Shipment.try_auto_assign_delivery_agent(shipment["id"])
            if assigned:
                # Status just advanced a second time within this same
                # click (Ready for Dispatch -> Agent Assigned) — reflect
                # the real final status in the message shown below.
                chosen_status = "Agent Assigned"
            else:
                flash(
                    f"Shipment {tracking_id} is Ready for Dispatch, but no delivery agent is available right now — it will be assigned automatically as soon as one is free.",
                    "success",
                )

        elif chosen_status in ("Delivered", "Failed Delivery", "RTO"):
            # Delivery agent's job is done — free them, then immediately
            # try to clear the waiting queue with this newly-freed agent.
            delivery_agent_id = Shipment.get_agent_id_by_role(shipment["id"], "delivery")
            if delivery_agent_id:
                DeliveryAgent.set_free_by_agent_id(delivery_agent_id)
                Shipment.try_assign_waiting_shipments()

        # Populate the (previously unused) notifications table so the
        # customer's dashboard has real notifications to show.
        #
        # Gated by two settings: the master In-App Notifications switch,
        # and the more specific Shipment Status Change Notifications
        # switch. Either OFF means this particular notification is
        # skipped — the shipment itself still updates normally.
        settings = SystemSettings.load()
        if settings.get("in_app_notifications_enabled", True) and settings.get("status_change_notifications_enabled", True):
            Notification.create(
                user_id=shipment["sender_id"],
                shipment_id=shipment["id"],
                message=f"Your shipment {tracking_id} is now {chosen_status}.",
            )
        flash(f"Shipment {tracking_id} updated to '{chosen_status}'.", "success")
        return redirect(url_for("shipments.update_status", tracking_id=tracking_id))

    return render_template(
        "update_status.html",
        shipment=shipment,
        next_status=next_status,
    )


@shipments_bp.route("/shipments/goto-update-status")
def goto_update_status():
    """Small redirect helper for the Warehouse dashboard's 'Update
    Shipment' form — takes a typed-in Tracking ID and sends the user
    to the real, already-working update-status page for it."""
    tracking_id = request.args.get("tracking_id", "").strip()
    if not tracking_id:
        flash("Please enter a Tracking ID.", "danger")
        return redirect(url_for("auth.dashboard") + "#update-shipment-card")
    return redirect(url_for("shipments.update_status", tracking_id=tracking_id))


@shipments_bp.route("/agent/set-availability", methods=["POST"])
def set_availability():
    """Real write behind the Agent dashboard's Available/Not Available
    toggle. Also now: blocks marking yourself Available while you still
    have an unfinished job (prevents double-booking), and if you're
    genuinely free, immediately tries to clear the auto-assignment
    waiting queue with you."""
    if not login_required_role("delivery_agent"):
        flash("You don't have permission to do that.", "danger")
        return redirect(url_for("auth.login"))

    is_available = request.form.get("is_available") == "true"
    agent = DeliveryAgent.get_or_create(session["user_id"])

    if is_available and DeliveryAgent.has_active_assignment(agent["id"]):
        flash("You still have an active shipment in progress — you'll be marked Available automatically once it's done.", "danger")
        return redirect(url_for("auth.dashboard") + "#availability")

    DeliveryAgent.set_availability(session["user_id"], is_available)

    if is_available:
        newly_assigned = Shipment.try_assign_waiting_shipments()
        if newly_assigned:
            flash(f"You're now marked as Available. {newly_assigned} waiting shipment(s) were just assigned to you.", "success")
        else:
            flash("You're now marked as Available.", "success")
    else:
        flash("You're now marked as Not Available — you won't receive new auto-assignments.", "success")

    return redirect(url_for("auth.dashboard") + "#availability")


@shipments_bp.route("/shipments/<tracking_id>/update-location", methods=["POST"])
def update_location(tracking_id):
    """Real write behind the Agent dashboard's 'Update Location' card.
    Logs a location update without advancing the shipment's status.

    Live Tracking Map addition: also accepts optional latitude/longitude
    fields. If present, they're validated and saved to shipment_locations
    so the customer's tracking map can show a live pin. The original
    text-only 'location' field still works exactly as before — lat/lng
    are purely additive and never required."""
    if not login_required_role("delivery_agent"):
        flash("You don't have permission to update location.", "danger")
        return redirect(url_for("auth.login"))

    location = request.form.get("location", "").strip()
    if not location:
        flash("Please enter a location.", "danger")
        return redirect(url_for("auth.dashboard") + "#update-location")

    shipment = Shipment.find_by_tracking_id(tracking_id)
    if not shipment:
        flash("Shipment not found.", "danger")
        return redirect(url_for("auth.dashboard") + "#delivery-proof")

    agent = DeliveryAgent.get_or_create(session["user_id"])
    settings = SystemSettings.load()
    photo = request.files.get("photo")
    has_photo = bool(photo and photo.filename)

    if settings.get("delivery_proof_required") and not has_photo:
        flash("Delivery proof (a photo) is required before this shipment can be completed.", "danger")
        return redirect(url_for("auth.dashboard") + "#delivery-proof")

    