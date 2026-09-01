from functools import wraps
from flask import Blueprint, render_template, request, redirect, url_for, session, flash
from models import User, Shipment, DeliveryAgent, Warehouse

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
    in_transit = sum(1 for s in shipments if s["status"] in ("Picked Up", "At Warehouse", "In Transit", "Out for Delivery"))
    delivered = sum(1 for s in shipments if s["status"] == "Delivered")
    pending = sum(1 for s in shipments if s["status"] == "Created")
    return dict(
        name=session.get("user_name"), role="customer",
        shipments=shipments[:3], total=total, in_transit=in_transit,
        delivered=delivered, pending=pending,
    )


def _agent_dashboard_context():
    agent = DeliveryAgent.get_or_create(session["user_id"])
    assigned = Shipment.list_assigned_to_agent(agent["id"])
    picked_up = sum(1 for s in assigned if s["status"] == "Picked Up")
    delivered = sum(1 for s in assigned if s["status"] == "Delivered")
    failed = sum(1 for s in assigned if s["status"] in ("Failed Delivery", "RTO"))
    return dict(
        name=session.get("user_name"), role="delivery_agent",
        assigned=assigned, assigned_count=len(assigned),
        picked_up=picked_up, delivered=delivered, failed=failed,
        is_available=agent["is_available"],
    )


def _warehouse_dashboard_context():
    # NOTE: users table doesn't yet link a warehouse_staff account to a specific
    # warehouse, so this uses the first warehouse in the table as a stand-in.
    warehouse = Warehouse.get_first()
    if warehouse:
        incoming = Shipment.list_at_warehouse(warehouse["id"], status="In Transit")
        at_warehouse_now = Shipment.list_at_warehouse(warehouse["id"], status="At Warehouse")
        outgoing_unassigned = Shipment.unassigned_at_warehouse(warehouse["id"])
        agents_available = DeliveryAgent.count_available(warehouse["id"])
    else:
        incoming, at_warehouse_now, outgoing_unassigned, agents_available = [], [], [], 0
    return dict(
        name=session.get("user_name"), role="warehouse_staff",
        warehouse=warehouse, incoming=incoming, at_warehouse_now=at_warehouse_now,
        outgoing_unassigned=outgoing_unassigned, agents_available=agents_available,
    )


def _admin_dashboard_context():
    all_shipments = Shipment.list_all(limit=10)
    total_shipments = Shipment.count_all()
    delivered_count = Shipment.count_delivered()
    active_agents = DeliveryAgent.count_available()
    warehouse_count = Warehouse.count_all()
    delivered_rate = round((delivered_count / total_shipments) * 100, 1) if total_shipments else 0
    return dict(
        name=session.get("user_name"), role="admin",
        all_shipments=all_shipments, total_shipments=total_shipments,
        active_agents=active_agents, warehouse_count=warehouse_count,
        delivered_rate=delivered_rate,
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

        if user:
            token = User.set_reset_token(email)
            reset_link = url_for("auth.reset_password", token=token, _external=True)
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
    templates link to via url_for('auth.dashboard')."""
    if "user_id" not in session:
        flash("Please log in to continue.", "danger")
        return redirect(url_for("auth.login"))

    role = session.get("user_role")
    template_name = DASHBOARD_TEMPLATES.get(role, "dashboard_customer.html")
    context_builder = _CONTEXT_BUILDERS.get(role, _customer_dashboard_context)
    return render_template(template_name, **context_builder())


# ============================================================
# SPECIFIC ROLE-PROTECTED ROUTES (requirement #5)
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
# DEMO ROLE SWITCHER — testing/demonstration only. Never writes
# to the database — session-only role override.
# ============================================================

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