from flask import Blueprint, render_template, request, redirect, url_for, session, flash
from models import Shipment, User

shipments_bp = Blueprint("shipments", __name__)


@shipments_bp.route("/shipments/create", methods=["GET", "POST"])
def create_shipment():
    if "user_id" not in session:
        flash("Please log in to create a shipment.", "danger")
        return redirect(url_for("auth.login"))

    if request.method == "POST":
        receiver_name = request.form.get("receiver_name", "").strip()
        receiver_address = request.form.get("receiver_address", "").strip()
        receiver_phone = request.form.get("receiver_phone", "").strip()

        if not receiver_name or not receiver_address or not receiver_phone:
            flash("Receiver name, address, and phone are required.", "danger")
            return redirect(url_for("shipments.create_shipment"))

        sender = User.find_by_id(session["user_id"])
        tracking_id = Shipment.create(
            sender_id=session["user_id"],
            receiver_name=receiver_name,
            receiver_address=receiver_address,
            receiver_phone=receiver_phone,
            package_type=request.form.get("package_type") or None,
            weight=float(request.form.get("weight")) if request.form.get("weight") else None,
            sender_name=sender["name"] if sender else None,
        )
        flash(f"Shipment created! Your Tracking ID is {tracking_id}", "success")
        return redirect(url_for("shipments.my_shipments"))

    return render_template("create_shipment.html")


@shipments_bp.route("/shipments/my")
def my_shipments():
    if "user_id" not in session:
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
    return render_template("track.html", tracking_id=tracking_id, shipment=shipment, history=history, progress=progress)