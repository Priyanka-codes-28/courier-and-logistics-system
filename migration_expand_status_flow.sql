ALTER TABLE shipments ALTER COLUMN status TYPE VARCHAR(30);

ALTER TABLE shipments DROP CONSTRAINT IF EXISTS shipments_status_check;

ALTER TABLE shipments ADD CONSTRAINT shipments_status_check
    CHECK (status IN (
        'Created',
        'Picked Up',
        'In Transit',
        'At Warehouse',
        'Arrived at Warehouse',
        'Processing',
        'Ready for Dispatch',
        'Agent Assigned',
        'Out for Delivery',
        'Delivered',
        'Failed Delivery',
        'RTO'
    ));