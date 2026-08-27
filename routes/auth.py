from flask import Blueprint

auth_bp = Blueprint("auth", __name__)


# Stub routes — just enough for the landing page's Login / Sign Up links to work.
# Replace these with real registration/login logic (with MySQL) when you build that module.

@auth_bp.route("/login")
def login():
    return "<h2>Login page — coming soon</h2><p><a href='/'>Back to home</a></p>"


@auth_bp.route("/register")
def register():
    return "<h2>Register page — coming soon</h2><p><a href='/'>Back to home</a></p>"