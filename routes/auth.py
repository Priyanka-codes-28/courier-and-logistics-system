import re
import requests
from datetime import datetime
from functools import wraps
from flask import Blueprint, render_template, request, redirect, url_for, session, flash, current_app
from werkzeug.security import check_password_hash
from models import User, Shipment, DeliveryAgent, Warehouse, Notification, DeliveryProof, STATUS_FLOW, WarehouseActivity, Payment, SystemSettings, get_pagination, DeliveryRating
import email_utils
import google_oauth

# BLUEPRINT
auth_bp = Blueprint("auth", __name__)

def _warehouse_badge_count(counter):
    if session.get("user_role") != "warehouse_staff":
        return 0
    try:
        wh = Warehouse.get_first()
        return counter(wh["id"]) if wh else 0
    except Exception:
        return 0


@auth_bp.record_once
def _register_sidebar_globals(state):
    g = state.app.jinja_env.globals
    g["incoming_shipment_count"] = lambda: _warehouse_badge_count(Shipment.count_incoming_at_warehouse)
    g["outgoing_shipment_count"] = lambda: _warehouse_badge_count(Shipment.count_outgoing_at_warehouse)
    g["inventory_shipment_count"] = lambda: _warehouse_badge_count(Shipment.count_inventory_at_warehouse)

# Brute-force protection: after this many wrong OTP attempts, the OTP
# is invalidated and the user must request a brand new one.
MAX_OTP_ATTEMPTS = 5
def _send_otp_email(to_email, otp):
    subject = "CourierOS Password Reset OTP"
    body = (
        f"Your CourierOS password reset OTP is: {otp}\n\n"
        f"This code expires in 5 minutes. If you didn't request a "
        f"password reset, you can safely ignore this email."
    )

    payload = {
        "sender": {
            "name": current_app.config["BREVO_SENDER_NAME"],
            "email": current_app.config["BREVO_SENDER_EMAIL"],
        },
        "to": [{"email": to_email}],
        "subject": subject,
        "textContent": body,
    }
    headers = {
        "api-key": current_app.config["BREVO_API_KEY"],
        "Content-Type": "application/json",
        "Accept": "application/json",
    }

    try:
        response = requests.post(
            "https://api.brevo.com/v3/smtp/email",
            json=payload,
            headers=headers,
            timeout=10,
        )
        if response.status_code in (200, 201):
            return True
        current_app.logger.error(
            f"Brevo API error sending OTP email: {response.status_code} {response.text}"
        )
        return False
    except requests.RequestException as e:
        current_app.logger.error(f"Failed to reach Brevo API: {e}")
        return False
    
    

#INPUT VALIDATION
EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
PHONE_PATTERN = re.compile(r"^[0-9+\-\s()]{7,20}$")
NAME_PATTERN = re.compile(r"^[A-Za-z][A-Za-z\s.'-]{1,79}$")

#ROLE-BASED DASHBOARD MAPPING
DASHBOARD_TEMPLATES = {
    "customer": "dashboard_customer.html",
    "delivery_agent": "dashboard_agent.html",
    "warehouse_staff": "dashboard_warehouse.html",
    "admin": "dashboard_admin.html",
}

# ROLE-BASED ROUTE PROTECTION
def role_required(*allowed_roles):
    def decorator(view_func):
        @wraps(view_func)
        def wrapped(*args, **kwargs):
            if "user_id" not in session:
                flash("Please log in to continue.", "danger")
                return redirect(url_for("auth.login"))

            if session.get("user_role") not in allowed_roles:
                flash("Access Denied: you don't have permission to view that page.", "danger")
                return redirect(url_for("auth.dashboard"))

            return view_func(*args, **kwargs)
        return wrapped
    return decorator

#CUSTOMER DASHBOARD CONTEXT
def _customer_dashboard_context():
    shipments = Shipment.list_by_sender(session["user_id"])
    total = len(shipments)
    in_transit = sum(1 for s in shipments if s["status"] in ("Picked Up", "In Transit", "Arrived at Warehouse", "Processing", "Ready for Dispatch", "Agent Assigned", "Out for Delivery"))
    delivered = sum(1 for s in shipments if s["status"] == "Delivered")
    pending = sum(1 for s in shipments if s["status"] in ("Created", "Awaiting Pickup"))

    current_shipment = Shipment.get_current_for_sender(session["user_id"])
    current_location = None
    pickup_agent_name = None
    delivery_agent_name = None
    progress_steps = []
    if current_shipment:
        current_location = Shipment.get_latest_location(current_shipment["id"])
        pickup_agent_name = Shipment.get_agent_name_by_role(current_shipment["id"], "pickup")
        delivery_agent_name = Shipment.get_agent_name_by_role(current_shipment["id"], "delivery")
        try:
            current_idx = STATUS_FLOW.index(current_shipment["status"])
        except ValueError:
            current_idx = -1  
        for i, stage in enumerate(STATUS_FLOW):
            progress_steps.append({
                "label": stage,
                "done": i < current_idx,
                "active": i == current_idx,
            })
#CUSTOMER NOTIFICATIONS, PROOF AND PAYMENTS
    notifications = Notification.list_for_user(session["user_id"], limit=4)
    delivery_proof = DeliveryProof.find_latest_for_sender(session["user_id"])
    pending_payments = Payment.list_pending_for_sender(session["user_id"])

    out_for_delivery_shipments = Shipment.list_out_for_delivery_for_sender(session["user_id"])
    for ofd in out_for_delivery_shipments:
        Shipment.ensure_delivery_code(ofd["id"])   # no-op when a code already exists

    rate_cards = DeliveryRating.list_recent_delivered_for_customer(session["user_id"], limit=5)

    return dict(
        name=session.get("user_name"), role="customer",
        shipments=shipments[:3], total=total, in_transit=in_transit,
        delivered=delivered, pending=pending,
        current_shipment=current_shipment, current_location=current_location,
        pickup_agent_name=pickup_agent_name, delivery_agent_name=delivery_agent_name, progress_steps=progress_steps,
        notifications=notifications, delivery_proof=delivery_proof,
        pending_payments=pending_payments,
        out_for_delivery_shipments=out_for_delivery_shipments,
        rate_cards=rate_cards,
    )
#DELIVERY AGENT DASHBOARD
def _agent_dashboard_context():
    agent = DeliveryAgent.get_or_create(session["user_id"])
    all_assigned = Shipment.list_assigned_to_agent(agent["id"])
    picked_up = sum(1 for s in all_assigned if s["status"] == "Picked Up")
    delivered = sum(1 for s in all_assigned if s["status"] == "Delivered")
    failed = sum(1 for s in all_assigned if s["status"] in ("Failed Delivery", "RTO"))
    route_stops = [s["receiver_address"] for s in all_assigned if s["status"] not in ("Delivered", "Failed Delivery", "RTO")]


    # Paginate the Assigned Shipments table only; every stat above already
    # used the full list, so pagination here can't skew those counts.
    assigned_page = request.args.get("assigned_page", 1, type=int)
    assigned_pagination = get_pagination(len(all_assigned), assigned_page, 10)
    assigned = all_assigned[assigned_pagination["offset"]: assigned_pagination["offset"] + assigned_pagination["per_page"]]

    current_shipment = Shipment.get_current_for_agent(agent["id"])
    delivery_history = Shipment.list_delivery_history_for_agent(agent["id"], limit=5)
    notifications = Notification.list_for_user(session["user_id"], limit=4)

    return dict(
        name=session.get("user_name"), role="delivery_agent",
        assigned=assigned, assigned_count=len(all_assigned),
        picked_up=picked_up, delivered=delivered, failed=failed,
        is_available=agent["is_available"],
        current_shipment=current_shipment, delivery_history=delivery_history,
        notifications=notifications, route_stops=route_stops,
        assigned_pagination=assigned_pagination,
    )

#WAREHOUSE DASHBOARD
def _warehouse_dashboard_context():
    warehouse = Warehouse.get_first()
    if warehouse:
        incoming = Shipment.list_at_warehouse(warehouse["id"], status="In Transit")
        at_warehouse_now = Shipment.list_inventory_at_warehouse(warehouse["id"])
        outgoing_unassigned = Shipment.unassigned_at_warehouse(warehouse["id"])
        outgoing_all = Shipment.list_outgoing_at_warehouse(warehouse["id"])
        agents_available = DeliveryAgent.count_available(warehouse["id"])
        received_today = WarehouseActivity.count_received_today(warehouse["id"])
        dispatched_today = WarehouseActivity.count_dispatched_today(warehouse["id"])
        recent_activity = WarehouseActivity.list_recent(warehouse["id"], limit=4)
        notification_count = Notification.count_for_warehouse(warehouse["id"])
        unread_notification_count = Notification.count_unread_for_warehouse(warehouse["id"])
    else:
        incoming, at_warehouse_now, outgoing_unassigned, outgoing_all, agents_available = [], [], [], [], 0
        received_today, dispatched_today, recent_activity = 0, 0, []
        notification_count, unread_notification_count = 0, 0
    agents_busy = DeliveryAgent.count_busy()
    agents_offline = DeliveryAgent.count_offline()

    incoming_page = request.args.get("incoming_page", 1, type=int)
    incoming_pagination = get_pagination(len(incoming), incoming_page, 10)
    incoming_page_items = incoming[incoming_pagination["offset"]: incoming_pagination["offset"] + incoming_pagination["per_page"]]

    outgoing_page = request.args.get("outgoing_page", 1, type=int)
    outgoing_pagination = get_pagination(len(outgoing_all), outgoing_page, 10)
    outgoing_page_items = outgoing_all[outgoing_pagination["offset"]: outgoing_pagination["offset"] + outgoing_pagination["per_page"]]

    return dict(
        name=session.get("user_name"), role="warehouse_staff",
        warehouse=warehouse, incoming=incoming,
        incoming_page_items=incoming_page_items,
        at_warehouse_now=at_warehouse_now,
        outgoing_unassigned=outgoing_unassigned,
        outgoing_all=outgoing_all,
        outgoing_page_items=outgoing_page_items,
        agents_available=agents_available,
        waiting_shipments=outgoing_unassigned, agents_busy=agents_busy, agents_offline=agents_offline,
        received_today=received_today, dispatched_today=dispatched_today,
        recent_activity=recent_activity,
        notification_count=notification_count,
        unread_notification_count=unread_notification_count,
        incoming_pagination=incoming_pagination,
        outgoing_pagination=outgoing_pagination,
    )
    
#ADMIN DASHBOARD CONTEXT
def _admin_dashboard_context():
    total_shipments = Shipment.count_all()
    delivered_count = Shipment.count_delivered()
    active_agents = DeliveryAgent.count_available()
    warehouse_count = Warehouse.count_all()
    delivered_rate = round((delivered_count / total_shipments) * 100, 1) if total_shipments else 0

    breakdown = Shipment.status_breakdown()
    weekly = Shipment.deliveries_this_week()
    users_list = User.list_all(limit=10)

    agents_busy = DeliveryAgent.count_busy()
    agents_offline = DeliveryAgent.count_offline()
    delivery_executives = DeliveryAgent.count_all()

    active_shipments = total_shipments - delivered_count - breakdown["failed"]
    failed_rate = round((breakdown["failed"] / total_shipments) * 100, 1) if total_shipments else 0
    unassigned_count = Shipment.count_unassigned_system_wide()
    total_revenue = Payment.total_revenue()
    settings = SystemSettings.load()

    # Three independent, small paginators for the dashboard's inline
    # preview tables — each keeps its own page in the URL without
    # resetting the other two (see extra_args in the template).
    shipments_page = request.args.get("shipments_page", 1, type=int)
    shipments_pagination = get_pagination(total_shipments, shipments_page, 5)
    all_shipments = Shipment.list_all_with_sender(limit=5, offset=shipments_pagination["offset"])

    warehouse_page = request.args.get("warehouse_page", 1, type=int)
    warehouse_pagination = get_pagination(warehouse_count, warehouse_page, 5)
    warehouses_overview = Warehouse.list_all_with_counts(limit=5, offset=warehouse_pagination["offset"])

    notif_page = request.args.get("notif_page", 1, type=int)
    notif_total = Notification.count_all()
    notif_pagination = get_pagination(notif_total, notif_page, 5)
    notifications_log = Notification.list_all_recent(limit=5, offset=notif_pagination["offset"])

    rating_summary = DeliveryRating.get_rating_summary()

    return dict(
        name=session.get("user_name"), role="admin",
        all_shipments=all_shipments, total_shipments=total_shipments,
        active_agents=active_agents, warehouse_count=warehouse_count,
        delivered_rate=delivered_rate,
        breakdown=breakdown, weekly=weekly,
        warehouses_overview=warehouses_overview, users_list=users_list,
        notifications_log=notifications_log,
        agents_busy=agents_busy, agents_offline=agents_offline,
        delivery_executives=delivery_executives,
        active_shipments=active_shipments, pending=breakdown["pending"],
        failed_count=breakdown["failed"], failed_rate=failed_rate,
        unassigned_count=unassigned_count, total_revenue=total_revenue,
        settings=settings,
        shipments_pagination=shipments_pagination,
        warehouse_pagination=warehouse_pagination,
        notif_pagination=notif_pagination,
        rating_summary=rating_summary,
    )

#CONTEXT BUILDER MAPPING
_CONTEXT_BUILDERS = {
    "customer": _customer_dashboard_context,
    "delivery_agent": _agent_dashboard_context,
    "warehouse_staff": _warehouse_dashboard_context,
    "admin": _admin_dashboard_context,
}

#REGISTRATION
@auth_bp.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        email = request.form.get("email", "").strip().lower()
        phone = request.form.get("phone", "").strip()
        password = request.form.get("password", "")
        confirm_password = request.form.get("confirm_password", "")

        if not name or not email or not password:
            flash("Name, email and password are required.", "danger")
            return redirect(url_for("auth.register"))

        if password != confirm_password:
            flash("Passwords do not match.", "danger")
            return redirect(url_for("auth.register"))

        if User.find_by_email(email):
            flash("An account with this email already exists.", "danger")
            return redirect(url_for("auth.register"))
        User.create(name, email, phone, password, role="customer")

        # Welcome email — never allowed to block registration if Brevo is
        # down or misconfigured; email_utils logs any failure on its own.
        try:
            email_utils.send_welcome_email(email, name)
        except Exception as e:
            current_app.logger.error(f"[email_utils] welcome email failed for {email}: {e}")

        flash("Registration successful. Please log in.", "success")
        return redirect(url_for("auth.login"))

    return render_template("register.html")

#LOGIN
@auth_bp.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")

        user = User.find_by_email(email)
        if user is None or not User.verify_password(user, password):
            flash("Invalid email or password.", "danger")
            return redirect(url_for("auth.login"))
        
#ACCOUNT STATUS CHECK
        if user.get("status") == "inactive":
            flash("Your account has been deactivated. Please contact an administrator.", "danger")
            return redirect(url_for("auth.login"))

        session["user_id"] = user["id"]
        session["user_name"] = user["name"]
        session["user_email"] = user["email"]
        session["user_role"] = user["role"]

        flash(f"Welcome back, {user['name']}!", "success")
        return redirect(url_for("auth.dashboard"))

    return render_template("login.html")

# GOOGLE SIGN IN / SIGN UP
@auth_bp.route("/login/google")
def google_login():
    if not google_oauth.GOOGLE_OAUTH_CONFIGURED:
        flash("Google sign-in isn't configured on this server yet. Please use your email and password.", "danger")
        return redirect(url_for("auth.login"))

    redirect_uri = url_for("auth.google_callback", _external=True)
    return google_oauth.oauth.google.authorize_redirect(redirect_uri, prompt="select_account")


@auth_bp.route("/login/google/callback")
def google_callback():
    if not google_oauth.GOOGLE_OAUTH_CONFIGURED:
        flash("Google sign-in isn't configured on this server yet. Please use your email and password.", "danger")
        return redirect(url_for("auth.login"))

    try:
        token = google_oauth.oauth.google.authorize_access_token()
    except Exception as e:
        current_app.logger.error(f"[google_oauth] authorize_access_token failed: {e}")
        flash("Google sign-in was cancelled or could not be completed. Please try again.", "danger")
        return redirect(url_for("auth.login"))
    userinfo = token.get("userinfo")
    if not userinfo:
        try:
            userinfo = google_oauth.oauth.google.userinfo(token=token)
        except Exception as e:
            current_app.logger.error(f"[google_oauth] userinfo fetch failed: {e}")
            flash("We couldn't verify your Google account. Please try again.", "danger")
            return redirect(url_for("auth.login"))

    google_id = userinfo.get("sub")
    email = (userinfo.get("email") or "").strip().lower()
    email_verified = userinfo.get("email_verified", False)
    name = (userinfo.get("name") or (email.split("@")[0] if email else "Google User")).strip()
    picture = userinfo.get("picture")

    if not google_id or not email:
        flash("Google didn't return the account information we need. Please try again.", "danger")
        return redirect(url_for("auth.login"))

    if not email_verified:
        flash("Your Google email address isn't verified. Please verify it with Google and try again.", "danger")
        return redirect(url_for("auth.login"))

    try:
        user = User.find_or_create_google_user(google_id, email, name, picture)
    except Exception as e:
        current_app.logger.error(f"[google_oauth] account lookup/creation failed for {email}: {e}")
        flash("We couldn't complete Google sign-in right now. Please try again, or use your email and password.", "danger")
        return redirect(url_for("auth.login"))

    if not user:
        flash("We couldn't complete Google sign-in right now. Please try again.", "danger")
        return redirect(url_for("auth.login"))

    if user.get("status") == "inactive":
        flash("Your account has been deactivated. Please contact an administrator.", "danger")
        return redirect(url_for("auth.login"))

    session["user_id"] = user["id"]
    session["user_name"] = user["name"]
    session["user_email"] = user["email"]
    session["user_role"] = user["role"]

    flash(f"Welcome, {user['name']}!", "success")
    return redirect(url_for("auth.dashboard"))

#LOGOUT
@auth_bp.route("/logout")
def logout():
    session.clear()
    flash("You have been logged out.", "success")
    return redirect(url_for("auth.login"))

@auth_bp.route("/forgot-password", methods=["GET", "POST"])
def forgot_password():
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        user = User.find_by_email(email)

        if user:
            otp = User.set_reset_otp(email)
            email_sent = _send_otp_email(email, otp)
            if not email_sent:
                flash("We couldn't send the OTP email right now. Please try again in a moment.", "danger")
                return redirect(url_for("auth.forgot_password"))
            
        session["otp_reset_email"] = email
        flash("If an account exists with that email, a 6-digit OTP has been sent.", "success")
        return redirect(url_for("auth.verify_otp"))

    return render_template("forgot_password.html")


@auth_bp.route("/forgot-password/verify-otp", methods=["GET", "POST"])
def verify_otp():
    """Step 2: the user enters the 6-digit code from their email."""
    email = session.get("otp_reset_email")
    if not email:
        flash("Please start the password reset process again.", "danger")
        return redirect(url_for("auth.forgot_password"))

    if request.method == "POST":
        submitted_otp = request.form.get("otp", "").strip()
        record = User.get_otp_reset_record(email)

        generic_error = "Invalid or expired OTP. Please try again or request a new code."

        if not record or not record["reset_token"]:
            flash(generic_error, "danger")
            return redirect(url_for("auth.verify_otp"))

        if record["reset_otp_attempts"] >= MAX_OTP_ATTEMPTS:
            User.invalidate_reset_otp(email)
            flash("Too many incorrect attempts. Please request a new OTP.", "danger")
            return redirect(url_for("auth.forgot_password"))

        if not record["reset_token_expiry"] or record["reset_token_expiry"] < datetime.now():
            User.invalidate_reset_otp(email)
            flash("This OTP has expired. Please request a new one.", "danger")
            return redirect(url_for("auth.forgot_password"))

        if not check_password_hash(record["reset_token"], submitted_otp):
            User.increment_otp_attempts(email)
            flash(generic_error, "danger")
            return redirect(url_for("auth.verify_otp"))

        User.invalidate_reset_otp(email)
        session.pop("otp_reset_email", None)
        session["otp_verified_email"] = email
        return redirect(url_for("auth.reset_password"))

    return render_template("verify_otp.html")


@auth_bp.route("/forgot-password/reset", methods=["GET", "POST"])
def reset_password():
    
    email = session.get("otp_verified_email")
    if not email:
        flash("Please verify your OTP before resetting your password.", "danger")
        return redirect(url_for("auth.forgot_password"))

    if request.method == "POST":
        password = request.form.get("password", "")
        confirm_password = request.form.get("confirm_password", "")

        if not password or password != confirm_password:
            flash("Passwords do not match.", "danger")
            return redirect(url_for("auth.reset_password"))

        if len(password) < 6:
            flash("Password must be at least 6 characters long.", "danger")
            return redirect(url_for("auth.reset_password"))

        user = User.find_by_email(email)
        if not user:
            
            flash("Something went wrong. Please start again.", "danger")
            return redirect(url_for("auth.forgot_password"))

        User.reset_password(user["id"], password)
        session.pop("otp_verified_email", None)
        flash("Your password has been reset. Please log in.", "success")
        return redirect(url_for("auth.login"))

    return render_template("reset_password.html")

#PROFILE & ACCOUNT MANAGEMENT
@auth_bp.route("/profile", methods=["GET", "POST"])
def profile():
    if "user_id" not in session:
        flash("Please log in to continue.", "danger")
        return redirect(url_for("auth.login"))

    user = User.find_by_id(session["user_id"])

    if request.method == "POST":
        form_type = request.form.get("form_type")

        # Account & Security
        if form_type == "password":
            current_password = request.form.get("current_password", "")
            new_password = request.form.get("new_password", "")
            confirm_new_password = request.form.get("confirm_new_password", "")

            if not current_password or not new_password or not confirm_new_password:
                flash("All password fields are required.", "danger")
                return redirect(url_for("auth.profile"))

            if not User.verify_password(user, current_password):
                flash("Current password is incorrect.", "danger")
                return redirect(url_for("auth.profile"))

            if new_password != confirm_new_password:
                flash("New password and confirmation do not match.", "danger")
                return redirect(url_for("auth.profile"))

            if len(new_password) < 6:
                flash("New password must be at least 6 characters long.", "danger")
                return redirect(url_for("auth.profile"))

            User.reset_password(session["user_id"], new_password)
            flash("Password changed successfully.", "success")
            return redirect(url_for("auth.profile"))

        # Profile Information: name / email / phone 
        name = request.form.get("name", "").strip()
        phone = request.form.get("phone", "").strip()
        email = request.form.get("email", "").strip().lower()

        if not name or not email:
            flash("Name and email are required.", "danger")
            return redirect(url_for("auth.profile"))

        if not NAME_PATTERN.match(name):
            flash("Please enter a valid full name (letters only, 2-80 characters).", "danger")
            return redirect(url_for("auth.profile"))

        if not EMAIL_PATTERN.match(email):
            flash("Please enter a valid email address.", "danger")
            return redirect(url_for("auth.profile"))

        if phone and not PHONE_PATTERN.match(phone):
            flash("Please enter a valid phone number.", "danger")
            return redirect(url_for("auth.profile"))

        success, message = User.update_profile(session["user_id"], name, phone, email)
        flash(message, "success" if success else "danger")
        if success:
            session["user_name"] = name
            session["user_email"] = email
        return redirect(url_for("auth.profile"))

    return render_template("profile.html", user=user, role=session.get("user_role"))

#DASHBOARD ROUTE
@auth_bp.route("/dashboard")
def dashboard():
    if "user_id" not in session:
        flash("Please log in to continue.", "danger")
        return redirect(url_for("auth.login"))

    role = session.get("user_role")
    template_name = DASHBOARD_TEMPLATES.get(role, "dashboard_customer.html")
    context_builder = _CONTEXT_BUILDERS.get(role, _customer_dashboard_context)
    return render_template(template_name, **context_builder())

@auth_bp.route("/customer/dashboard")
@role_required("customer")
def customer_dashboard():
    return render_template(DASHBOARD_TEMPLATES["customer"], **_customer_dashboard_context())


@auth_bp.route("/delivery-agent/dashboard")
@role_required("delivery_agent")
def delivery_agent_dashboard():
    return render_template(DASHBOARD_TEMPLATES["delivery_agent"], **_agent_dashboard_context())


@auth_bp.route("/warehouse/dashboard")
@role_required("warehouse_staff")
def warehouse_dashboard():
    return render_template(DASHBOARD_TEMPLATES["warehouse_staff"], **_warehouse_dashboard_context())
#WAREHOUSE NOTIFICATIONS
@auth_bp.route("/warehouse/notifications")
@role_required("warehouse_staff")
def warehouse_notifications():
    warehouse = Warehouse.get_first()
    page = request.args.get("page", 1, type=int)
    per_page = 10
    total = Notification.count_for_warehouse(warehouse["id"]) if warehouse else 0
    pagination = get_pagination(total, page, per_page)
    notifications = (
        Notification.list_for_warehouse(warehouse["id"], limit=per_page, offset=pagination["offset"])
        if warehouse else []
    )
    unread_count = Notification.count_unread_for_warehouse(warehouse["id"]) if warehouse else 0
    return render_template(
        "warehouse_notifications.html",
        role="warehouse_staff",
        name=session.get("user_name"),
        notifications=notifications,
        unread_count=unread_count,
        pagination=pagination,
    )


@auth_bp.route("/warehouse/notifications/<int:notification_id>/read", methods=["POST"])
@role_required("warehouse_staff")
def mark_warehouse_notification_read(notification_id):
    warehouse = Warehouse.get_first()
    if warehouse:
        Notification.mark_read_for_warehouse(notification_id, warehouse["id"])
    return redirect(url_for("auth.warehouse_notifications", page=request.form.get("page", 1, type=int)))


@auth_bp.route("/warehouse/notifications/mark-all-read", methods=["POST"])
@role_required("warehouse_staff")
def mark_all_warehouse_notifications_read():
    warehouse = Warehouse.get_first()
    if warehouse:
        Notification.mark_all_read_for_warehouse(warehouse["id"])
        flash("All notifications marked as read.", "success")
    return redirect(url_for("auth.warehouse_notifications"))

# WAREHOUSE MODULE PAGES

WAREHOUSE_PAGE_SIZE = 10


def _warehouse_page_base():
    """Shared by every warehouse module page: the warehouse row and the
    unread count for the sidebar badge."""
    warehouse = Warehouse.get_first()
    unread = Notification.count_unread_for_warehouse(warehouse["id"]) if warehouse else 0
    return warehouse, unread


def _paginate_list(items, page_arg="page", per_page=WAREHOUSE_PAGE_SIZE):
    pagination = get_pagination(len(items), request.args.get(page_arg, 1, type=int), per_page)
    start = pagination["offset"]
    return items[start:start + per_page], pagination


@auth_bp.route("/warehouse/incoming")
@role_required("warehouse_staff")
def warehouse_incoming():
    warehouse, unread = _warehouse_page_base()
    items = Shipment.list_at_warehouse(warehouse["id"], status="In Transit") if warehouse else []
    page_items, pagination = _paginate_list(items)
    return render_template(
        "warehouse_incoming.html", role="warehouse_staff", name=session.get("user_name"),
        warehouse=warehouse, shipments=page_items, pagination=pagination,
        unread_notification_count=unread,
    )


@auth_bp.route("/warehouse/outgoing")
@role_required("warehouse_staff")
def warehouse_outgoing():
    warehouse, unread = _warehouse_page_base()
    items = Shipment.list_outgoing_at_warehouse(warehouse["id"]) if warehouse else []
    page_items, pagination = _paginate_list(items)
    return render_template(
        "warehouse_outgoing.html", role="warehouse_staff", name=session.get("user_name"),
        warehouse=warehouse, shipments=page_items, pagination=pagination,
        unread_notification_count=unread,
    )


@auth_bp.route("/warehouse/assign-agent")
@role_required("warehouse_staff")
def warehouse_assign_agent():
    warehouse, unread = _warehouse_page_base()
    items = Shipment.unassigned_at_warehouse(warehouse["id"]) if warehouse else []
    page_items, pagination = _paginate_list(items)
    return render_template(
        "warehouse_assign_agent.html", role="warehouse_staff", name=session.get("user_name"),
        warehouse=warehouse, shipments=page_items, pagination=pagination,
        agents_available=DeliveryAgent.count_available(warehouse["id"]) if warehouse else 0,
        agents_busy=DeliveryAgent.count_busy(), agents_offline=DeliveryAgent.count_offline(),
        unread_notification_count=unread,
    )


@auth_bp.route("/warehouse/inventory")
@role_required("warehouse_staff")
def warehouse_inventory():
    warehouse, unread = _warehouse_page_base()
    items = Shipment.list_inventory_at_warehouse(warehouse["id"]) if warehouse else []
    page_items, pagination = _paginate_list(items)
    return render_template(
        "warehouse_inventory.html", role="warehouse_staff", name=session.get("user_name"),
        warehouse=warehouse, shipments=page_items, pagination=pagination,
        unread_notification_count=unread,
    )


@auth_bp.route("/warehouse/update-status")
@role_required("warehouse_staff")
def warehouse_update_status():
    warehouse, unread = _warehouse_page_base()
    return render_template(
        "warehouse_update_status.html", role="warehouse_staff", name=session.get("user_name"),
        warehouse=warehouse, unread_notification_count=unread,
    )


@auth_bp.route("/warehouse/details")
@role_required("warehouse_staff")
def warehouse_details():
    warehouse, unread = _warehouse_page_base()
    return render_template(
        "warehouse_details.html", role="warehouse_staff", name=session.get("user_name"),
        warehouse=warehouse, unread_notification_count=unread,
    )

AGENT_PAGE_SIZE = 10

def _agent_page_base():
    """Shared by every agent module page: the agent row, current job,
    and the unread count for the sidebar badge."""
    agent = DeliveryAgent.get_or_create(session["user_id"])
    current_shipment = Shipment.get_current_for_agent(agent["id"])
    unread = Notification.count_for_user(session["user_id"])
    return agent, current_shipment, unread


@auth_bp.route("/delivery-agent/assigned")
@role_required("delivery_agent")
def agent_assigned_shipments():
    agent, current_shipment, unread = _agent_page_base()
    all_assigned = Shipment.list_assigned_to_agent(agent["id"])
    page = request.args.get("page", 1, type=int)
    pagination = get_pagination(len(all_assigned), page, AGENT_PAGE_SIZE)
    shipments = all_assigned[pagination["offset"]: pagination["offset"] + AGENT_PAGE_SIZE]
    return render_template(
        "agent_assigned.html", role="delivery_agent", name=session.get("user_name"),
        shipments=shipments, pagination=pagination,
        assigned_count=len(all_assigned), unread_notification_count=unread,
    )


@auth_bp.route("/delivery-agent/update-status")
@role_required("delivery_agent")
def agent_update_status():
    agent, current_shipment, unread = _agent_page_base()
    return render_template(
        "agent_update_status.html", role="delivery_agent", name=session.get("user_name"),
        current_shipment=current_shipment, unread_notification_count=unread,
    )


@auth_bp.route("/delivery-agent/update-location")
@role_required("delivery_agent")
def agent_update_location():
    agent, current_shipment, unread = _agent_page_base()
    return render_template(
        "agent_update_location.html", role="delivery_agent", name=session.get("user_name"),
        current_shipment=current_shipment, unread_notification_count=unread,
    )


@auth_bp.route("/delivery-agent/delivery-proof")
@role_required("delivery_agent")
def agent_delivery_proof():
    agent, current_shipment, unread = _agent_page_base()
    return render_template(
        "agent_delivery_proof.html", role="delivery_agent", name=session.get("user_name"),
        current_shipment=current_shipment, unread_notification_count=unread,
    )


@auth_bp.route("/delivery-agent/availability")
@role_required("delivery_agent")
def agent_availability():
    agent, current_shipment, unread = _agent_page_base()
    return render_template(
        "agent_availability.html", role="delivery_agent", name=session.get("user_name"),
        is_available=agent["is_available"], unread_notification_count=unread,
    )


@auth_bp.route("/delivery-agent/history")
@role_required("delivery_agent")
def agent_delivery_history():
    agent, current_shipment, unread = _agent_page_base()
    page = request.args.get("page", 1, type=int)
    total = Shipment.count_delivery_history_for_agent(agent["id"])
    pagination = get_pagination(total, page, AGENT_PAGE_SIZE)
    delivery_history = Shipment.list_delivery_history_for_agent(
        agent["id"], limit=AGENT_PAGE_SIZE, offset=pagination["offset"],
    )
    return render_template(
        "agent_delivery_history.html", role="delivery_agent", name=session.get("user_name"),
        delivery_history=delivery_history, pagination=pagination,
        unread_notification_count=unread,
    )


@auth_bp.route("/delivery-agent/notifications")
@role_required("delivery_agent")
def agent_notifications():
    agent, current_shipment, unread = _agent_page_base()
    page = request.args.get("page", 1, type=int)
    total = Notification.count_for_user(session["user_id"])
    pagination = get_pagination(total, page, AGENT_PAGE_SIZE)
    notifications = Notification.list_for_user(
        session["user_id"], limit=AGENT_PAGE_SIZE, offset=pagination["offset"],
    )
    return render_template(
        "agent_notifications.html", role="delivery_agent", name=session.get("user_name"),
        notifications=notifications, pagination=pagination,
        unread_notification_count=unread,
    )


@auth_bp.route("/admin/system-settings")
@role_required("admin")
def admin_system_settings():
    settings = SystemSettings.load()
    return render_template("admin_system_settings.html", settings=settings)


# CUSTOMER MODULE PAGES 
CUSTOMER_PAGE_SIZE = 10

@auth_bp.route("/customer/ratings")
@role_required("customer")
def customer_ratings():
    page = request.args.get("page", 1, type=int)
    per_page = 5
    total = DeliveryRating.count_delivered_for_customer(session["user_id"])
    pagination = get_pagination(total, page, per_page)
    rate_items = DeliveryRating.list_delivered_for_customer(
        session["user_id"], limit=per_page, offset=pagination["offset"]
    )
    return render_template("customer_ratings.html", rate_items=rate_items, pagination=pagination)

#CUSTOMER - RATE A DELIVERED SHIPMENT
@auth_bp.route("/customer/shipments/<tracking_id>/rate", methods=["POST"])
@role_required("customer")
def rate_delivery(tracking_id):
    if request.form.get("return_to") == "ratings":
        back = url_for("auth.customer_ratings", page=request.form.get("page", 1, type=int))
    else:
        back = url_for("auth.dashboard") + "#rate-delivery"

    shipment = Shipment.find_by_tracking_id(tracking_id)
    if not shipment or shipment["sender_id"] != session["user_id"]:
        flash("You can only rate your own shipments.", "danger")
        return redirect(back)
    if shipment["status"] != "Delivered":
        flash("You can rate a shipment only after it has been delivered.", "danger")
        return redirect(back)

    raw_rating = request.form.get("rating", "").strip()
    if not (raw_rating.isascii() and raw_rating.isdigit() and 1 <= int(raw_rating) <= 5):
        flash("Please choose a rating from 1 to 5 stars.", "danger")
        return redirect(back)

    feedback = request.form.get("feedback", "").strip()
    if len(feedback) > 1000:
        flash("Feedback must be 1000 characters or fewer.", "danger")
        return redirect(back)

    created = DeliveryRating.create_rating(
        shipment["id"], session["user_id"], int(raw_rating), feedback or None
    )
    if created:
        flash("Thank you! Your rating has been submitted.", "success")
    else:
        flash("This shipment has already been rated.", "danger")
    return redirect(back)


#ADMIN - CUSTOMER RATINGS
@auth_bp.route("/admin/ratings")
@role_required("admin")
def admin_ratings():
    page = request.args.get("page", 1, type=int)
    per_page = 20
    summary = DeliveryRating.get_rating_summary()
    pagination = get_pagination(summary["total"], page, per_page)
    ratings_list = DeliveryRating.get_all_ratings(limit=per_page, offset=pagination["offset"])
    return render_template(
        "admin_ratings.html",
        ratings_list=ratings_list, summary=summary, pagination=pagination,
    )


@auth_bp.route("/customer/notifications")
@role_required("customer")
def customer_notifications():
    page = request.args.get("page", 1, type=int)
    total = Notification.count_for_user(session["user_id"])
    pagination = get_pagination(total, page, CUSTOMER_PAGE_SIZE)
    notifications = Notification.list_for_user(
        session["user_id"], limit=CUSTOMER_PAGE_SIZE, offset=pagination["offset"],
    )
    return render_template(
        "customer_notifications.html", role="customer", name=session.get("user_name"),
        notifications=notifications, pagination=pagination,
    )


@auth_bp.route("/customer/delivery-proof")
@role_required("customer")
def customer_delivery_proof():
    page = request.args.get("page", 1, type=int)
    total = DeliveryProof.count_for_sender(session["user_id"])
    pagination = get_pagination(total, page, CUSTOMER_PAGE_SIZE)
    proofs = DeliveryProof.list_for_sender(
        session["user_id"], limit=CUSTOMER_PAGE_SIZE, offset=pagination["offset"],
    )
    return render_template(
        "customer_delivery_proof.html", role="customer", name=session.get("user_name"),
        proofs=proofs, pagination=pagination,
    )


#ADMIN USER MANAGEMENT
@auth_bp.route("/admin/dashboard")
@role_required("admin")
def admin_dashboard():
    return render_template(DASHBOARD_TEMPLATES["admin"], **_admin_dashboard_context())

#ADMIN — SHIPMENT MANAGEMENT (dedicated page, reuses Shipment.list_all_with_sender)
@auth_bp.route("/admin/shipments")
@role_required("admin")
def manage_shipments():
    page = request.args.get("page", 1, type=int)
    per_page = 20
    total = Shipment.count_all()
    pagination = get_pagination(total, page, per_page)
    shipments_list = Shipment.list_all_with_sender(limit=per_page, offset=pagination["offset"])
    return render_template("admin_shipments.html", shipments_list=shipments_list, pagination=pagination)

#ADMIN — WAREHOUSE MANAGEMENT (dedicated page, reuses Warehouse.list_all_with_counts)
@auth_bp.route("/admin/warehouses")
@role_required("admin")
def manage_warehouses():
    page = request.args.get("page", 1, type=int)
    per_page = 10
    total = Warehouse.count_all()
    pagination = get_pagination(total, page, per_page)
    warehouses_list = Warehouse.list_all_with_counts(limit=per_page, offset=pagination["offset"])
    agents_available = DeliveryAgent.count_available()
    agents_busy = DeliveryAgent.count_busy()
    agents_offline = DeliveryAgent.count_offline()
    return render_template(
        "admin_warehouses.html",
        warehouses_list=warehouses_list,
        agents_available=agents_available,
        agents_busy=agents_busy,
        agents_offline=agents_offline,
        pagination=pagination,
    )

#ADMIN — NOTIFICATIONS (dedicated page, reuses Notification.list_all_recent)
@auth_bp.route("/admin/notifications")
@role_required("admin")
def admin_notifications():
    page = request.args.get("page", 1, type=int)
    per_page = 20
    total = Notification.count_all()
    pagination = get_pagination(total, page, per_page)
    notifications_log = Notification.list_all_recent(limit=per_page, offset=pagination["offset"])
    return render_template("admin_notifications.html", notifications_log=notifications_log, pagination=pagination)

#ADMIN — REPORTS & ANALYTICS (dedicated page, reuses the same metrics as the dashboard)
@auth_bp.route("/admin/reports")
@role_required("admin")
def admin_reports():
    total_shipments = Shipment.count_all()
    delivered_count = Shipment.count_delivered()
    delivered_rate = round((delivered_count / total_shipments) * 100, 1) if total_shipments else 0
    breakdown = Shipment.status_breakdown()
    weekly = Shipment.deliveries_this_week()
    failed_rate = round((breakdown["failed"] / total_shipments) * 100, 1) if total_shipments else 0
    active_shipments = total_shipments - delivered_count - breakdown["failed"]
    total_revenue = Payment.total_revenue()
    return render_template(
        "admin_reports.html",
        total_shipments=total_shipments, delivered_count=delivered_count,
        delivered_rate=delivered_rate, breakdown=breakdown, weekly=weekly,
        failed_rate=failed_rate, active_shipments=active_shipments,
        total_revenue=total_revenue,
    )

@auth_bp.route("/admin/payments")
@role_required("admin")
def admin_payments():
    page = request.args.get("page", 1, type=int)
    per_page = 20
    total = Payment.count_all()
    pagination = get_pagination(total, page, per_page)
    payments_list = Payment.list_all(limit=per_page, offset=pagination["offset"])
    total_revenue = Payment.total_revenue()
    return render_template(
        "admin_payments.html",
        payments_list=payments_list,
        total_revenue=total_revenue,
        pagination=pagination,
    )

# USER MANAGEMENT 

@auth_bp.route("/admin/users")
@role_required("admin")
def manage_users():
    search = request.args.get("search", "").strip()
    role_filter = request.args.get("role", "").strip()
    status_filter = request.args.get("status", "").strip()
    page = request.args.get("page", 1, type=int)
    per_page = 10

    total = User.count_for_management(
        search=search or None, role=role_filter or None, status=status_filter or None,
    )
    pagination = get_pagination(total, page, per_page)

    users_list = User.list_for_management(
        search=search or None,
        role=role_filter or None,
        status=status_filter or None,
        limit=per_page, offset=pagination["offset"],
    )
    availability_map = DeliveryAgent.list_availability_by_user_id()

    return render_template(
        "manage_users.html",
        users_list=users_list,
        availability_map=availability_map,
        search=search, role_filter=role_filter, status_filter=status_filter,
        pagination=pagination,
    )


@auth_bp.route("/admin/users/<int:user_id>/edit", methods=["GET", "POST"])
@role_required("admin")
def edit_user(user_id):
    target_user = User.find_by_id(user_id)
    if not target_user:
        flash("User not found.", "danger")
        return redirect(url_for("auth.manage_users"))

    if request.method == "POST":
        name = request.form.get("name", "").strip()
        email = request.form.get("email", "").strip().lower()
        phone = request.form.get("phone", "").strip()
        role = request.form.get("role", "").strip()

        if not name or not email:
            flash("Name and email are required.", "danger")
            return redirect(url_for("auth.edit_user", user_id=user_id))

        if role not in ("customer", "delivery_agent", "warehouse_staff", "admin"):
            flash("Invalid role selected.", "danger")
            return redirect(url_for("auth.edit_user", user_id=user_id))

        success, message = User.update_details(user_id, name, email, phone, role)
        flash(message, "success" if success else "danger")
        if success:
            return redirect(url_for("auth.manage_users"))
        return redirect(url_for("auth.edit_user", user_id=user_id))

    return render_template("edit_user.html", target_user=target_user)

@auth_bp.route("/admin/users/<int:user_id>/status", methods=["POST"])
@role_required("admin")
def set_user_status(user_id):
    new_status = request.form.get("status", "").strip()
    if new_status not in ("active", "inactive"):
        flash("Invalid status.", "danger")
        return redirect(url_for("auth.manage_users"))

    if user_id == session["user_id"] and new_status == "inactive":
        flash("You can't deactivate your own account.", "danger")
        return redirect(url_for("auth.manage_users"))

    User.set_status(user_id, new_status)
    flash(f"User account marked as {new_status}.", "success")
    return redirect(url_for("auth.manage_users"))


@auth_bp.route("/admin/users/<int:user_id>/delete", methods=["POST"])
@role_required("admin")
def delete_user(user_id):
    if user_id == session["user_id"]:
        flash("You can't delete your own account.", "danger")
        return redirect(url_for("auth.manage_users"))

    if User.has_shipment_history(user_id):
        flash("This user has shipment history and can't be permanently deleted — deactivate them instead to preserve records.", "danger")
        return redirect(url_for("auth.manage_users"))

    User.delete(user_id)
    flash("User permanently deleted.", "success")
    return redirect(url_for("auth.manage_users"))
# ===== ADMIN: WAREHOUSE ADD / EDIT / REMOVE =====
def _read_warehouse_form():
    return {
        "name": request.form.get("name", "").strip(),
        "address": request.form.get("address", "").strip(),
        "city": request.form.get("city", "").strip(),
        "contact_number": request.form.get("contact_number", "").strip(),
    }


def _validate_warehouse_form(data):
    if not all(data.values()):
        return "All warehouse fields are required."
    if not PHONE_PATTERN.match(data["contact_number"]):
        return "Please enter a valid contact number."
    return None


@auth_bp.route("/admin/warehouses/add", methods=["GET", "POST"])
@role_required("admin")
def add_warehouse():
    if request.method == "POST":
        data = _read_warehouse_form()
        error = _validate_warehouse_form(data)
        if error:
            flash(error, "danger")
            return render_template("admin_warehouse_form.html", mode="add", warehouse=data)
        Warehouse.create(data["name"], data["address"], data["city"], data["contact_number"])
        flash("Warehouse added successfully.", "success")
        return redirect(url_for("auth.manage_warehouses"))
    return render_template("admin_warehouse_form.html", mode="add", warehouse={})


@auth_bp.route("/admin/warehouses/<int:warehouse_id>/edit", methods=["GET", "POST"])
@role_required("admin")
def edit_warehouse(warehouse_id):
    warehouse = Warehouse.find_by_id(warehouse_id)
    if not warehouse:
        flash("Warehouse not found.", "danger")
        return redirect(url_for("auth.manage_warehouses"))

    if request.method == "POST":
        data = _read_warehouse_form()
        error = _validate_warehouse_form(data)
        if error:
            flash(error, "danger")
            data["id"] = warehouse_id
            return render_template("admin_warehouse_form.html", mode="edit", warehouse=data)
        Warehouse.update(warehouse_id, data["name"], data["address"], data["city"], data["contact_number"])
        flash("Warehouse updated successfully.", "success")
        return redirect(url_for("auth.manage_warehouses"))

    return render_template("admin_warehouse_form.html", mode="edit", warehouse=warehouse)


@auth_bp.route("/admin/warehouses/<int:warehouse_id>/delete", methods=["POST"])
@role_required("admin")
def delete_warehouse(warehouse_id):
    if not Warehouse.find_by_id(warehouse_id):
        flash("Warehouse not found.", "danger")
        return redirect(url_for("auth.manage_warehouses"))

    deps = Warehouse.dependency_counts(warehouse_id)
    if deps["shipments"] or deps["agents"]:
        flash(
            f"This warehouse still has {deps['shipments']} shipment(s) and "
            f"{deps['agents']} agent(s) linked to it and can't be removed.",
            "danger",
        )
        return redirect(url_for("auth.manage_warehouses"))

    Warehouse.delete(warehouse_id)
    flash("Warehouse removed.", "success")
    return redirect(url_for("auth.manage_warehouses"))


# ===== ADMIN: DELIVERY AGENT ADD / PERFORMANCE =====
@auth_bp.route("/admin/agents/add", methods=["GET", "POST"])
@role_required("admin")
def add_agent():
    warehouses = Warehouse.list_all_with_counts()
    form = {}

    if request.method == "POST":
        form = {
            "name": request.form.get("name", "").strip(),
            "email": request.form.get("email", "").strip().lower(),
            "phone": request.form.get("phone", "").strip(),
            "warehouse_id": request.form.get("warehouse_id", "").strip(),
        }
        password = request.form.get("password", "")

        error = None
        if not form["name"] or not form["email"] or not password:
            error = "Name, email and password are required."
        elif not NAME_PATTERN.match(form["name"]):
            error = "Please enter a valid full name (letters only, 2-80 characters)."
        elif not EMAIL_PATTERN.match(form["email"]):
            error = "Please enter a valid email address."
        elif form["phone"] and not PHONE_PATTERN.match(form["phone"]):
            error = "Please enter a valid phone number."
        elif len(password) < 6:
            error = "Password must be at least 6 characters long."
        elif User.find_by_email(form["email"]):
            error = "An account with this email already exists."

        warehouse_id = None
        if not error and form["warehouse_id"]:
            try:
                warehouse_id = int(form["warehouse_id"])
            except ValueError:
                error = "Invalid warehouse selected."
            else:
                if not Warehouse.find_by_id(warehouse_id):
                    error = "Selected warehouse does not exist."

        if error:
            flash(error, "danger")
            return render_template("admin_agent_form.html", form=form, warehouses=warehouses)

        user_id = User.create(form["name"], form["email"], form["phone"] or None,
                              password, role="delivery_agent")
        DeliveryAgent.get_or_create(user_id, warehouse_id)
        flash("Delivery agent added successfully.", "success")
        return redirect(url_for("auth.manage_users", role="delivery_agent"))

    return render_template("admin_add_agent.html", form=form, warehouses=warehouses)


@auth_bp.route("/admin/agents/performance")
@role_required("admin")
def agent_performance():
    agents = []
    for r in DeliveryAgent.list_performance():
        r = dict(r)
        finished = (r["delivered"] or 0) + (r["failed"] or 0)
        r["success_rate"] = round(r["delivered"] / finished * 100, 1) if finished else None
        if r["is_available"]:
            r["availability"] = "Available"
        elif r["active"]:
            r["availability"] = "Busy"
        else:
            r["availability"] = "Offline"
        agents.append(r)

    total_delivered = sum(a["delivered"] for a in agents)
    total_failed = sum(a["failed"] for a in agents)
    total_active = sum(a["active"] for a in agents)
    finished_total = total_delivered + total_failed
    overall_rate = round(total_delivered / finished_total * 100, 1) if finished_total else None

    return render_template(
        "admin_agent_performance.html",
        agents=agents, total_delivered=total_delivered, total_failed=total_failed,
        total_active=total_active, overall_rate=overall_rate,
    )

VALID_NOTIFICATION_PREFERENCES = {"In-App", "Email", "SMS"}

#SYSTEM SETTINGS
@auth_bp.route("/admin/system-settings/save", methods=["POST"])
@role_required("admin")
def save_system_settings():
    errors = []

    tracking_id_format = request.form.get("tracking_id_format", "").strip()
    if not tracking_id_format:
        errors.append("Tracking ID Format cannot be empty.")

    try:
        max_active = int(request.form.get("max_active_shipments_per_agent", "").strip())
        if max_active <= 0:
            raise ValueError
    except ValueError:
        errors.append("Maximum Active Shipments per Agent must be a positive number.")
        max_active = None

    notification_preference = request.form.get("customer_notification_preference", "In-App").strip()
    if notification_preference not in VALID_NOTIFICATION_PREFERENCES:
        notification_preference = "In-App"

    if errors:
        for message in errors:
            flash(message, "danger")
        return redirect(request.form.get("next") or (url_for("auth.dashboard") + "#system-settings"))

    SystemSettings.update({
        "tracking_id_format": tracking_id_format,
        "auto_generate_tracking_id": request.form.get("auto_generate_tracking_id") == "on",
        "auto_assign_agents": request.form.get("auto_assign_agents") == "on",
        "agent_assignment_method": "Least Busy Agent",
        "max_active_shipments_per_agent": max_active,
        "auto_reassign_on_unavailable": request.form.get("auto_reassign_on_unavailable") == "on",
        "in_app_notifications_enabled": request.form.get("in_app_notifications_enabled") == "on",
        "status_change_notifications_enabled": request.form.get("status_change_notifications_enabled") == "on",
        "customer_notification_preference": notification_preference,
    })

    flash("System settings saved successfully.", "success")
    return redirect(request.form.get("next") or (url_for("auth.dashboard") + "#system-settings"))
#DEMO LOGIN
DEMO_ROLES = ["customer", "delivery_agent", "warehouse_staff", "admin"]


@auth_bp.route("/demo/login-as/<role_name>")
def demo_login_as(role_name):
    if role_name not in DEMO_ROLES:
        flash("Unknown role.", "danger")
        return redirect(url_for("auth.login"))

    user = User.find_first_by_role(role_name)
    if not user:
        user = User.find_any()

    if not user:
        flash("No accounts exist yet — please register one first.", "danger")
        return redirect(url_for("auth.register"))

    session["user_id"] = user["id"]
    session["user_name"] = f"Demo {role_name.replace('_', ' ').title()}"
    session["user_role"] = role_name

    flash(f"Logged in as a demo {role_name.replace('_', ' ').title()} account.", "success")
    return redirect(url_for("auth.dashboard"))

#DEMO ROLE SWITCHING
@auth_bp.route("/demo/switch-role/<role_name>")
def demo_switch_role(role_name):
    if "user_id" not in session:
        
        flash("Please log in first.", "danger")
        return redirect(url_for("auth.login"))

    if role_name not in DEMO_ROLES:
        flash("Unknown role.", "danger")
        return redirect(url_for("auth.dashboard"))

    session["user_role"] = role_name
    flash(f"Demo mode: now viewing as {role_name.replace('_', ' ').title()}. (Not saved to database.)", "success")
    return redirect(url_for("auth.dashboard"))