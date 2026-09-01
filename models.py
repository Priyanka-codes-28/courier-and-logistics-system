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
        """Used by the demo quick-login feature. Returns the first real user
        with this role, or None if no such user exists yet."""
        db = get_db()
        with db.cursor() as cur:
            cur.execute("SELECT * FROM users WHERE role = %s ORDER BY id LIMIT 1", (role,))
            return cur.fetchone()

    @staticmethod
    def find_any():
        """Fallback for demo quick-login when no user of the requested role
        exists at all — picks any real user so session['user_id'] always
        points at a genuine row (keeps foreign keys like shipments.sender_id
        valid even in demo mode)."""
        db = get_db()
        with db.cursor() as cur:
            cur.execute("SELECT * FROM users ORDER BY id LIMIT 1")
            return cur.fetchone()

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

    @staticmethod
    def list_all(limit=20):
        """Used by the Admin dashboard's Manage Users table."""
        db = get_db()
        with db.cursor() as cur:
            cur.execute("SELECT id, name, email, role FROM users ORDER BY created_at DESC LIMIT %s", (limit,))
            return cur.fetchall()

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


# Order of statuses in the shipment lifecycle (Phase 3 — 9-stage flow).
# Used to calculate progress percentage for the live tracking map, and
# to determine the "next" status an agent/warehouse staff can move a
# shipment to.
STATUS_FLOW = [
    "Created",
    "Picked Up",
    "In Transit",
    "Arrived at Warehouse",
    "Processing",
    "Ready for Dispatch",
    "Agent Assigned",
    "Out for Delivery",
    "Delivered",
]

# Exception statuses — valid as a transition from any non-final status,
# not part of the normal forward sequence above.
EXCEPTION_STATUSES = ["Failed Delivery", "RTO"]


class Shipment:
    """Handles all DB operations for the shipments table and its status history."""

    @staticmethod
    def generate_tracking_id():
        """Format: CL + YYYYMMDD + 4 random uppercase alphanumeric chars, e.g. CL20260830X7F2"""
        date_part = datetime.now().strftime("%Y%m%d")
        rand_part = secrets.token_hex(2).upper()
        return f"CL{date_part}{rand_part}"

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
        """Returns 0-100 based on how far along STATUS_FLOW this status is. Used to
        position the marker on the live tracking map."""
        try:
            idx = STATUS_FLOW.index(status)
            return int((idx / (len(STATUS_FLOW) - 1)) * 100)
        except ValueError:
            return 0

    @staticmethod
    def next_status(current_status):
        """Returns the next status in the flow, or None if already at the end
        or the status isn't part of the normal flow (e.g. Failed Delivery, RTO)."""
        try:
            idx = STATUS_FLOW.index(current_status)
            if idx + 1 < len(STATUS_FLOW):
                return STATUS_FLOW[idx + 1]
        except ValueError:
            pass
        return None

    @staticmethod
    def is_valid_transition(current_status, requested_status):
        """Phase 3 requirement: 'Do not allow invalid status transitions.'
        A transition is valid only if:
          - requested_status is the immediate next stage in STATUS_FLOW, OR
          - requested_status is one of the exception statuses (Failed
            Delivery / RTO), which can happen at any point except once
            a shipment is already Delivered.
        This is checked here, server-side, regardless of what a POST
        request claims — even if someone bypasses the UI and sends a
        raw request, an invalid jump is still rejected."""
        if current_status == "Delivered":
            return False  # final status, no further transitions allowed

        if requested_status in EXCEPTION_STATUSES:
            return True

        return requested_status == Shipment.next_status(current_status)

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
        """Shipments sitting at this warehouse, ready to go out, with no agent yet."""
        db = get_db()
        with db.cursor() as cur:
            cur.execute(
                """SELECT s.* FROM shipments s
                   WHERE s.origin_warehouse_id = %s AND s.status = 'Arrived At Warehouse'
                   AND NOT EXISTS (
                       SELECT 1 FROM shipment_assignments sa WHERE sa.shipment_id = s.id
                   )
                   ORDER BY s.created_at ASC""",
                (warehouse_id,),
            )
            return cur.fetchall()

    @staticmethod
    def create_assignment(shipment_id, agent_id):
        """Assigns a delivery agent to a shipment. This is the real write
        that was previously just a frontend demo message. Once this runs,
        the shipment shows up in that agent's 'Assigned Shipments' list via
        list_assigned_to_agent(), and disappears from the warehouse's
        'needs agent' list via unassigned_at_warehouse() (which checks for
        an existing row here). Shipment.status is left unchanged — the
        agent still needs to physically pick it up and update status
        themselves through the existing Update Status flow."""
        db = get_db()
        with db.cursor() as cur:
            cur.execute(
                "INSERT INTO shipment_assignments (shipment_id, agent_id) VALUES (%s, %s)",
                (shipment_id, agent_id),
            )

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
        """Real counts grouped into the 5 buckets shown on the Admin
        dashboard's Shipment Status Overview bars."""
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
            cur.execute("SELECT COUNT(*) AS c FROM shipments WHERE status = 'Created'")
            pending = cur.fetchone()["c"]
            cur.execute("SELECT COUNT(*) AS c FROM shipments WHERE status IN ('Failed Delivery','RTO')")
            failed = cur.fetchone()["c"]
            return dict(delivered=delivered, in_transit=in_transit,
                        out_for_delivery=out_for_delivery, pending=pending, failed=failed)

    @staticmethod
    def deliveries_this_week():
        """Real count of shipments marked Delivered on each of the last 7
        days — used by the Admin dashboard's weekly bar chart."""
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
        """Same as list_all() but joins the sender's name — used by the
        Admin dashboard's Recent Shipments table (shows Customer + Route)."""
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
        """Real count for the Admin dashboard's System Alerts — shipments
        at any warehouse still waiting for an agent, not scoped to one
        warehouse like unassigned_at_warehouse()."""
        db = get_db()
        with db.cursor() as cur:
            cur.execute(
                """SELECT COUNT(*) AS c FROM shipments s
                   WHERE s.status = 'Arrived At Warehouse'
                   AND NOT EXISTS (SELECT 1 FROM shipment_assignments sa WHERE sa.shipment_id = s.id)"""
            )
            return cur.fetchone()["c"]

    @staticmethod
    def get_current_for_sender(sender_id):
        """The customer's 'Current Shipment' — their most recent shipment
        that isn't yet Delivered. Falls back to their most recent shipment
        overall if everything they have is already delivered."""
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
        """Most recent location string logged in the status history —
        used as the 'Current Location' field."""
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
        """The agent's 'current job' — most recently assigned shipment that
        isn't yet Delivered/Failed/RTO. Falls back to their most recent
        assignment overall if everything is already finished."""
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
        """Logs a location update without changing the shipment's status —
        for the agent dashboard's standalone 'Update Location' feature."""
        db = get_db()
        with db.cursor() as cur:
            cur.execute("SELECT status FROM shipments WHERE id = %s", (shipment_id,))
            current_status = cur.fetchone()["status"]
            cur.execute(
                """INSERT INTO shipment_status_history (shipment_id, status, location, updated_by)
                   VALUES (%s, %s, %s, %s)""",
                (shipment_id, current_status, location, updated_by),
            )


class DeliveryAgent:
    """Handles delivery_agents table — one row per agent, linked to a user account."""

    @staticmethod
    def find_by_user_id(user_id):
        db = get_db()
        with db.cursor() as cur:
            cur.execute("SELECT * FROM delivery_agents WHERE user_id = %s", (user_id,))
            return cur.fetchone()

    @staticmethod
    def get_user_id(agent_id):
        """Reverse lookup: given a delivery_agents.id, return the linked
        users.id — used to send that agent a real notification."""
        db = get_db()
        with db.cursor() as cur:
            cur.execute("SELECT user_id FROM delivery_agents WHERE id = %s", (agent_id,))
            row = cur.fetchone()
            return row["user_id"] if row else None

    @staticmethod
    def get_or_create(user_id, warehouse_id=None):
        """Agents don't self-register into delivery_agents on account creation,
        so this makes sure a row exists the first time an agent visits their dashboard."""
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
    @staticmethod
    def find_first_available():
        """Picks an available agent for automatic assignment at shipment
        creation. Among available agents, prefers whoever currently has
        the fewest active (not yet delivered/failed) assignments, so load
        spreads out reasonably instead of always picking the same agent."""
        db = get_db()
        with db.cursor() as cur:
            cur.execute(
                """SELECT da.id,
                          COUNT(sa.id) FILTER (WHERE sa.status IN ('assigned','picked_up')) AS active_count
                   FROM delivery_agents da
                   LEFT JOIN shipment_assignments sa ON sa.agent_id = da.id
                   WHERE da.is_available = TRUE
                   GROUP BY da.id
                   ORDER BY active_count ASC, da.id ASC
                   LIMIT 1"""
            )
            row = cur.fetchone()
            return row["id"] if row else None

    @staticmethod
    def list_all_with_names():
        """Joins delivery_agents with users to get each agent's real name —
        used to populate the Assign Delivery Agent dropdown with real agents
        instead of hardcoded fake names."""
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
    def count_busy():
        """Agents currently marked available=True but who have at least
        one assignment that isn't yet delivered/failed — i.e. actively
        out on a delivery right now."""
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
    """Handles warehouses table."""

    @staticmethod
    def find_by_id(warehouse_id):
        db = get_db()
        with db.cursor() as cur:
            cur.execute("SELECT * FROM warehouses WHERE id = %s", (warehouse_id,))
            return cur.fetchone()

    @staticmethod
    def get_first():
        """Temporary helper: since users table has no warehouse_id link yet for
        warehouse_staff, this returns the first warehouse as a stand-in until
        that relationship is added."""
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
        """Used by the Admin dashboard's Warehouse Overview table — real
        shipment count per warehouse instead of hardcoded numbers."""
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
    """Handles the notifications table — was defined in the schema but
    never used until now. Populated automatically whenever a shipment's
    status changes (see Shipment.update_status side effect in the route)."""

    @staticmethod
    def create(user_id, shipment_id, message, channel="app"):
        db = get_db()
        with db.cursor() as cur:
            cur.execute(
                """INSERT INTO notifications (user_id, shipment_id, message, channel)
                   VALUES (%s, %s, %s, %s)""",
                (user_id, shipment_id, message, channel),
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
        """System-wide notification feed for the Admin dashboard's
        Notifications Log — shows who each notification was sent to."""
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


class DeliveryProof:
    """Handles the delivery_proof table — was defined in the schema but
    never used until now."""

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
        """Most recent delivery proof across all of a customer's shipments —
        used by the Customer Dashboard's Delivery Proof section."""
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
    """Small helper queries for the Warehouse dashboard's 'Warehouse
    Activity' cards and 'Recent Warehouse Activity' feed — all derived
    from shipment_status_history, so no new table was needed."""

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