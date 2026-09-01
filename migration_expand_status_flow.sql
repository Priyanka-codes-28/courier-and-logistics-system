-- Phase 3: expands the shipment status workflow to the full 9-stage
-- flow, plus keeps Failed Delivery / RTO as exception statuses.
--
-- Safe on an existing database: this only widens which values are
-- ALLOWED. It does not touch any existing rows, and every status
-- value already in use (Created, Picked Up, In Transit, Out for
-- Delivery, Delivered, Failed Delivery, RTO) remains valid.
--
-- NOTE: "At Warehouse" (used by earlier phases) is intentionally
-- kept in the allowed list too, so any existing rows with that
-- status remain valid — it is not part of the new forward flow,
-- but old data referencing it won't break.

-- Widen the column first — "Arrived at Warehouse" is exactly 20
-- characters, leaving zero margin in the old VARCHAR(20) column.
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