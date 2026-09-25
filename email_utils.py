import logging
import requests
from flask import current_app, render_template

BREVO_ENDPOINT = "https://api.brevo.com/v3/smtp/email"

def send_email(to_email, to_name, subject, html_content, text_content=None):
    if not to_email:
        _log_error(f"send_email('{subject}') called with no recipient address — skipped")
        return False

    api_key = current_app.config.get("BREVO_API_KEY")
    sender_email = current_app.config.get("BREVO_SENDER_EMAIL")
    if not api_key or not sender_email:
        _log_error(
            f"send_email('{subject}') skipped — BREVO_API_KEY / BREVO_SENDER_EMAIL "
            f"is not configured"
        )
        return False

    payload = {
        "sender": {
            "name": current_app.config.get("BREVO_SENDER_NAME", "CourierOS"),
            "email": sender_email,
        },
        "to": [{"email": to_email, "name": to_name or to_email}],
        "subject": subject,
        "htmlContent": html_content,
    }
    if text_content:
        payload["textContent"] = text_content

    headers = {
        "api-key": api_key,
        "Content-Type": "application/json",
        "Accept": "application/json",
    }

    try:
        response = requests.post(BREVO_ENDPOINT, json=payload, headers=headers, timeout=10)
        if response.status_code in (200, 201):
            return True
        _log_error(
            f"Brevo API error sending '{subject}' to {to_email}: "
            f"{response.status_code} {response.text}"
        )
        return False
    except requests.RequestException as e:
        _log_error(f"Failed to reach Brevo API sending '{subject}' to {to_email}: {e}")
        return False
    except Exception as e:  
        _log_error(f"Unexpected error sending '{subject}' to {to_email}: {e}")
        return False


def _log_error(message):
    try:
        current_app.logger.error(f"[email_utils] {message}")
    except Exception:
        logging.getLogger(__name__).error(message)


def _send_rendered(template_name, to_email, to_name, subject, text_content=None, **context):
    
    try:
        html = render_template(f"emails/{template_name}", to_name=to_name, **context)
    except Exception as e:
        
        _log_error(
            f"Failed to render '{template_name}' for '{subject}' to {to_email}: "
            f"{type(e).__name__}: {e}"
        )
        return False
    return send_email(to_email, to_name, subject, html, text_content)

# 1. Registration confirmation

def send_welcome_email(to_email, name):
    subject = "Welcome to CourierOS"
    text = f"Welcome to CourierOS, {name or ''}! Your account has been created successfully."
    return _send_rendered("welcome.html", to_email, name, subject, text_content=text, name=name)

# 2. Shipment creation confirmation

def send_shipment_created_email(to_email, to_name, tracking_id, sender_name, sender_address,
                                 sender_phone, receiver_name, receiver_address, receiver_phone,
                                 package_type, status):
    subject = f"Shipment Created — {tracking_id}"
    text = f"Your shipment {tracking_id} has been created. Status: {status}."
    return _send_rendered(
        "shipment_created.html", to_email, to_name, subject, text_content=text,
        tracking_id=tracking_id, sender_name=sender_name, sender_address=sender_address,
        sender_phone=sender_phone, receiver_name=receiver_name, receiver_address=receiver_address,
        receiver_phone=receiver_phone, package_type=package_type, status=status,
    )

# 3. Pickup agent assignment

def send_pickup_assigned_email(to_email, to_name, tracking_id, agent_name, pickup_address=None):
    subject = f"Pickup Agent Assigned — {tracking_id}"
    text = f"{agent_name} has been assigned to pick up your shipment {tracking_id}."
    return _send_rendered(
        "pickup_assigned.html", to_email, to_name, subject, text_content=text,
        tracking_id=tracking_id, agent_name=agent_name, pickup_address=pickup_address,
    )

# 4. shipment status update
STATUS_MESSAGES = {
    "Picked Up": "Your shipment has been picked up and is on its way to our warehouse.",
    "In Transit": "Your shipment is in transit.",
    "Arrived at Warehouse": "Your shipment has arrived at our warehouse.",
    "Processing": "Your shipment is being processed at our warehouse.",
    "Ready for Dispatch": "Your shipment is ready for dispatch.",
    "Agent Assigned": "A delivery agent has been assigned to your shipment.",
}


def send_status_update_email(to_email, to_name, tracking_id, status):
    subject = f"Shipment Update — {tracking_id} is now {status}"
    message = STATUS_MESSAGES.get(status, f"Your shipment status has been updated to {status}.")
    text = f"Shipment {tracking_id} status update: {status}."
    return _send_rendered(
        "status_update.html", to_email, to_name, subject, text_content=text,
        tracking_id=tracking_id, status=status, message=message,
    )

# 5. Out for delivery

def send_out_for_delivery_email(to_email, to_name, tracking_id, agent_name, receiver_address=None):
    subject = f"Out for Delivery — {tracking_id}"
    text = f"Your shipment {tracking_id} is out for delivery with {agent_name}."
    return _send_rendered(
        "out_for_delivery.html", to_email, to_name, subject, text_content=text,
        tracking_id=tracking_id, agent_name=agent_name, receiver_address=receiver_address,
    )

# 6. Delivery completion confirmation

def send_delivery_completed_email(to_email, to_name, tracking_id, delivered_at=None, proof=None):
    subject = f"Delivered — {tracking_id}"
    delivered_at_str = delivered_at.strftime("%b %d, %Y at %I:%M %p") if delivered_at else "just now"

    proof_line = None
    if proof:
        proof_type = (proof.get("proof_type") or "confirmation").title()
        proof_line = f"{proof_type} on file"

    text = f"Shipment {tracking_id} was delivered on {delivered_at_str}."
    return _send_rendered(
        "delivered.html", to_email, to_name, subject, text_content=text,
        tracking_id=tracking_id, delivered_at_str=delivered_at_str, proof_line=proof_line,
    )

# 7. Failed delivery / RTO
def send_failed_delivery_email(to_email, to_name, tracking_id, status):
    is_rto = status == "RTO"
    subject = f"{'Return to Origin' if is_rto else 'Delivery Attempt Failed'} — {tracking_id}"
    message = (
        "Your shipment is being returned to the origin."
        if is_rto else
        "We were unable to deliver your shipment on this attempt."
    )
    text = f"Shipment {tracking_id}: {status}. {message}"
    return _send_rendered(
        "failed_delivery.html", to_email, to_name, subject, text_content=text,
        tracking_id=tracking_id, status=status, is_rto=is_rto, message=message,
    )
    

# Delivery-agent notifications
def send_agent_pickup_assigned_email(to_email, to_name, tracking_id, sender_name, sender_address,
                                      receiver_name, receiver_address, package_type, weight, status):
    subject = f"New Pickup Assignment — {tracking_id}"
    text = f"You've been assigned to pick up shipment {tracking_id}. Please check your CourierOS dashboard."
    return _send_rendered(
        "agent_pickup_assigned.html", to_email, to_name, subject, text_content=text,
        tracking_id=tracking_id, sender_name=sender_name, sender_address=sender_address,
        receiver_name=receiver_name, receiver_address=receiver_address,
        package_type=package_type, weight=weight, status=status,
    )


def send_agent_delivery_assigned_email(to_email, to_name, tracking_id, sender_name, receiver_name,
                                        receiver_address, package_type, weight, status):
    subject = f"New Delivery Assignment — {tracking_id}"
    text = f"You've been assigned to deliver shipment {tracking_id}. Please check your CourierOS dashboard."
    return _send_rendered(
        "agent_delivery_assigned.html", to_email, to_name, subject, text_content=text,
        tracking_id=tracking_id, sender_name=sender_name, receiver_name=receiver_name,
        receiver_address=receiver_address, package_type=package_type, weight=weight, status=status,
    )


def send_agent_failed_delivery_email(to_email, to_name, tracking_id, receiver_name, receiver_address,
                                      status, remarks=None):
    subject = f"Delivery Update Required — {tracking_id} ({status})"
    text = f"Shipment {tracking_id} is now {status}. Please check your CourierOS dashboard."
    return _send_rendered(
        "agent_failed_delivery.html", to_email, to_name, subject, text_content=text,
        tracking_id=tracking_id, receiver_name=receiver_name, receiver_address=receiver_address,
        status=status, remarks=remarks,
    )

# Warehouse-staff notifications
def send_warehouse_shipment_arrived_email(to_email, to_name, tracking_id, sender_name, receiver_name,
                                           package_type, weight, status):
    subject = f"Shipment Arrived — {tracking_id}"
    text = f"Shipment {tracking_id} has arrived at the warehouse and is ready to process."
    return _send_rendered(
        "warehouse_shipment_arrived.html", to_email, to_name, subject, text_content=text,
        tracking_id=tracking_id, sender_name=sender_name, receiver_name=receiver_name,
        package_type=package_type, weight=weight, status=status,
    )


def send_warehouse_processing_email(to_email, to_name, tracking_id, sender_name, receiver_name,
                                     package_type, weight, status):
    subject = f"Shipment Processing — {tracking_id}"
    text = f"Shipment {tracking_id} is being processed at the warehouse."
    return _send_rendered(
        "warehouse_processing.html", to_email, to_name, subject, text_content=text,
        tracking_id=tracking_id, sender_name=sender_name, receiver_name=receiver_name,
        package_type=package_type, weight=weight, status=status,
    )


def send_warehouse_ready_for_dispatch_email(to_email, to_name, tracking_id, receiver_name,
                                             receiver_address, package_type, weight, status):
    subject = f"Ready for Dispatch — {tracking_id}"
    text = f"Shipment {tracking_id} is ready for dispatch."
    return _send_rendered(
        "warehouse_ready_for_dispatch.html", to_email, to_name, subject, text_content=text,
        tracking_id=tracking_id, receiver_name=receiver_name, receiver_address=receiver_address,
        package_type=package_type, weight=weight, status=status,
    )


def send_warehouse_delivery_assigned_email(to_email, to_name, tracking_id, agent_name, receiver_name,
                                            receiver_address, status):
    subject = f"Delivery Agent Assigned — {tracking_id}"
    text = f"{agent_name} has been assigned to deliver shipment {tracking_id}."
    return _send_rendered(
        "warehouse_delivery_assigned.html", to_email, to_name, subject, text_content=text,
        tracking_id=tracking_id, agent_name=agent_name, receiver_name=receiver_name,
        receiver_address=receiver_address, status=status,
    )

# Admin alerts 

def send_admin_alert_email(to_email, to_name, subject_line, tracking_id=None, sender_name=None,
                            receiver_name=None, status=None, remarks=None, extra_info=None):
    text = f"CourierOS admin alert: {subject_line}"
    return _send_rendered(
        "admin_alert.html", to_email, to_name, subject_line, text_content=text,
        tracking_id=tracking_id, sender_name=sender_name, receiver_name=receiver_name,
        status=status, remarks=remarks, extra_info=extra_info,
    )