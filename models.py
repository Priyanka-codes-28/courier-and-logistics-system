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
    
    @staticmethod
    def update_profile(user_id, name, phone, email):
        """Updates name, phone, and email for the Profile & Account page.
        Checks the new email isn't already used by a different account."""
        db = get_db()
        with db.cursor() as cur:
            cur.execute("SELECT id FROM users WHERE email = %s AND id != %s", (email, user_id))
            if cur.fetchone():
                return False, "That email is already used by another account."
            cur.execute(
                "UPDATE users SET name = %s, phone = %s, email = %s WHERE id = %s",
                (name, phone, email, user_id),
            )
            return True, "Profile updated successfully."
    
    # ---------- Password reset ----------

    @staticmethod
    def set_reset_token(email):
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


# Order of statuses in the shipment lifecycle
STATUS_FLOW = [
    "Created",
    "Picked Up",
    "At Warehouse",
    "In Transit",
    "Out for Delivery",
    "Delivered",
]


class Shipment:
    """Handles all DB operations for the shipments table and its status history."""

    @staticmethod
    def generate_tracking_id():
        date_part = datetime.now().strftime("%Y%m%d")
        rand_part = secrets.token_hex(2).upper()
        return f"CL{date_part}{rand_part}"

    @staticmethod
    def create(sender_id, receiver_name, receiver_address, receiver_phone,
               package_type=None, weight=None, sender_name=None, sender_address=None,
               sender_phone=None, origin_warehouse_id=None, destination_warehouse_id=None):
        db = get_db()
        tracking_id = Shipment.generate_tracking_id()
        with db.cursor() as cur:
            cur.execute(
                """INSERT INTO shipments
                   (tracking_id, sender_id, sender_name, sender_address, sender_phone,
                    receiver_name, receiver_address, receiver_phone, package_type, weight,
                    origin_warehouse_id, destination_warehouse_id, status)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'Created')
                   RETURNING id""",
                (tracking_id, sender_id, sender_name, sender_address, sender_phone,
                 receiver_name, receiver_address, receiver_phone, package_type, weight,
                 origin_warehouse_id, destination_warehouse_id),
            )
            shipment_id = cur.fetchone()["id"]
            cur.execute(
                """INSERT INTO shipment_status_history (shipment_id, status, location, updated_by)
                   VALUES (%s, 'Created', 'Order placed', %s)""",
                (shipment_id, sender_id),
            )
        return tracking_id

    @staticmethod
    def find_by_tracking_id(tracking_id):
        db = get_db()
        with db.cursor() as cur:
            cur.execute("SELECT * FROM shipments WHERE tracking_id = %s", (tracking_id.strip().upper(),))
            return cur.fetchone()

    @staticmethod
    def find_by_id(shipment_id):
        db = get_db()
        with db.cursor() as cur:
            cur.execute("SELECT * FROM shipments WHERE id = %s", (shipment_id,))
            return cur.fetchone()

    @staticmethod
    def list_by_sender(sender_id):
        db = get_db()
        with db.cursor() as cur:
            cur.execute(
                "SELECT * FROM shipments WHERE sender_id = %s ORDER BY created_at DESC",
                (sender_id,),
            )
            return cur.fetchall()

    @staticmethod
    def get_status_history(shipment_id):
        db = get_db()
        with db.cursor() as cur:
            cur.execute(
                "SELECT * FROM shipment_status_history WHERE shipment_id = %s ORDER BY timestamp ASC",
                (shipment_id,),
            )
            return cur.fetchall()

    @staticmethod
    def update_status(shipment_id, new_status, location=None, remarks=None, updated_by=None):
        db = get_db()
        with db.cursor() as cur:
            cur.execute("UPDATE shipments SET status = %s WHERE id = %s", (new_status, shipment_id))
            cur.execute(
                """INSERT INTO shipment_status_history (shipment_id, status, location, remarks, updated_by)
                   VALUES (%s, %s, %s, %s, %s)""",
                (shipment_id, new_status, location, remarks, updated_by),
            )

    @staticmethod
    def progress_percent(status):
        try:
            idx = STATUS_FLOW.index(status)
            return int((idx / (len(STATUS_FLOW) - 1)) * 100)
        except ValueError:
            return 0

    @staticmethod
    def next_status(current_status):
        try:
            idx = STATUS_FLOW.index(current_status)
            if idx + 1 < len(STATUS_FLOW):
                return STATUS_FLOW[idx + 1]
        except ValueError:
            pass
        return None

    @staticmethod
    def list_all(limit=50):
        db = get_db()
        with db.cursor() as cur:
            cur.execute("SELECT * FROM shipments ORDER BY created_at DESC LIMIT %s", (limit,))
            return cur.fetchall()

    @staticmethod
    def list_assigned_to_agent(agent_id):
        db = get_db()
        with db.cursor() as cur:
            cur.execute(
                """SELECT s.*, sa.status AS assignment_status, sa.assigned_at
                   FROM shipments s
                   JOIN shipment_assignments sa ON sa.shipment_id = s.id
                   WHERE sa.agent_id = %s
                   ORDER BY sa.assigned_at DESC""",
                (agent_id,),
            )
            return cur.fetchall()

    @staticmethod
    def list_at_warehouse(warehouse_id, status=None):
        db = get_db()
        with db.cursor() as cur:
            if status:
                cur.execute(
                    """SELECT * FROM shipments
                       WHERE (origin_warehouse_id = %s OR destination_warehouse_id = %s)
                       AND status = %s ORDER BY created_at DESC""",
                    (warehouse_id, warehouse_id, status),
                )
            else:
                cur.execute(
                    """SELECT * FROM shipments
                       WHERE origin_warehouse_id = %s OR destination_warehouse_id = %s
                       ORDER BY created_at DESC""",
                    (warehouse_id, warehouse_id),
                )
            return cur.fetchall()

    @staticmethod
    def unassigned_at_warehouse(warehouse_id):
        db = get_db()
        with db.cursor() as cur:
            cur.execute(
                """SELECT s.* FROM shipments s
                   WHERE s.origin_warehouse_id = %s AND s.status = 'At Warehouse'
                   AND NOT EXISTS (
                       SELECT 1 FROM shipment_assignments sa WHERE sa.shipment_id = s.id
                   )
                   ORDER BY s.created_at ASC""",
                (warehouse_id,),
            )
            return cur.fetchall()

    @staticmethod
    def count_all():
        db = get_db()
        with db.cursor() as cur:
            cur.execute("SELECT COUNT(*) AS c FROM shipments")
            return cur.fetchone()["c"]

    @staticmethod
    def count_delivered():
        db = get_db()
        with db.cursor() as cur:
            cur.execute("SELECT COUNT(*) AS c FROM shipments WHERE status = 'Delivered'")
            return cur.fetchone()["c"]


class DeliveryAgent:
    """Handles delivery_agents table — one row per agent, linked to a user account."""

    @staticmethod
    def find_by_user_id(user_id):
        db = get_db()
        with db.cursor() as cur:
            cur.execute("SELECT * FROM delivery_agents WHERE user_id = %s", (user_id,))
            return cur.fetchone()

    @staticmethod
    def get_or_create(user_id, warehouse_id=None):
        agent = DeliveryAgent.find_by_user_id(user_id)
        if agent:
            return agent
        db = get_db()
        with db.cursor() as cur:
            cur.execute(
                """INSERT INTO delivery_agents (user_id, warehouse_id, is_available)
                   VALUES (%s, %s, TRUE) RETURNING *""",
                (user_id, warehouse_id),
            )
            return cur.fetchone()

    @staticmethod
    def count_available(warehouse_id=None):
        db = get_db()
        with db.cursor() as cur:
            if warehouse_id:
                cur.execute(
                    "SELECT COUNT(*) AS c FROM delivery_agents WHERE is_available = TRUE AND warehouse_id = %s",
                    (warehouse_id,),
                )
            else:
                cur.execute("SELECT COUNT(*) AS c FROM delivery_agents WHERE is_available = TRUE")
            return cur.fetchone()["c"]

    @staticmethod
    def count_all():
        db = get_db()
        with db.cursor() as cur:
            cur.execute("SELECT COUNT(*) AS c FROM delivery_agents")
            return cur.fetchone()["c"]


class Warehouse:
    """Handles warehouses table."""

    @staticmethod
    def find_by_id(warehouse_id):
        db = get_db()
        with db.cursor() as cur:
            cur.execute("SELECT * FROM warehouses WHERE id = %s", (warehouse_id,))
            return cur.fetchone()

    @staticmethod
    def get_first():
        db = get_db()
        with db.cursor() as cur:
            cur.execute("SELECT * FROM warehouses ORDER BY id ASC LIMIT 1")
            return cur.fetchone()

    @staticmethod
    def count_all():
        db = get_db()
        with db.cursor() as cur:
            cur.execute("SELECT COUNT(*) AS c FROM warehouses")
            return cur.fetchone()["c"]