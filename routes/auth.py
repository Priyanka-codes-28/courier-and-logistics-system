from functools import wraps
from flask import Blueprint, render_template, request, redirect, url_for, session, flash
from models import User, Shipment, DeliveryAgent, Warehouse, Notification, DeliveryProof, STATUS_FLOW, WarehouseActivity, Payment

auth_bp = Blueprint("auth", __name__)

DASHBOARD_TEMPLATES = {
    "customer": "dashboard_customer.html",
    "delivery_agent": "dashboard_agent.html",
    "warehouse_staff": "dashboard_warehouse.html",
    "admin": "dashboard_admin.html",
}


# ============================================================
# ROLE-BASED ROUTE PROTECTION
#
# Use on any route that should only be reachable by specific
# roles. If the user isn't logged in, sends them to login. If
# they're logged in but with the wrong role, they are NOT shown
# the page — they're redirected to their OWN correct dashboard
# with an "Access Denied" message, per requirement #5.
# ============================================================
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


# ============================================================
# Per-role dashboard data. Extracted into standalone functions
# so both the smart /dashboard route AND the specific protected
# routes below (/customer/dashboard, /admin/dashboard, etc.)
# render identical data without duplicating any query logic.
# ============================================================

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
            current_idx = -1  # status is an exception status (Failed/RTO), not in the normal flow
        for i, stage in enumerate(STATUS_FLOW):
            progress_steps.append({
                "label": stage,
                "done": i < current_idx,
                "active": i == current_idx,
            })

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


def _agent_dashboard_context():
    agent = DeliveryAgent.get_or_create(session["user_id"])
    assigned = Shipment.list_assigned_to_agent(agent["id"])
    picked_up = sum(1 for s in assigned if s["status"] == "Picked Up")
    delivered = sum(1 for s in assigned if s["status"] == "Delivered")
    failed = sum(1 for s in assigned if s["status"] in ("Failed Delivery", "RTO"))

    current_shipment = Shipment.get_current_for_agent(agent["id"])
    delivery_history = Shipment.list_delivery_history_for_agent(agent["id"], limit=5)
    notifications = Notification.list_for_user(session["user_id"], limit=4)

    # Today's route: real addresses of this agent's currently active
    # (non-final) assigned shipments, in assignment order.
    route_stops = [s["receiver_address"] for s in assigned if s["status"] not in ("Delivered", "Failed Delivery", "RTO")]

    return dict(
        name=session.get("user_name"), role="delivery_agent",
        assigned=assigned, assigned_count=len(assigned),
        picked_up=picked_up, delivered=delivered, failed=failed,
        is_available=agent["is_available"],
        current_shipment=current_shipment, delivery_history=delivery_history,
        notifications=notifications, route_stops=route_stops,
    )


def _warehouse_dashboard_context():
    # NOTE: users table doesn't yet link a warehouse_staff account to a specific
    # warehouse, so this uses the first warehouse in the table as a stand-in.
    warehouse = Warehouse.get_first()
    if warehouse:
        incoming = Shipment.list_at_warehouse(warehouse["id"], status="In Transit")
        at_warehouse_now = Shipment.list_at_warehouse(warehouse["id"], status="Arrived at Warehouse")
        outgoing_unassigned = Shipment.unassigned_at_warehouse(warehouse["id"])
        agents_available = DeliveryAgent.count_available(warehouse["id"])
        received_today = WarehouseActivity.count_received_today(warehouse["id"])
        dispatched_today = WarehouseActivity.count_dispatched_today(warehouse["id"])
        recent_activity = WarehouseActivity.list_recent(warehouse["id"], limit=4)
    else:
        incoming, at_warehouse_now, outgoing_unassigned, agents_available = [], [], [], 0
        received_today, dispatched_today, recent_activity = 0, 0, []
    agents_busy = DeliveryAgent.count_busy()
    agents_offline = DeliveryAgent.count_offline()
    return dict(
        name=session.get("user_name"), role="warehouse_staff",
        warehouse=warehouse, incoming=incoming, at_warehouse_now=at_warehouse_now,
        outgoing_unassigned=outgoing_unassigned, agents_available=agents_available,
        waiting_shipments=outgoing_unassigned, agents_busy=agents_busy, agents_offline=agents_offline,
        received_today=received_today, dispatched_today=dispatched_today,
        recent_activity=recent_activity,
    )


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
    )


_CONTEXT_BUILDERS = {
    "customer": _customer_dashboard_context,
    "delivery_agent": _agent_dashboard_context,
    "warehouse_staff": _warehouse_dashboard_context,
    "admin": _admin_dashboard_context,
}


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

        # Role is fixed to 'customer' on public signup.
        # Agent / warehouse_staff / admin accounts are created separately by an admin.
        User.create(name, email, phone, password, role="customer")
        flash("Registration successful. Please log in.", "success")
        return redirect(url_for("auth.login"))

    return render_template("register.html")


@auth_bp.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")

        user = User.find_by_email(email)
        if user is None or not User.verify_password(user, password):
            flash("Invalid email or password.", "danger")
            return redirect(url_for("auth.login"))

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

        # Always show the same message whether or not the email exists —
        # prevents leaking which emails are registered.
        if user:
            token = User.set_reset_token(email)
            reset_link = url_for("auth.reset_password", token=token, _external=True)
            # No email service is configured yet, so the link is shown directly here
            # for testing. Once you add Flask-Mail, replace this with an actual email send.
            flash(f"Reset link (for testing, since email isn't configured yet): {reset_link}", "success")
        else:
            flash("If an account exists with that email, a reset link has been generated.", "success")

        return redirect(url_for("auth.forgot_password"))

    return render_template("forgot_password.html")


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


@auth_bp.route("/profile", methods=["GET", "POST"])
def profile():
    """Profile & Account page — shared across all 4 roles, since the
    feature (update name/phone/email) is identical regardless of role.
    Only requires being logged in, not any specific role."""
    if "user_id" not in session:
        flash("Please log in to continue.", "danger")
        return redirect(url_for("auth.login"))

    user = User.find_by_id(session["user_id"])

    if request.method == "POST":
        name = request.form.get("name", "").strip()
        phone = request.form.get("phone", "").strip()
        email = request.form.get("email", "").strip().lower()

        if not name or not email:
            flash("Name and email are required.", "danger")
            return redirect(url_for("auth.profile"))

        success, message = User.update_profile(session["user_id"], name, phone, email)
        flash(message, "success" if success else "danger")
        if success:
            session["user_name"] = name
            session["user_email"] = email
        return redirect(url_for("auth.profile"))

    return render_template("profile.html", user=user, role=session.get("user_role"))


@auth_bp.route("/dashboard")
def dashboard():
    """Smart entry point — always shows YOUR OWN role's dashboard.
    This is what login() redirects to, and what all existing
    templates link to via url_for('auth.dashboard'). Unchanged
    behavior from before this refactor."""
    if "user_id" not in session:
        flash("Please log in to continue.", "danger")
        return redirect(url_for("auth.login"))

    role = session.get("user_role")
    template_name = DASHBOARD_TEMPLATES.get(role, "dashboard_customer.html")
    context_builder = _CONTEXT_BUILDERS.get(role, _customer_dashboard_context)
    return render_template(template_name, **context_builder())


# ============================================================
# SPECIFIC ROLE-PROTECTED ROUTES (requirement #5)
#
# Direct URLs per role, each guarded by role_required(). If a
# customer tries to visit /admin/dashboard directly, they are
# redirected to their own dashboard with an Access Denied
# message instead of ever seeing admin content.
#
# These reuse the exact same context builders and templates as
# the smart /dashboard route above — no duplicated logic.
# ============================================================

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


@auth_bp.route("/admin/dashboard")
@role_required("admin")
def admin_dashboard():
    return render_template(DASHBOARD_TEMPLATES["admin"], **_admin_dashboard_context())


# ============================================================
# USER MANAGEMENT — full dedicated page, admin-only.
# ============================================================

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


# ============================================================
# DEMO ROLE SWITCHER — for testing/demonstration purposes only.
#
# Changes ONLY the current session's role, in memory. Nothing is
# written to the database — the user's real role in the `users`
# table is completely untouched. This lets you preview all 4
# dashboards instantly without ever running an SQL UPDATE.
#
# Log out (or close the browser) and log back in normally, and
# your account reverts to its real, database-stored role.
# ============================================================

DEMO_ROLES = ["customer", "delivery_agent", "warehouse_staff", "admin"]


@auth_bp.route("/demo/login-as/<role_name>")
def demo_login_as(role_name):
    """Quick demo login — signs in directly as the given role with no
    password. Uses a real existing user account under the hood (so
    session['user_id'] is always valid for foreign keys), but overrides
    the displayed name/role for the session only. Nothing about the
    account's real database role is changed."""
    if role_name not in DEMO_ROLES:
        flash("Unknown role.", "danger")
        return redirect(url_for("auth.login"))

    user = User.find_first_by_role(role_name)
    if not user:
        # No account with this role exists yet — fall back to any user,
        # but still show the requested role's dashboard for the demo.
        user = User.find_any()

    if not user:
        flash("No accounts exist yet — please register one first.", "danger")
        return redirect(url_for("auth.register"))

    session["user_id"] = user["id"]
    session["user_name"] = f"Demo {role_name.replace('_', ' ').title()}"
    session["user_role"] = role_name

    flash(f"Logged in as a demo {role_name.replace('_', ' ').title()} account.", "success")
    return redirect(url_for("auth.dashboard"))


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