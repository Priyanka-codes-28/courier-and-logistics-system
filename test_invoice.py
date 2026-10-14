import io
import os
import sqlite3
import sys
import types
import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

sqlite3.register_adapter(datetime, lambda d: d.isoformat(" "))
sqlite3.register_converter("TIMESTAMP", lambda b: datetime.fromisoformat(b.decode()))


class _Cur:
    def __init__(self, conn):
        self.c = conn.cursor()

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, sql, params=()):
        self.c.execute(sql.replace("%s", "?").replace("NOW()", "CURRENT_TIMESTAMP"), params)
        return self

    def fetchone(self):
        r = self.c.fetchone()
        return dict(r) if r else None

    def fetchall(self):
        return [dict(r) for r in self.c.fetchall()]


class _DB:
    def __init__(self):
        self.conn = sqlite3.connect(":memory:", detect_types=sqlite3.PARSE_DECLTYPES, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row

    def cursor(self):
        return _Cur(self.conn)

    def raw(self, sql, params=()):
        rows = self.conn.execute(sql, params).fetchall()
        self.conn.commit()
        return rows


_cur = {"db": None}
db_mod = types.ModuleType("db")
db_mod.get_db = lambda: _cur["db"]
sys.modules["db"] = db_mod

IST = timezone(timedelta(hours=5, minutes=30))
tz_mod = types.ModuleType("timezone_utils")
tz_mod.IST = IST
tz_mod.format_ist = lambda v, *a, **k: v.strftime("%d %b %Y, %I:%M %p") if hasattr(v, "strftime") else str(v)
sys.modules["timezone_utils"] = tz_mod

from flask import Blueprint, Flask, get_flashed_messages, render_template_string  # noqa: E402
from pypdf import PdfReader 

import invoice_pdf  
from models import Payment  
from routes import shipments as shipments_module  

SCHEMA = """
CREATE TABLE users (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT, email TEXT, phone TEXT, role TEXT);
CREATE TABLE shipments (
  id INTEGER PRIMARY KEY AUTOINCREMENT, tracking_id TEXT UNIQUE, sender_id INTEGER,
  sender_name TEXT, sender_address TEXT, sender_phone TEXT, receiver_name TEXT, receiver_address TEXT,
  receiver_phone TEXT, package_type TEXT, package_description TEXT, weight REAL,
  status TEXT, created_at TIMESTAMP);
CREATE TABLE payments (
  id INTEGER PRIMARY KEY AUTOINCREMENT, shipment_id INTEGER, amount REAL, status TEXT DEFAULT 'pending',
  payment_method TEXT, transaction_ref TEXT, paid_at TIMESTAMP, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP);
"""


def pdf_text(data):
    reader = PdfReader(io.BytesIO(data))
    return " ".join((p.extract_text() or "") for p in reader.pages), len(reader.pages)


class InvoiceTests(unittest.TestCase):
    def setUp(self):
        self.db = _DB()
        self.db.conn.executescript(SCHEMA)
        _cur["db"] = self.db

        self.app = Flask(__name__)
        self.app.config.update(SECRET_KEY="t", TESTING=True)
        auth = Blueprint("auth", __name__)

        @auth.route("/login")
        def login():
            return render_template_string(
                "{% for m in get_flashed_messages() %}<p class='flash'>{{ m }}</p>{% endfor %}LOGIN")

        @auth.route("/dashboard")
        def dashboard():
            return "DASH"

        @auth.route("/admin/shipments")
        def manage_shipments():
            return "ADMIN SHIPMENTS"

        self.app.register_blueprint(auth)
        self.app.register_blueprint(shipments_module.shipments_bp)

        @self.app.route("/_flash")
        def _flash():
            return render_template_string(
                "{% for m in get_flashed_messages() %}<p class='flash'>{{ m }}</p>{% endfor %}")

        self.ctx = self.app.app_context()
        self.ctx.push()
        self.addCleanup(self.ctx.pop)

        self.alice = self.user("Alice Sharma", "alice@example.com", "9000000001", "customer")
        self.bob = self.user("Bob Rao", "bob@example.com", "9000000002", "customer")
        self.agent = self.user("Dev Agent", "agent@example.com", "9000000003", "delivery_agent")
        self.wh = self.user("Wanda Staff", "wh@example.com", "9000000004", "warehouse_staff")
        self.admin = self.user("Ada Admin", "admin@example.com", "9000000005", "admin")

    def user(self, name, email, phone, role):
        self.db.raw("INSERT INTO users (name,email,phone,role) VALUES (?,?,?,?)", (name, email, phone, role))
        return self.db.raw("SELECT id FROM users WHERE email=?", (email,))[0]["id"]

    def shipment(self, sender, tid="CL20261014AB12", weight=2.0, amount=90.0, pay_status="pending",
                 payment=True, **over):
        row = dict(sender_name="Alice Sharma", sender_address="12 MG Road, Bengaluru",
                   sender_phone="9000000001", receiver_name="Ravi Kumar",
                   receiver_address="45 Banjara Hills, Hyderabad", receiver_phone="9111111111",
                   package_type="Documents", package_description=None, status="Created")
        row.update(over)
        self.db.raw(
            """INSERT INTO shipments (tracking_id,sender_id,sender_name,sender_address,sender_phone,
               receiver_name,receiver_address,receiver_phone,package_type,package_description,weight,
               status,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (tid, sender, row["sender_name"], row["sender_address"], row["sender_phone"],
             row["receiver_name"], row["receiver_address"], row["receiver_phone"], row["package_type"],
             row["package_description"], weight, row["status"], datetime(2026, 10, 9, 18, 10)))
        sid = self.db.raw("SELECT id FROM shipments WHERE tracking_id=?", (tid,))[0]["id"]
        if payment:
            self.db.raw("INSERT INTO payments (shipment_id, amount, status) VALUES (?,?,?)",
                        (sid, amount, pay_status))
        return sid

    def client_as(self, user_id, role):
        c = self.app.test_client()
        with c.session_transaction() as s:
            s["user_id"], s["user_role"] = user_id, role
        return c

    def get_pdf(self, client, tid="CL20261014AB12"):
        r = client.get(f"/shipments/{tid}/invoice")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.mimetype, "application/pdf")
        self.assertTrue(r.data.startswith(b"%PDF"))
        return r, *pdf_text(r.data)

    def flashed(self, client):
        return client.get("/_flash").get_data(as_text=True)

    def test_01_customer_downloads_own_invoice_with_correct_content(self):
        self.shipment(self.alice)
        r, text, pages = self.get_pdf(self.client_as(self.alice, "customer"))
        self.assertEqual(pages, 1)
        self.assertIn('attachment; filename="CourierOS_Invoice_CL20261014AB12.pdf"', r.headers["Content-Disposition"])
        self.assertIn("no-store", r.headers["Cache-Control"])
        for expected in ("CourierOS", "INVOICE", "INV-000001", "CL20261014AB12",
                         "Alice Sharma", "9000000001", "alice@example.com", "12 MG Road, Bengaluru",
                         "Ravi Kumar", "9111111111", "45 Banjara Hills, Hyderabad",
                         "09 Oct 2026", "Documents", "2 kg",
                         "Base delivery charge", "INR 50.00", "INR 20.00 / kg", "INR 40.00",
                         "Total shipment charges", "INR 90.00", "PENDING"):
            self.assertIn(expected, text, expected)

    def test_02_charges_match_payment_record_not_a_new_formula(self):
        # stored amount equals the existing fee rule -> breakdown shown
        self.shipment(self.alice, tid="CLAAA1", weight=3.0, amount=Payment.calculate_fee(3.0))
        _, text, _ = self.get_pdf(self.client_as(self.alice, "customer"), "CLAAA1")
        self.assertIn("INR 110.00", text)
        self.assertIn("Weight charge", text)
        # stored amount differs from the formula (e.g. rates changed later) -> only the stored total
        self.shipment(self.alice, tid="CLBBB2", weight=3.0, amount=123.45)
        _, text, _ = self.get_pdf(self.client_as(self.alice, "customer"), "CLBBB2")
        self.assertIn("INR 123.45", text)
        self.assertIn("Delivery charges (as billed)", text)
        self.assertNotIn("Base delivery charge", text)
        self.assertNotIn("INR 110.00", text)

    def test_03_paid_status_method_reference_and_download_does_not_change_payment(self):
        sid = self.shipment(self.alice, pay_status="paid")
        self.db.raw("UPDATE payments SET payment_method='Card ending 4242', transaction_ref='TXN123ABC', "
                    "paid_at=? WHERE shipment_id=?", (datetime(2026, 10, 10, 9, 30), sid))
        _, text, _ = self.get_pdf(self.client_as(self.alice, "customer"))
        for expected in ("PAID", "Card ending 4242", "TXN123ABC", "PAID ON", "10 Oct 2026"):
            self.assertIn(expected, text, expected)
        # pending shipment stays pending after being downloaded
        self.shipment(self.alice, tid="CLPEND1")
        self.get_pdf(self.client_as(self.alice, "customer"), "CLPEND1")
        row = self.db.raw("SELECT p.status, p.paid_at FROM payments p JOIN shipments s ON s.id=p.shipment_id "
                          "WHERE s.tracking_id='CLPEND1'")[0]
        self.assertEqual((row["status"], row["paid_at"]), ("pending", None))

    def test_04_optional_payment_details_missing(self):
        self.shipment(self.alice)  # pending: no method / reference / paid date
        _, text, _ = self.get_pdf(self.client_as(self.alice, "customer"))
        self.assertGreaterEqual(text.count("Not available"), 2)
        self.assertNotIn("None", text)

    def test_05_no_payment_record_still_generates(self):
        self.shipment(self.alice, payment=False)
        _, text, _ = self.get_pdf(self.client_as(self.alice, "customer"))
        self.assertIn("No payment record", text)
        self.assertIn("Not available", text)
        self.assertNotIn("INR 90.00", text)

    def test_06_missing_weight_uses_existing_default_rule(self):
        self.shipment(self.alice, weight=None, amount=Payment.calculate_fee(None))
        _, text, _ = self.get_pdf(self.client_as(self.alice, "customer"))
        self.assertIn("Not specified", text)
        self.assertIn("default 0.5 kg", text)
        self.assertIn("INR 60.00", text)

    def test_07_customer_cannot_download_someone_elses_invoice(self):
        self.shipment(self.bob, tid="CLBOB001")
        c = self.client_as(self.alice, "customer")
        r = c.get("/shipments/CLBOB001/invoice")
        self.assertEqual(r.status_code, 302)
        self.assertTrue(r.headers["Location"].endswith("/shipments/my"))
        self.assertNotEqual(r.mimetype, "application/pdf")
        owned_msg = self.flashed(c)
        # identical to a shipment that doesn't exist -> no way to probe other customers' IDs
        r = c.get("/shipments/CLNOPE99/invoice")
        self.assertTrue(r.headers["Location"].endswith("/shipments/my"))
        self.assertEqual(self.flashed(c), owned_msg)
        self.assertIn("Shipment not found", owned_msg)

    def test_08_roles(self):
        self.shipment(self.alice)
        # signed out
        r = self.app.test_client().get("/shipments/CL20261014AB12/invoice")
        self.assertTrue(r.headers["Location"].endswith("/login"))
        # delivery agent + warehouse staff are refused
        for uid, role in ((self.agent, "delivery_agent"), (self.wh, "warehouse_staff")):
            r = self.client_as(uid, role).get("/shipments/CL20261014AB12/invoice")
            self.assertEqual(r.status_code, 302, role)
            self.assertTrue(r.headers["Location"].endswith("/dashboard"), role)
            self.assertFalse(r.data.startswith(b"%PDF"))
        # admin can download any shipment
        _, text, _ = self.get_pdf(self.client_as(self.admin, "admin"))
        self.assertIn("Alice Sharma", text)
        # admin gets sent to the admin shipments page for a bad id
        r = self.client_as(self.admin, "admin").get("/shipments/CLNOPE99/invoice")
        self.assertTrue(r.headers["Location"].endswith("/admin/shipments"))

    def test_09_invalid_ids_are_rejected(self):
        c = self.client_as(self.alice, "customer")
        for bad in ("abc%24def", "x%27%3B%20DROP%20TABLE%20users--", "A" * 80, "a%20b", "%3Cscript%3E"):
            r = c.get(f"/shipments/{bad}/invoice")
            self.assertEqual(r.status_code, 302, bad)
            self.assertTrue(r.headers["Location"].endswith("/shipments/my"), bad)
            self.assertIn("Invalid tracking ID", self.flashed(c), bad)
        self.assertEqual(self.db.raw("SELECT COUNT(*) c FROM users")[0]["c"], 5)
        # a path-traversal style id never reaches the route (404), and never returns a PDF
        r = c.get("/shipments/..%2F..%2Fetc/invoice")
        self.assertIn(r.status_code, (302, 404))
        self.assertFalse(r.data.startswith(b"%PDF"))

    def test_10_tracking_id_lookup_is_case_insensitive_like_the_rest_of_the_app(self):
        self.shipment(self.alice)
        r, text, _ = self.get_pdf(self.client_as(self.alice, "customer"), "cl20261014ab12")
        self.assertIn("CourierOS_Invoice_CL20261014AB12.pdf", r.headers["Content-Disposition"])

    def test_11_markup_in_user_data_cannot_break_the_pdf(self):
        self.shipment(self.alice, receiver_name="<b>Eve</b> & <i>Co</i>",
                      receiver_address="Flat <7> & Sons </para> Road",
                      package_description="Fragile <font color=red>glass</font> " + "very long text " * 80)
        _, text, _ = self.get_pdf(self.client_as(self.alice, "customer"))
        self.assertIn("<b>Eve</b>", text)
        self.assertIn("&", text)
        self.assertIn("<7>", text)

    def test_12_generation_failure_is_handled_without_leaking_details(self):
        self.shipment(self.alice)
        c = self.client_as(self.alice, "customer")
        with mock.patch.object(invoice_pdf, "build_invoice_pdf", side_effect=RuntimeError("secret db password")):
            r = c.get("/shipments/CL20261014AB12/invoice")
        self.assertEqual(r.status_code, 302)
        self.assertNotIn(b"secret", r.data)
        msg = self.flashed(c)
        self.assertIn("generate the invoice", msg)
        self.assertNotIn("secret", msg)

    def test_13_dashboard_template_has_the_buttons(self):
        import jinja2
        with open(os.path.join(ROOT, "templates", "dashboard_customer.html"), encoding="utf-8") as fh:
            src = fh.read()
        jinja2.Environment().parse(src)  # still valid Jinja
        self.assertIn("url_for('shipments.download_invoice', tracking_id=current_shipment.tracking_id)", src)
        self.assertIn("url_for('shipments.download_invoice', tracking_id=s.tracking_id)", src)
        self.assertIn("Download Invoice", src)


if __name__ == "__main__":
    unittest.main(verbosity=2)