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
    def find_first_by_role(role):
        db = get_db()
        with db.cursor() as cur:
            cur.execute("SELECT * FROM users WHERE role = %s ORDER BY id LIMIT 1", (role,))
            return cur.fetchone()

    @staticmethod
    def find_any():
        db = get_db()
        with db.cursor() as cur:
            cur.execute("SELECT * FROM users ORDER BY id LIMIT 1")
            return cur.fetchone()

    @staticmethod
    def update_profile(user_id, name, phone, email):
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

    @staticmethod
    def list_all(limit=20):
        db = get_db()
        with db.cursor() as cur:
            cur.execute("SELECT id, name, email, role FROM users ORDER BY created_at DESC LIMIT %s", (limit,))
            return cur.fetchall()

    @staticmethod
    def list_for_management(search=None, role=None, status=None):
        db = get_db()
        query = "SELECT id, name, email, phone, role, status, created_at FROM users WHERE 1=1"
        params = []

        if search:
            query += " AND (name ILIKE %s OR email ILIKE %s)"
            like = f"%{search}%"
            params.extend([like, like])
        if role:
            query += " AND role = %s"
            params.append(role)
        if status:
            query += " AND status = %s"
            params.append(status)

        query += " ORDER BY created_at DESC"
        with db.cursor() as cur:
            cur.execute(query, tuple(params))
            return cur.fetchall()

    @staticmethod
    def update_details(user_id, name, email, phone, role):
        db = get_db()
        with db.cursor() as cur:
            cur.execute("SELECT id FROM users WHERE email = %s AND id != %s", (email, user_id))
            if cur.fetchone():
                return False, "That email is already used by another account."
            cur.execute(
                "UPDATE users SET name = %s, email = %s, phone = %s, role = %s WHERE id = %s",
                (name, email, phone, role, user_id),
            )
            return True, "User updated successfully."

    @staticmethod
    def set_status(user_id, status):
        db = get_db()
        with db.cursor() as cur:
            cur.execute("UPDATE users SET status = %s WHERE id = %s", (status, user_id))

    @staticmethod
    def has_shipment_history(user_id):
        db = get_db()
        with db.cursor() as cur:
            cur.execute("SELECT 1 FROM shipments WHERE sender_id = %s LIMIT 1", (user_id,))
            if cur.fetchone():
                return True
            cur.execute(
                """SELECT 1 FROM shipment_assignments sa
                   JOIN delivery_agents da ON da.id = sa.agent_id
                   WHERE da.user_id = %s LIMIT 1""",
                (user_id,),
            )
            return cur.fetchone() is not None

    @staticmethod
    def delete(user_id):
        db = get_db()
        with db.cursor() as cur:
            cur.execute("DELETE FROM users WHERE id = %s", (user_id,))

    # Password reset

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
STATUS_FLOW = [
    "Created",
    "Awaiting Pickup",
    "Picked Up",
    "In Transit",
    "Arrived at Warehouse",
    "Processing",
    "Ready for Dispatch",
    "Agent Assigned",
    "Out for Delivery",
    "Delivered",
]

EXCEPTION_STATUSES = ["Failed Delivery", "RTO"]


class Shipment:
    """Handles all DB operations for the shipments table and its status history."""

    @staticmethod
    def generate_tracking_id():
        """Format: <prefix> + YYYYMMDD + 4 random uppercase alphanumeric
        chars, e.g. CL20260830X7F2. The prefix now comes from the admin's
        Tracking ID Format setting (default 'CLYYYYMMDDXXXX' -> prefix
        'CL', identical to the original hardcoded behavior)."""
        settings = SystemSettings.load()
        fmt = (settings.get("tracking_id_format") or "CLYYYYMMDDXXXX").strip()
        prefix = fmt.split("YYYY")[0] if "YYYY" in fmt else fmt
        prefix = prefix or "CL"
        date_part = datetime.now().strftime("%Y%m%d")
        rand_part = secrets.token_hex(2).upper()
        return f"{prefix}{date_part}{rand_part}"

    @staticmethod
    def create(sender_id, receiver_name, receiver_address, receiver_phone,
               package_type=None, weight=None, sender_name=None, sender_address=None,
               sender_phone=None, package_description=None,
               origin_warehouse_id=None, destination_warehouse_id=None):
        db = get_db()
        tracking_id = Shipment.generate_tracking_id()
        with db.cursor() as cur:
            cur.execute(
                """INSERT INTO shipments
                   (tracking_id, sender_id, sender_name, sender_address, sender_phone,
                    receiver_name, receiver_address, receiver_phone, package_type,
                    package_description, weight,
                    origin_warehouse_id, destination_warehouse_id, status)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'Created')
                   RETURNING id""",
                (tracking_id, sender_id, sender_name, sender_address, sender_phone,
                 receiver_name, receiver_address, receiver_phone, package_type,
                 package_description, weight,
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
    def list_all(limit=50):
        db = get_db()
        with db.cursor() as cur:
            cur.execute("SELECT * FROM shipments ORDER BY created_at DESC LIMIT %s", (limit,))
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
    def get_last_update_timestamp(shipment_id):
        db = get_db()
        with db.cursor() as cur:
            cur.execute(
                "SELECT MAX(timestamp) AS last_updated FROM shipment_status_history WHERE shipment_id = %s",
                (shipment_id,),
            )
            row = cur.fetchone()
            return row["last_updated"] if row else None

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
    def is_valid_transition(current_status, requested_status):
        if current_status == "Delivered":
            return False 

        if requested_status in EXCEPTION_STATUSES:
            return True

        return requested_status == Shipment.next_status(current_status)

    @staticmethod
    def list_assigned_to_agent(agent_id):
        
        db = get_db()
        with db.cursor() as cur:
            cur.execute(
                """SELECT * FROM (
                       SELECT DISTINCT ON (s.id) s.*, sa.status AS assignment_status,
                              sa.assigned_at, sa.agent_role
                       FROM shipments s
                       JOIN shipment_assignments sa ON sa.shipment_id = s.id
                       WHERE sa.agent_id = %s
                       ORDER BY s.id, sa.assigned_at DESC
                   ) sub
                   ORDER BY assigned_at DESC""",
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
    def list_incoming_at_warehouse(warehouse_id):
        
        db = get_db()
        with db.cursor() as cur:
            cur.execute(
                """SELECT s.*, u.name AS customer_name
                   FROM shipments s
                   JOIN users u ON u.id = s.sender_id
                   WHERE (s.origin_warehouse_id = %s OR s.destination_warehouse_id = %s)
                   AND s.status = 'Arrived at Warehouse'
                   ORDER BY s.created_at ASC""",
                (warehouse_id, warehouse_id),
            )
            return cur.fetchall()

    @staticmethod
    def unassigned_at_warehouse(warehouse_id):
        
        db = get_db()
        with db.cursor() as cur:
            cur.execute(
                """SELECT s.* FROM shipments s
                   WHERE s.origin_warehouse_id = %s AND s.status = 'Ready for Dispatch'
                   AND NOT EXISTS (
                       SELECT 1 FROM shipment_assignments sa
                       WHERE sa.shipment_id = s.id AND sa.agent_role = 'delivery'
                   )
                   ORDER BY s.created_at ASC""",
                (warehouse_id,),
            )
            return cur.fetchall()

    @staticmethod
    def create_assignment(shipment_id, agent_id, agent_role="pickup"):
    
        db = get_db()
        with db.cursor() as cur:
            cur.execute(
                "INSERT INTO shipment_assignments (shipment_id, agent_id, agent_role) VALUES (%s, %s, %s)",
                (shipment_id, agent_id, agent_role),
            )

    @staticmethod
    def get_agent_name_by_role(shipment_id, agent_role):
        """Real agent name for a specific role on this shipment, or None
        if that role hasn't been assigned yet."""
        db = get_db()
        with db.cursor() as cur:
            cur.execute(
                """SELECT u.name FROM shipment_assignments sa
                   JOIN delivery_agents da ON da.id = sa.agent_id
                   JOIN users u ON u.id = da.user_id
                   WHERE sa.shipment_id = %s AND sa.agent_role = %s
                   ORDER BY sa.assigned_at DESC LIMIT 1""",
                (shipment_id, agent_role),
            )
            row = cur.fetchone()
            return row["name"] if row else None

    @staticmethod
    def sync_assignment_status(shipment_id, new_shipment_status):
        
        db = get_db()
        with db.cursor() as cur:
            if new_shipment_status == "Picked Up":
                cur.execute(
                    """UPDATE shipment_assignments SET status = 'picked_up'
                       WHERE shipment_id = %s AND agent_role = 'pickup' AND status = 'assigned'""",
                    (shipment_id,),
                )
            elif new_shipment_status == "Delivered":
                cur.execute(
                    "UPDATE shipment_assignments SET status = 'delivered' WHERE shipment_id = %s",
                    (shipment_id,),
                )
            elif new_shipment_status in ("Failed Delivery", "RTO"):
                cur.execute(
                    """UPDATE shipment_assignments SET status = 'failed'
                       WHERE shipment_id = %s AND agent_role = 'delivery'""",
                    (shipment_id,),
                )

    @staticmethod
    def get_agent_id_by_role(shipment_id, agent_role):
        
        db = get_db()
        with db.cursor() as cur:
            cur.execute(
                """SELECT agent_id FROM shipment_assignments
                   WHERE shipment_id = %s AND agent_role = %s
                   ORDER BY assigned_at DESC LIMIT 1""",
                (shipment_id, agent_role),
            )
            row = cur.fetchone()
            return row["agent_id"] if row else None

    @staticmethod
    def list_waiting_for_delivery_agent():
        
        db = get_db()
        with db.cursor() as cur:
            cur.execute(
                """SELECT s.* FROM shipments s
                   WHERE s.status = 'Ready for Dispatch'
                   AND NOT EXISTS (
                       SELECT 1 FROM shipment_assignments sa
                       WHERE sa.shipment_id = s.id AND sa.agent_role = 'delivery'
                   )
                   ORDER BY s.created_at ASC"""
            )
            return cur.fetchall()

    @staticmethod
    def try_auto_assign_delivery_agent(shipment_id):
        
        settings = SystemSettings.load()
        if not settings.get("auto_assign_agents", True):
            return False

        agent_id = DeliveryAgent.find_first_available()
        if not agent_id:
            return False

        Shipment.create_assignment(shipment_id, agent_id, agent_role="delivery")
        DeliveryAgent.set_busy_by_agent_id(agent_id)

        agent_user_id = DeliveryAgent.get_user_id(agent_id)
        if agent_user_id and settings.get("in_app_notifications_enabled", True):
            Notification.create(
                user_id=agent_user_id,
                shipment_id=shipment_id,
                message="New final delivery automatically assigned to you.",
            )

        if settings.get("in_app_notifications_enabled", True):
            shipment_row = Shipment.find_by_id(shipment_id)
            warehouse_id = None
            if shipment_row:
                warehouse_id = shipment_row.get("origin_warehouse_id") or shipment_row.get("destination_warehouse_id")
            if warehouse_id:
                agent_user = User.find_by_id(agent_user_id) if agent_user_id else None
                agent_name = agent_user["name"] if agent_user else f"Agent #{agent_id}"
                tracking_id = shipment_row["tracking_id"] if shipment_row else shipment_id
                Notification.create(
                    shipment_id=shipment_id,
                    warehouse_id=warehouse_id,
                    title="Delivery Agent Assigned",
                    notif_type="agent_assigned",
                    message=f"Agent {agent_name} has been automatically assigned to shipment {tracking_id}.",
                )

        Shipment.update_status(
            shipment_id, "Agent Assigned",
            location="Final delivery agent auto-assigned", updated_by=None,
        )
        return True

    @staticmethod
    def try_assign_waiting_shipments():
        
        waiting = Shipment.list_waiting_for_delivery_agent()
        assigned_count = 0
        for shipment in waiting:
            if Shipment.try_auto_assign_delivery_agent(shipment["id"]):
                assigned_count += 1
            else:
                break  
        return assigned_count

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

    @staticmethod
    def status_breakdown():
        db = get_db()
        with db.cursor() as cur:
            cur.execute("SELECT COUNT(*) AS c FROM shipments WHERE status = 'Delivered'")
            delivered = cur.fetchone()["c"]
            cur.execute(
                """SELECT COUNT(*) AS c FROM shipments
                   WHERE status IN ('Picked Up','In Transit','Arrived at Warehouse','Processing','Ready for Dispatch','Agent Assigned')"""
            )
            in_transit = cur.fetchone()["c"]
            cur.execute("SELECT COUNT(*) AS c FROM shipments WHERE status = 'Out for Delivery'")
            out_for_delivery = cur.fetchone()["c"]
            cur.execute("SELECT COUNT(*) AS c FROM shipments WHERE status IN ('Created', 'Awaiting Pickup')")
            pending = cur.fetchone()["c"]
            cur.execute("SELECT COUNT(*) AS c FROM shipments WHERE status IN ('Failed Delivery','RTO')")
            failed = cur.fetchone()["c"]
            return dict(delivered=delivered, in_transit=in_transit,
                        out_for_delivery=out_for_delivery, pending=pending, failed=failed)

    @staticmethod
    def deliveries_this_week():

        db = get_db()
        with db.cursor() as cur:
            cur.execute(
                """SELECT to_char(day, 'Dy') AS label, COALESCE(c.cnt, 0) AS count
                   FROM generate_series(CURRENT_DATE - INTERVAL '6 days', CURRENT_DATE, INTERVAL '1 day') AS day
                   LEFT JOIN (
                       SELECT h.timestamp::date AS d, COUNT(DISTINCT h.shipment_id) AS cnt
                       FROM shipment_status_history h
                       WHERE h.status = 'Delivered' AND h.timestamp >= CURRENT_DATE - INTERVAL '6 days'
                       GROUP BY h.timestamp::date
                   ) c ON c.d = day
                   ORDER BY day"""
            )
            return cur.fetchall()

    @staticmethod
    def list_all_with_sender(limit=10):
    
        db = get_db()
        with db.cursor() as cur:
            cur.execute(
                """SELECT s.*, u.name AS customer_name FROM shipments s
                   JOIN users u ON u.id = s.sender_id
                   ORDER BY s.created_at DESC LIMIT %s""",
                (limit,),
            )
            return cur.fetchall()

    @staticmethod
    def count_unassigned_system_wide():
    
        db = get_db()
        with db.cursor() as cur:
            cur.execute(
                """SELECT COUNT(*) AS c FROM shipments s
                   WHERE s.status = 'Ready for Dispatch'
                   AND NOT EXISTS (
                       SELECT 1 FROM shipment_assignments sa
                       WHERE sa.shipment_id = s.id AND sa.agent_role = 'delivery'
                   )"""
            )
            return cur.fetchone()["c"]

    @staticmethod
    def get_current_for_sender(sender_id):
        
        db = get_db()
        with db.cursor() as cur:
            cur.execute(
                """SELECT * FROM shipments WHERE sender_id = %s AND status != 'Delivered'
                   ORDER BY created_at DESC LIMIT 1""",
                (sender_id,),
            )
            active = cur.fetchone()
            if active:
                return active
            cur.execute(
                "SELECT * FROM shipments WHERE sender_id = %s ORDER BY created_at DESC LIMIT 1",
                (sender_id,),
            )
            return cur.fetchone()

    @staticmethod
    def get_latest_location(shipment_id):
        
        db = get_db()
        with db.cursor() as cur:
            cur.execute(
                """SELECT location FROM shipment_status_history
                   WHERE shipment_id = %s AND location IS NOT NULL
                   ORDER BY timestamp DESC LIMIT 1""",
                (shipment_id,),
            )
            row = cur.fetchone()
            return row["location"] if row else None

    @staticmethod
    def get_assigned_agent_name(shipment_id):
        """Real agent name if this shipment has been assigned, else None."""
        db = get_db()
        with db.cursor() as cur:
            cur.execute(
                """SELECT u.name FROM shipment_assignments sa
                   JOIN delivery_agents da ON da.id = sa.agent_id
                   JOIN users u ON u.id = da.user_id
                   WHERE sa.shipment_id = %s
                   ORDER BY sa.assigned_at DESC LIMIT 1""",
                (shipment_id,),
            )
            row = cur.fetchone()
            return row["name"] if row else None

    @staticmethod
    def get_current_for_agent(agent_id):
        
        db = get_db()
        with db.cursor() as cur:
            cur.execute(
                """SELECT s.* FROM shipments s
                   JOIN shipment_assignments sa ON sa.shipment_id = s.id
                   WHERE sa.agent_id = %s AND s.status NOT IN ('Delivered', 'Failed Delivery', 'RTO')
                   ORDER BY sa.assigned_at DESC LIMIT 1""",
                (agent_id,),
            )
            active = cur.fetchone()
            if active:
                return active
            cur.execute(
                """SELECT s.* FROM shipments s
                   JOIN shipment_assignments sa ON sa.shipment_id = s.id
                   WHERE sa.agent_id = %s
                   ORDER BY sa.assigned_at DESC LIMIT 1""",
                (agent_id,),
            )
            return cur.fetchone()

    @staticmethod
    def list_delivery_history_for_agent(agent_id, limit=10):
        """Completed and failed deliveries handled by this agent."""
        db = get_db()
        with db.cursor() as cur:
            cur.execute(
                """SELECT s.tracking_id, s.status,
                          (SELECT MAX(h.timestamp) FROM shipment_status_history h
                           WHERE h.shipment_id = s.id AND h.status = s.status) AS finished_at
                   FROM shipments s
                   JOIN shipment_assignments sa ON sa.shipment_id = s.id
                   WHERE sa.agent_id = %s AND s.status IN ('Delivered', 'Failed Delivery', 'RTO')
                   ORDER BY finished_at DESC LIMIT %s""",
                (agent_id, limit),
            )
            return cur.fetchall()

    @staticmethod
    def update_location_only(shipment_id, location, updated_by):
        
        db = get_db()
        with db.cursor() as cur:
            cur.execute("SELECT status FROM shipments WHERE id = %s", (shipment_id,))
            current_status = cur.fetchone()["status"]
            cur.execute(
                """INSERT INTO shipment_status_history (shipment_id, status, location, updated_by)
                   VALUES (%s, %s, %s, %s)""",
                (shipment_id, current_status, location, updated_by),
            )

    # Live Tracking Map
    ETA_BY_STATUS = {
        "Created": "Pending pickup assignment",
        "Awaiting Pickup": "Pickup scheduled shortly",
        "Picked Up": "1-2 days",
        "In Transit": "1-2 days",
        "Arrived at Warehouse": "Processing at warehouse",
        "Processing": "Processing at warehouse",
        "Ready for Dispatch": "Awaiting delivery agent",
        "Agent Assigned": "Out for delivery shortly",
        "Out for Delivery": "30-60 minutes",
        "Delivered": "Delivered",
        "Failed Delivery": "Delivery attempt failed — rescheduling",
        "RTO": "Returning to origin",
    }

    @staticmethod
    def eta_label(status):
        return Shipment.ETA_BY_STATUS.get(status, "Calculating...")


class DeliveryAgent:

    @staticmethod
    def find_by_user_id(user_id):
        db = get_db()
        with db.cursor() as cur:
            cur.execute("SELECT * FROM delivery_agents WHERE user_id = %s", (user_id,))
            return cur.fetchone()

    @staticmethod
    def get_user_id(agent_id):
        db = get_db()
        with db.cursor() as cur:
            cur.execute("SELECT user_id FROM delivery_agents WHERE id = %s", (agent_id,))
            row = cur.fetchone()
            return row["user_id"] if row else None

    @staticmethod
    def get_or_create(user_id, warehouse_id=None):
        
        agent = DeliveryAgent.find_by_user_id(user_id)
        if agent:
            return agent
        if warehouse_id is None:
            wh = Warehouse.get_first()
            warehouse_id = wh["id"] if wh else None
        db = get_db()
        with db.cursor() as cur:
            cur.execute(
                """INSERT INTO delivery_agents (user_id, warehouse_id, is_available)
                   VALUES (%s, %s, TRUE) RETURNING *""",
                (user_id, warehouse_id),
            )
            return cur.fetchone()

    @staticmethod
    def set_availability(user_id, is_available):
        
        db = get_db()
        with db.cursor() as cur:
            cur.execute(
                "UPDATE delivery_agents SET is_available = %s WHERE user_id = %s",
                (is_available, user_id),
            )

    @staticmethod
    def set_busy_by_agent_id(agent_id):
        
        db = get_db()
        with db.cursor() as cur:
            cur.execute("UPDATE delivery_agents SET is_available = FALSE WHERE id = %s", (agent_id,))

    @staticmethod
    def set_free_by_agent_id(agent_id):
        
        db = get_db()
        with db.cursor() as cur:
            cur.execute("UPDATE delivery_agents SET is_available = TRUE WHERE id = %s", (agent_id,))

    @staticmethod
    def is_assigned_to_shipment(agent_id, shipment_id):
        
        db = get_db()
        with db.cursor() as cur:
            cur.execute(
                "SELECT 1 FROM shipment_assignments WHERE agent_id = %s AND shipment_id = %s LIMIT 1",
                (agent_id, shipment_id),
            )
            return cur.fetchone() is not None

    @staticmethod
    def has_active_assignment(agent_id):
        
        db = get_db()
        with db.cursor() as cur:
            cur.execute(
                "SELECT 1 FROM shipment_assignments WHERE agent_id = %s AND status IN ('assigned', 'picked_up') LIMIT 1",
                (agent_id,),
            )
            return cur.fetchone() is not None

    @staticmethod
    def list_availability_by_user_id():
        
        db = get_db()
        with db.cursor() as cur:
            cur.execute(
                """SELECT da.user_id, da.is_available,
                          EXISTS (
                              SELECT 1 FROM shipment_assignments sa
                              WHERE sa.agent_id = da.id AND sa.status IN ('assigned', 'picked_up')
                          ) AS has_active_job
                   FROM delivery_agents da"""
            )
            result = {}
            for row in cur.fetchall():
                if row["is_available"]:
                    label = "Available"
                elif row["has_active_job"]:
                    label = "Busy"
                else:
                    label = "Offline"
                result[row["user_id"]] = label
            return result

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

    @staticmethod
    def find_first_available():
        
        db = get_db()
        settings = SystemSettings.load()
        max_active = settings.get("max_active_shipments_per_agent") or 5
        with db.cursor() as cur:
            cur.execute(
                """SELECT da.id,
                          COUNT(sa.id) FILTER (WHERE sa.status IN ('assigned','picked_up')) AS active_count
                   FROM delivery_agents da
                   LEFT JOIN shipment_assignments sa ON sa.agent_id = da.id
                   WHERE da.is_available = TRUE
                   GROUP BY da.id
                   HAVING COUNT(sa.id) FILTER (WHERE sa.status IN ('assigned','picked_up')) < %s
                   ORDER BY active_count ASC, da.id ASC
                   LIMIT 1""",
                (max_active,)
            )
            row = cur.fetchone()
            if row:
                return row["id"]

            cur.execute(
                """SELECT u.id FROM users u
                   LEFT JOIN delivery_agents da ON da.user_id = u.id
                   WHERE u.role = 'delivery_agent' AND da.id IS NULL
                   ORDER BY u.id LIMIT 1"""
            )
            missing = cur.fetchone()
            if missing:
                new_agent = DeliveryAgent.get_or_create(missing["id"])
                return new_agent["id"]

            return None

    @staticmethod
    def list_all_with_names():
        
        db = get_db()
        with db.cursor() as cur:
            cur.execute(
                """SELECT da.id, u.name, da.is_available
                   FROM delivery_agents da
                   JOIN users u ON da.user_id = u.id
                   ORDER BY u.name"""
            )
            return cur.fetchall()

    @staticmethod
    def list_available_with_names():
        
        db = get_db()
        with db.cursor() as cur:
            cur.execute(
                """SELECT da.id, u.name
                   FROM delivery_agents da
                   JOIN users u ON da.user_id = u.id
                   WHERE da.is_available = TRUE
                   ORDER BY u.name"""
            )
            return cur.fetchall()

    @staticmethod
    def is_available(agent_id):
        
        db = get_db()
        with db.cursor() as cur:
            cur.execute("SELECT is_available FROM delivery_agents WHERE id = %s", (agent_id,))
            row = cur.fetchone()
            return bool(row["is_available"]) if row else False

    @staticmethod
    def count_busy():
        
        db = get_db()
        with db.cursor() as cur:
            cur.execute(
                """SELECT COUNT(DISTINCT da.id) AS c
                   FROM delivery_agents da
                   JOIN shipment_assignments sa ON sa.agent_id = da.id
                   WHERE sa.status IN ('assigned', 'picked_up')"""
            )
            return cur.fetchone()["c"]

    @staticmethod
    def count_offline():
        db = get_db()
        with db.cursor() as cur:
            cur.execute("SELECT COUNT(*) AS c FROM delivery_agents WHERE is_available = FALSE")
            return cur.fetchone()["c"]


class Warehouse:
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

    @staticmethod
    def list_all_with_counts():
        
        db = get_db()
        with db.cursor() as cur:
            cur.execute(
                """SELECT w.id, w.name,
                          COUNT(s.id) AS shipment_count
                   FROM warehouses w
                   LEFT JOIN shipments s
                     ON s.origin_warehouse_id = w.id OR s.destination_warehouse_id = w.id
                   GROUP BY w.id, w.name
                   ORDER BY w.name"""
            )
            return cur.fetchall()


class Notification:
    
    @staticmethod
    def create(user_id=None, shipment_id=None, message=None, channel="app",
               title=None, notif_type=None, warehouse_id=None):
        db = get_db()
        with db.cursor() as cur:
            cur.execute(
                """INSERT INTO notifications (user_id, shipment_id, message, channel, title, type, warehouse_id)
                   VALUES (%s, %s, %s, %s, %s, %s, %s)""",
                (user_id, shipment_id, message, channel, title, notif_type, warehouse_id),
            )

    @staticmethod
    def list_for_user(user_id, limit=5):
        db = get_db()
        with db.cursor() as cur:
            cur.execute(
                """SELECT n.*, s.tracking_id FROM notifications n
                   LEFT JOIN shipments s ON s.id = n.shipment_id
                   WHERE n.user_id = %s
                   ORDER BY n.sent_at DESC LIMIT %s""",
                (user_id, limit),
            )
            return cur.fetchall()

    @staticmethod
    def list_all_recent(limit=5):
        
        db = get_db()
        with db.cursor() as cur:
            cur.execute(
                """SELECT n.message, n.sent_at, u.name AS recipient_name
                   FROM notifications n
                   JOIN users u ON u.id = n.user_id
                   ORDER BY n.sent_at DESC LIMIT %s""",
                (limit,),
            )
            return cur.fetchall()

    # Warehouse Notifications

    @staticmethod
    def list_for_warehouse(warehouse_id, limit=50):
        db = get_db()
        with db.cursor() as cur:
            cur.execute(
                """SELECT n.*, s.tracking_id FROM notifications n
                   LEFT JOIN shipments s ON s.id = n.shipment_id
                   WHERE n.warehouse_id = %s
                   ORDER BY n.sent_at DESC LIMIT %s""",
                (warehouse_id, limit),
            )
            return cur.fetchall()

    @staticmethod
    def count_unread_for_warehouse(warehouse_id):
        db = get_db()
        with db.cursor() as cur:
            cur.execute(
                "SELECT COUNT(*) AS c FROM notifications WHERE warehouse_id = %s AND is_read = FALSE",
                (warehouse_id,),
            )
            return cur.fetchone()["c"]

    @staticmethod
    def count_for_warehouse(warehouse_id):
        db = get_db()
        with db.cursor() as cur:
            cur.execute(
                "SELECT COUNT(*) AS c FROM notifications WHERE warehouse_id = %s",
                (warehouse_id,),
            )
            return cur.fetchone()["c"]

    @staticmethod
    def mark_read_for_warehouse(notification_id, warehouse_id):
        
        db = get_db()
        with db.cursor() as cur:
            cur.execute(
                "UPDATE notifications SET is_read = TRUE WHERE id = %s AND warehouse_id = %s",
                (notification_id, warehouse_id),
            )

    @staticmethod
    def mark_all_read_for_warehouse(warehouse_id):
        db = get_db()
        with db.cursor() as cur:
            cur.execute(
                "UPDATE notifications SET is_read = TRUE WHERE warehouse_id = %s AND is_read = FALSE",
                (warehouse_id,),
            )
class DeliveryProof:
    @staticmethod
    def find_by_shipment(shipment_id):
        db = get_db()
        with db.cursor() as cur:
            cur.execute(
                "SELECT * FROM delivery_proof WHERE shipment_id = %s ORDER BY delivered_at DESC LIMIT 1",
                (shipment_id,),
            )
            return cur.fetchone()

    @staticmethod
    def find_latest_for_sender(sender_id):
    
        db = get_db()
        with db.cursor() as cur:
            cur.execute(
                """SELECT dp.*, s.tracking_id, s.receiver_name FROM delivery_proof dp
                   JOIN shipments s ON s.id = dp.shipment_id
                   WHERE s.sender_id = %s
                   ORDER BY dp.delivered_at DESC LIMIT 1""",
                (sender_id,),
            )
            return cur.fetchone()

    @staticmethod
    def create(shipment_id, agent_id, proof_type="photo", file_path=None):
        db = get_db()
        with db.cursor() as cur:
            cur.execute(
                """INSERT INTO delivery_proof (shipment_id, agent_id, proof_type, file_path)
                   VALUES (%s, %s, %s, %s)""",
                (shipment_id, agent_id, proof_type, file_path),
            )


class WarehouseActivity:
    
    @staticmethod
    def count_received_today(warehouse_id):
        db = get_db()
        with db.cursor() as cur:
            cur.execute(
                """SELECT COUNT(DISTINCT h.shipment_id) AS c
                   FROM shipment_status_history h
                   JOIN shipments s ON s.id = h.shipment_id
                   WHERE (s.origin_warehouse_id = %s OR s.destination_warehouse_id = %s)
                   AND h.status IN ('Arrived at Warehouse', 'At Warehouse')
                   AND h.timestamp::date = CURRENT_DATE""",
                (warehouse_id, warehouse_id),
            )
            return cur.fetchone()["c"]

    @staticmethod
    def count_dispatched_today(warehouse_id):
        db = get_db()
        with db.cursor() as cur:
            cur.execute(
                """SELECT COUNT(DISTINCT h.shipment_id) AS c
                   FROM shipment_status_history h
                   JOIN shipments s ON s.id = h.shipment_id
                   WHERE s.origin_warehouse_id = %s
                   AND h.status = 'Out for Delivery'
                   AND h.timestamp::date = CURRENT_DATE""",
                (warehouse_id,),
            )
            return cur.fetchone()["c"]

    @staticmethod
    def list_recent(warehouse_id, limit=4):
        db = get_db()
        with db.cursor() as cur:
            cur.execute(
                """SELECT h.status, h.timestamp, s.tracking_id
                   FROM shipment_status_history h
                   JOIN shipments s ON s.id = h.shipment_id
                   WHERE s.origin_warehouse_id = %s OR s.destination_warehouse_id = %s
                   ORDER BY h.timestamp DESC LIMIT %s""",
                (warehouse_id, warehouse_id, limit),
            )
            return cur.fetchall()

class Payment:  
    FLAT_RATE = 50.00
    PER_KG_RATE = 20.00

    @staticmethod
    def calculate_fee(weight):
        effective_weight = float(weight) if weight else 0.5
        return round(Payment.FLAT_RATE + (effective_weight * Payment.PER_KG_RATE), 2)

    @staticmethod
    def create(shipment_id, amount):
        db = get_db()
        with db.cursor() as cur:
            cur.execute(
                "INSERT INTO payments (shipment_id, amount) VALUES (%s, %s) RETURNING id",
                (shipment_id, amount),
            )
            return cur.fetchone()["id"]

    @staticmethod
    def find_by_shipment(shipment_id):
        db = get_db()
        with db.cursor() as cur:
            cur.execute(
                "SELECT * FROM payments WHERE shipment_id = %s ORDER BY created_at DESC LIMIT 1",
                (shipment_id,),
            )
            return cur.fetchone()

    @staticmethod
    def mark_paid(payment_id, payment_method, transaction_ref):
        db = get_db()
        with db.cursor() as cur:
            cur.execute(
                """UPDATE payments SET status = 'paid', payment_method = %s,
                   transaction_ref = %s, paid_at = CURRENT_TIMESTAMP
                   WHERE id = %s""",
                (payment_method, transaction_ref, payment_id),
            )

    @staticmethod
    def list_pending_for_sender(sender_id):
        db = get_db()
        with db.cursor() as cur:
            cur.execute(
                """SELECT p.*, s.tracking_id FROM payments p
                   JOIN shipments s ON s.id = p.shipment_id
                   WHERE s.sender_id = %s AND p.status = 'pending'
                   ORDER BY p.created_at DESC""",
                (sender_id,),
            )
            return cur.fetchall()

    @staticmethod
    def total_revenue():
        db = get_db()
        with db.cursor() as cur:
            cur.execute("SELECT COALESCE(SUM(amount), 0) AS total FROM payments WHERE status = 'paid'")
            return cur.fetchone()["total"]


class ShipmentLocation:
    
    @staticmethod
    def record(shipment_id, agent_id, latitude, longitude):
        db = get_db()
        with db.cursor() as cur:
            cur.execute(
                """INSERT INTO shipment_locations (shipment_id, agent_id, latitude, longitude)
                   VALUES (%s, %s, %s, %s)""",
                (shipment_id, agent_id, latitude, longitude),
            )

    @staticmethod
    def get_latest(shipment_id):
        
        db = get_db()
        with db.cursor() as cur:
            cur.execute(
                """SELECT latitude, longitude, agent_id, updated_at
                   FROM shipment_locations
                   WHERE shipment_id = %s
                   ORDER BY updated_at DESC LIMIT 1""",
                (shipment_id,),
            )
            return cur.fetchone()

    @staticmethod
    def get_history(shipment_id, limit=50):
        db = get_db()
        with db.cursor() as cur:
            cur.execute(
                """SELECT latitude, longitude, agent_id, updated_at
                   FROM shipment_locations
                   WHERE shipment_id = %s
                   ORDER BY updated_at DESC LIMIT %s""",
                (shipment_id, limit),
            )
            return cur.fetchall()


class SystemSettings:
    
    DEFAULTS = {
        "tracking_id_format": "CLYYYYMMDDXXXX",
        "auto_generate_tracking_id": True,
        "auto_assign_agents": True,
        "agent_assignment_method": "Least Busy Agent",
        "max_active_shipments_per_agent": 5,
        "auto_reassign_on_unavailable": True,
        "in_app_notifications_enabled": True,
        "status_change_notifications_enabled": True,
        "customer_notification_preference": "In-App",
    }

    @staticmethod
    def load():
        db = get_db()
        with db.cursor() as cur:
            cur.execute("SELECT * FROM system_settings WHERE id = 1")
            row = cur.fetchone()
            return dict(row) if row else dict(SystemSettings.DEFAULTS)

    @staticmethod
    def update(values):
        allowed_columns = set(SystemSettings.DEFAULTS.keys())
        columns = [c for c in values if c in allowed_columns]
        if not columns:
            return
        set_clause = ", ".join(f"{c} = %s" for c in columns)
        params = [values[c] for c in columns]
        db = get_db()
        with db.cursor() as cur:
            cur.execute(
                f"UPDATE system_settings SET {set_clause}, updated_at = NOW() WHERE id = 1",
                params,
            )