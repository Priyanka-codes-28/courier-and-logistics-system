from flask import Blueprint, render_template, request, redirect, url_for, session, flash
from models import User

auth_bp = Blueprint("auth", __name__)


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


@auth_bp.route("/dashboard")
def dashboard():
    if "user_id" not in session:
        flash("Please log in to continue.", "danger")
        return redirect(url_for("auth.login"))

    role = session.get("user_role")
    templates = {
        "customer": "dashboard_customer.html",
        "delivery_agent": "dashboard_agent.html",
        "warehouse_staff": "dashboard_warehouse.html",
        "admin": "dashboard_admin.html",
    }
    template_name = templates.get(role, "dashboard_customer.html")
    return render_template(template_name, name=session.get("user_name"), role=role)