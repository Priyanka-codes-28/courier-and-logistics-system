import secrets
from datetime import datetime, timedelta
from werkzeug.security import generate_password_hash, check_password_hash
from db import get_db


class User:
    """Handles all DB operations for the users table."""

    @staticmethod
    def create(name, email, phone, password, role="customer"):
        db = get_db()
        password_hash = generate_password_hash(password)
        with db.cursor() as cur:
            cur.execute(
                """INSERT INTO users (name, email, phone, password_hash, role)
                   VALUES (%s, %s, %s, %s, %s) RETURNING id""",
                (name, email, phone, password_hash, role),
            )
            return cur.fetchone()["id"]

    @staticmethod
    def find_by_email(email):
        db = get_db()
        with db.cursor() as cur:
            cur.execute("SELECT * FROM users WHERE email = %s", (email,))
            return cur.fetchone()

    @staticmethod
    def find_by_id(user_id):
        db = get_db()
        with db.cursor() as cur:
            cur.execute("SELECT * FROM users WHERE id = %s", (user_id,))
            return cur.fetchone()

    @staticmethod
    def verify_password(user, password):
        return check_password_hash(user["password_hash"], password)

    # ---------- Password reset ----------

    @staticmethod
    def set_reset_token(email):
        """Generates a reset token valid for 1 hour and stores it against the user."""
        db = get_db()
        token = secrets.token_urlsafe(32)
        expiry = datetime.now() + timedelta(hours=1)
        with db.cursor() as cur:
            cur.execute(
                """UPDATE users SET reset_token = %s, reset_token_expiry = %s
                   WHERE email = %s""",
                (token, expiry, email),
            )
        return token

    @staticmethod
    def find_by_reset_token(token):
        db = get_db()
        with db.cursor() as cur:
            cur.execute(
                """SELECT * FROM users
                   WHERE reset_token = %s AND reset_token_expiry > %s""",
                (token, datetime.now()),
            )
            return cur.fetchone()

    @staticmethod
    def reset_password(user_id, new_password):
        db = get_db()
        password_hash = generate_password_hash(new_password)
        with db.cursor() as cur:
            cur.execute(
                """UPDATE users SET password_hash = %s, reset_token = NULL, reset_token_expiry = NULL
                   WHERE id = %s""",
                (password_hash, user_id),
            )