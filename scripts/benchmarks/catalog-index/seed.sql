-- Synthetic data only. Refuse any database other than this disposable benchmark database.
\set ON_ERROR_STOP on
BEGIN;
DO $$ BEGIN
    IF current_database() <> 'commerce_load_index' THEN
        RAISE EXCEPTION 'Only commerce_load_index is allowed';
    END IF;
    IF EXISTS (SELECT 1 FROM products) THEN
        RAISE EXCEPTION 'Benchmark requires an empty catalog';
    END IF;
END $$;
UPDATE shops SET status = 'active' WHERE id = 'cb59df63-1c60-4bf8-90ac-ae5809c57c01';
INSERT INTO categories (id, name, created_at)
SELECT md5('category-' || i)::uuid, 'Benchmark category ' || i, now()
FROM generate_series(0, 99) AS i;
INSERT INTO products (id, category_id, shop_id, title, brand, description, active, version, created_at)
SELECT md5('product-' || i)::uuid, md5('category-' || (i % 100))::uuid,
       'cb59df63-1c60-4bf8-90ac-ae5809c57c01', 'Benchmark product ' || i,
       'Benchmark', repeat('synthetic ', 20), (i / 100) % 5 <> 0, 1, now()
FROM generate_series(0, 99999) AS i;
INSERT INTO skus (id, product_id, code, attributes, price_fen, version, active, created_at, cost_version)
SELECT md5('sku-' || i)::uuid, md5('product-' || i)::uuid,
       'BENCH-' || i, '{}'::jsonb, 2500, 1, true, now(), 1
FROM generate_series(0, 99999) AS i;
COMMIT;
ANALYZE products;
ANALYZE skus;
ANALYZE shops;
