CREATE TABLE users (
    id SERIAL PRIMARY KEY,
    name VARCHAR(100) NOT NULL,
    email VARCHAR(120) NOT NULL UNIQUE,
    phone VARCHAR(15),
    password_hash VARCHAR(255) NOT NULL,
    role VARCHAR(20) NOT NULL DEFAULT 'customer'
    CHECK (role IN ('customer', 'delivery_agent', 'warehouse_staff', 'admin')),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE warehouses (
    id SERIAL PRIMARY KEY,
    name VARCHAR(100) NOT NULL,
    address VARCHAR(255),
    city VARCHAR(50),
    state VARCHAR(50),
    pincode VARCHAR(10),
    contact_number VARCHAR(15),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE delivery_agents (
    id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    warehouse_id INTEGER REFERENCES warehouses(id) ON DELETE SET NULL,
    vehicle_type VARCHAR(50),
    is_available BOOLEAN DEFAULT TRUE,
    current_location VARCHAR(255)
);

CREATE TABLE shipments (
    id SERIAL PRIMARY KEY,
    tracking_id VARCHAR(20) NOT NULL UNIQUE,
    sender_id INTEGER NOT NULL REFERENCES users(id),
    sender_name VARCHAR(100),
    sender_address VARCHAR(255),
    sender_phone VARCHAR(15),
    receiver_name VARCHAR(100) NOT NULL,
    receiver_address VARCHAR(255) NOT NULL,
    receiver_phone VARCHAR(15) NOT NULL,
    package_type VARCHAR(50),
    package_description varchar(255),
    weight DECIMAL(6,2),
    origin_warehouse_id INTEGER REFERENCES warehouses(id),
    destination_warehouse_id INTEGER REFERENCES warehouses(id),
    status VARCHAR(20) NOT NULL DEFAULT 'Created'
        CHECK (status IN ('Created','Picked Up','At Warehouse','In Transit',
                           'Out for Delivery','Delivered','Failed Delivery','RTO')),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE shipment_status_history (
    id SERIAL PRIMARY KEY,
    shipment_id INTEGER NOT NULL REFERENCES shipments(id) ON DELETE CASCADE,
    status VARCHAR(50) NOT NULL,
    location VARCHAR(255),
    remarks VARCHAR(255),
    updated_by INTEGER REFERENCES users(id),
    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE shipment_assignments (
    id SERIAL PRIMARY KEY,
    shipment_id INTEGER NOT NULL REFERENCES shipments(id) ON DELETE CASCADE,
    agent_id INTEGER NOT NULL REFERENCES delivery_agents(id),
    assigned_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    status VARCHAR(20) DEFAULT 'assigned'
        CHECK (status IN ('assigned','picked_up','delivered','failed'))
);

CREATE TABLE delivery_proof (
    id SERIAL PRIMARY KEY,
    shipment_id INTEGER NOT NULL REFERENCES shipments(id) ON DELETE CASCADE,
    agent_id INTEGER REFERENCES delivery_agents(id),
    proof_type VARCHAR(20) DEFAULT 'photo'
        CHECK (proof_type IN ('photo','signature','otp')),
    file_path VARCHAR(255),
    delivered_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE notifications (
    id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    shipment_id INTEGER REFERENCES shipments(id) ON DELETE SET NULL,
    message VARCHAR(255) NOT NULL,
    channel VARCHAR(10) DEFAULT 'app'
        CHECK (channel IN ('sms','email','app')),
    is_read BOOLEAN DEFAULT FALSE,
    sent_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);