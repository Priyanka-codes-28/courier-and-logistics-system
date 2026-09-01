from flask import Blueprint, render_template, request, redirect, url_for, session, flash
from models import Shipment, User, DeliveryAgent, Notification, DeliveryProof, Warehouse
import os
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

        # Auto-assign an available delivery agent right away, so no manual
        # "Assign Delivery Agent" click is needed for the default flow.
        agent_id = DeliveryAgent.find_first_available()
        if agent_id:
            new_shipment = Shipment.find_by_tracking_id(tracking_id)
            Shipment.create_assignment(new_shipment["id"], agent_id)
            agent_user_id = DeliveryAgent.get_user_id(agent_id)
            if agent_user_id:
                Notification.create(
                    user_id=agent_user_id,
                    shipment_id=new_shipment["id"],
                    message=f"New shipment {tracking_id} has been assigned to you.",
                )
            flash(f"Shipment created! Your Tracking ID is {tracking_id}. It's been assigned to a delivery agent.", "success")
        else:
            flash(f"Shipment created! Your Tracking ID is {tracking_id}. No delivery agents are available right now — it'll be assigned once one is.", "success")

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

    if tracking_id:
        shipment = Shipment.find_by_tracking_id(tracking_id)
        if shipment:
            history = Shipment.get_status_history(shipment["id"])
        else:
            flash(f"No shipment found with Tracking ID '{tracking_id}'.", "danger")

    progress = Shipment.progress_percent(shipment["status"]) if shipment else 0

    return render_template(
        "track.html",
        tracking_id=tracking_id,
        shipment=shipment,
        history=history,
        progress=progress,
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
            location=location or None,
            remarks=remarks or None,
            updated_by=session["user_id"],
        )
        # Populate the (previously unused) notifications table so the
        # customer's dashboard has real notifications to show.
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


@shipments_bp.route("/shipments/assign-agent", methods=["POST"])
def assign_agent():
    """The real write behind the warehouse dashboard's Assign Delivery Agent
    form. Only warehouse staff and admins can do this. Once this runs, the
    shipment stops appearing in the warehouse's 'needs agent' list and
    starts appearing in the chosen agent's 'Assigned Shipments' list."""
    if not login_required_role("warehouse_staff", "admin"):
        flash("You don't have permission to assign delivery agents.", "danger")
        return redirect(url_for("auth.login"))

    tracking_id = request.form.get("tracking_id", "").strip()
    agent_id = request.form.get("agent_id", "").strip()

    if not tracking_id or not agent_id:
        flash("Please provide both a Tracking ID and an agent.", "danger")
        return redirect(url_for("auth.dashboard") + "#assign-agent-card")

    shipment = Shipment.find_by_tracking_id(tracking_id)
    if not shipment:
        flash(f"No shipment found with Tracking ID '{tracking_id}'.", "danger")
        return redirect(url_for("auth.dashboard") + "#assign-agent-card")

    Shipment.create_assignment(shipment["id"], int(agent_id))

    agent_user_id = DeliveryAgent.get_user_id(int(agent_id))
    if agent_user_id:
        Notification.create(
            user_id=agent_user_id,
            shipment_id=shipment["id"],
            message=f"New shipment {tracking_id} has been assigned to you.",
        )

    flash(f"Shipment {tracking_id} has been assigned successfully.", "success")
    return redirect(url_for("auth.dashboard") + "#outgoing-table")
@shipments_bp.route("/shipments/<tracking_id>/update-location", methods=["POST"])
def update_location(tracking_id):
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
        return redirect(url_for("auth.dashboard") + "#update-location")

    Shipment.update_location_only(shipment["id"], location, session["user_id"])
    flash(f"Location updated for {tracking_id}.", "success")
    return redirect(url_for("auth.dashboard") + "#update-location")


@shipments_bp.route("/shipments/<tracking_id>/submit-proof", methods=["POST"])
def submit_proof(tracking_id):
    if not login_required_role("delivery_agent"):
        flash("You don't have permission to submit delivery proof.", "danger")
        return redirect(url_for("auth.login"))

    shipment = Shipment.find_by_tracking_id(tracking_id)
    if not shipment:
        flash("Shipment not found.", "danger")
        return redirect(url_for("auth.dashboard") + "#delivery-proof")

    agent = DeliveryAgent.get_or_create(session["user_id"])

    file_path = None
    photo = request.files.get("photo")
    if photo and photo.filename:
        os.makedirs(UPLOAD_FOLDER, exist_ok=True)
        filename = secure_filename(f"{tracking_id}_{photo.filename}")
        full_path = os.path.join(UPLOAD_FOLDER, filename)
        photo.save(full_path)
        file_path = url_for("static", filename=f"uploads/{filename}")

    DeliveryProof.create(shipment["id"], agent["id"], proof_type="photo", file_path=file_path)
    flash(f"Delivery proof submitted for {tracking_id}.", "success")
    return redirect(url_for("auth.dashboard") + "#delivery-proof")