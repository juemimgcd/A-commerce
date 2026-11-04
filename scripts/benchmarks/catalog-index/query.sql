SELECT products.category_id, products.title, products.brand, products.description,
       products.active, products.version, products.id, products.created_at, products.shop_id
FROM products JOIN shops ON shops.id = products.shop_id
WHERE products.active IS TRUE AND shops.status = 'active'
  AND products.category_id = md5('category-42')::uuid AND products.id > '55555555-5555-5555-5555-555555555555'::uuid
  AND EXISTS (SELECT skus.id FROM skus WHERE skus.product_id = products.id
              AND skus.active IS TRUE AND skus.price_fen BETWEEN 0 AND 100000000
              AND skus.attributes @> '{}'::jsonb)
ORDER BY products.id LIMIT 21;
