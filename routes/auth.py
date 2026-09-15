import re
from functools import wraps
from flask import Blueprint, render_template, request, redirect, url_for, session, flash
from models import User, Shipment, DeliveryAgent, Warehouse, Notification, DeliveryProof, STATUS_FLOW, WarehouseActivity, Payment, SystemSettings

# BLUEPRINT
auth_bp = Blueprint("auth", __name__)

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

    return dict(
        name=session.get("user_name"), role="customer",
        shipments=shipments[:3], total=total, in_transit=in_transit,
        delivered=delivered, pending=pending,
        current_shipment=current_shipment, current_location=current_location,
        pickup_agent_name=pickup_agent_name, delivery_agent_name=delivery_agent_name, progress_steps=progress_steps,
        notifications=notifications, delivery_proof=delivery_proof,
        pending_payments=pending_payments,
    )

#DELIVERY AGENT DASHBOARD
def _agent_dashboard_context():
    agent = DeliveryAgent.get_or_create(session["user_id"])
    assigned = Shipment.list_assigned_to_agent(agent["id"])
    picked_up = sum(1 for s in assigned if s["status"] == "Picked Up")
    delivered = sum(1 for s in assigned if s["status"] == "Delivered")
    failed = sum(1 for s in assigned if s["status"] in ("Failed Delivery", "RTO"))

    current_shipment = Shipment.get_current_for_agent(agent["id"])
    delivery_history = Shipment.list_delivery_history_for_agent(agent["id"], limit=5)
    notifications = Notification.list_for_user(session["user_id"], limit=4)

    route_stops = [s["receiver_address"] for s in assigned if s["status"] not in ("Delivered", "Failed Delivery", "RTO")]

    return dict(
        name=session.get("user_name"), role="delivery_agent",
        assigned=assigned, assigned_count=len(assigned),
        picked_up=picked_up, delivered=delivered, failed=failed,
        is_available=agent["is_available"],
        current_shipment=current_shipment, delivery_history=delivery_history,
        notifications=notifications, route_stops=route_stops,
    )

#WAREHOUSE DASHBOARD
def _warehouse_dashboard_context():
    warehouse = Warehouse.get_first()
    if warehouse:
        incoming = Shipment.list_at_warehouse(warehouse["id"], status="In Transit")
        at_warehouse_now = Shipment.list_at_warehouse(warehouse["id"], status="Arrived at Warehouse")
        outgoing_unassigned = Shipment.unassigned_at_warehouse(warehouse["id"])
        agents_available = DeliveryAgent.count_available(warehouse["id"])
        received_today = WarehouseActivity.count_received_today(warehouse["id"])
        dispatched_today = WarehouseActivity.count_dispatched_today(warehouse["id"])
        recent_activity = WarehouseActivity.list_recent(warehouse["id"], limit=4)
        notification_count = Notification.count_for_warehouse(warehouse["id"])
        unread_notification_count = Notification.count_unread_for_warehouse(warehouse["id"])
    else:
        incoming, at_warehouse_now, outgoing_unassigned, agents_available = [], [], [], 0
        received_today, dispatched_today, recent_activity = 0, 0, []
        notification_count, unread_notification_count = 0,0
    agents_busy = DeliveryAgent.count_busy()
    agents_offline = DeliveryAgent.count_offline()
    return dict(
        name=session.get("user_name"), role="warehouse_staff",
        warehouse=warehouse, incoming=incoming,
        at_warehouse_now=at_warehouse_now,
        outgoing_unassigned=outgoing_unassigned, agents_available=agents_available,
        waiting_shipments=outgoing_unassigned, agents_busy=agents_busy, agents_offline=agents_offline,
        received_today=received_today, dispatched_today=dispatched_today,
        recent_activity=recent_activity,
        notification_count=notification_count,
        unread_notification_count=unread_notification_count,
    )

#ADMIN DASHBOARD
def _admin_dashboard_context():
    all_shipments = Shipment.list_all_with_sender(limit=5)
    total_shipments = Shipment.count_all()
    delivered_count = Shipment.count_delivered()
    active_agents = DeliveryAgent.count_available()
    warehouse_count = Warehouse.count_all()
    delivered_rate = round((delivered_count / total_shipments) * 100, 1) if total_shipments else 0

    breakdown = Shipment.status_breakdown()
    weekly = Shipment.deliveries_this_week()
    warehouses_overview = Warehouse.list_all_with_counts()
    users_list = User.list_all(limit=10)
    notifications_log = Notification.list_all_recent(limit=5)

    agents_busy = DeliveryAgent.count_busy()
    agents_offline = DeliveryAgent.count_offline()
    delivery_executives = DeliveryAgent.count_all()

    active_shipments = total_shipments - delivered_count - breakdown["failed"]
    failed_rate = round((breakdown["failed"] / total_shipments) * 100, 1) if total_shipments else 0
    unassigned_count = Shipment.count_unassigned_system_wide()
    total_revenue = Payment.total_revenue()
    settings = SystemSettings.load()

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

#LOGOUT
@auth_bp.route("/logout")
def logout():
    session.clear()
    flash("You have been logged out.", "success")
    return redirect(url_for("auth.login"))

#FORGOT PASSWORD
@auth_bp.route("/forgot-password", methods=["GET", "POST"])
def forgot_password():
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        user = User.find_by_email(email)
        
        if user:
            token = User.set_reset_token(email)
            reset_link = url_for("auth.reset_password", token=token, _external=True)
            flash(f"Reset link (for testing, since email isn't configured yet): {reset_link}", "success")
        else:
            flash("If an account exists with that email, a reset link has been generated.", "success")

        return redirect(url_for("auth.forgot_password"))

    return render_template("forgot_password.html")

#RESET PASSWORD
@auth_bp.route("/reset-password/<token>", methods=["GET", "POST"])
def reset_password(token):
    user = User.find_by_reset_token(token)
    if not user:
        flash("This reset link is invalid or has expired. Please request a new one.", "danger")
        return redirect(url_for("auth.forgot_password"))

    if request.method == "POST":
        password = request.form.get("password", "")
        confirm_password = request.form.get("confirm_password", "")

        if not password or password != confirm_password:
            flash("Passwords do not match.", "danger")
            return redirect(url_for("auth.reset_password", token=token))

        User.reset_password(user["id"], password)
        flash("Your password has been reset. Please log in.", "success")
        return redirect(url_for("auth.login"))

    return render_template("reset_password.html", token=token)

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
    notifications = Notification.list_for_warehouse(warehouse["id"], limit=50) if warehouse else []
    unread_count = Notification.count_unread_for_warehouse(warehouse["id"]) if warehouse else 0
    return render_template(
        "warehouse_notifications.html",
        role="warehouse_staff",
        name=session.get("user_name"),
        notifications=notifications,
        unread_count=unread_count,
    )


@auth_bp.route("/warehouse/notifications/<int:notification_id>/read", methods=["POST"])
@role_required("warehouse_staff")
def mark_warehouse_notification_read(notification_id):
    warehouse = Warehouse.get_first()
    if warehouse:
        Notification.mark_read_for_warehouse(notification_id, warehouse["id"])
    return redirect(url_for("auth.warehouse_notifications"))


@auth_bp.route("/warehouse/notifications/mark-all-read", methods=["POST"])
@role_required("warehouse_staff")
def mark_all_warehouse_notifications_read():
    warehouse = Warehouse.get_first()
    if warehouse:
        Notification.mark_all_read_for_warehouse(warehouse["id"])
        flash("All notifications marked as read.", "success")
    return redirect(url_for("auth.warehouse_notifications"))

#ADMIN USER MANAGEMENT
@auth_bp.route("/admin/dashboard")
@role_required("admin")
def admin_dashboard():
    return render_template(DASHBOARD_TEMPLATES["admin"], **_admin_dashboard_context())

# USER MANAGEMENT 

@auth_bp.route("/admin/users")
@role_required("admin")
def manage_users():
    search = request.args.get("search", "").strip()
    role_filter = request.args.get("role", "").strip()
    status_filter = request.args.get("status", "").strip()

    users_list = User.list_for_management(
        search=search or None,
        role=role_filter or None,
        status=status_filter or None,
    )
    availability_map = DeliveryAgent.list_availability_by_user_id()

    return render_template(
        "manage_users.html",
        users_list=users_list,
        availability_map=availability_map,
        search=search, role_filter=role_filter, status_filter=status_filter,
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
        return redirect(url_for("auth.dashboard") + "#system-settings")

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
    return redirect(url_for("auth.dashboard") + "#system-settings")
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