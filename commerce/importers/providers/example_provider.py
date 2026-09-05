from commerce.importers.base import SupplierProvider, calculate_selling_price


class ExampleProvider(SupplierProvider):
    def fetch_categories(self):
        raise NotImplementedError("Configurez un fournisseur officiel avant toute synchronisation.")

    def fetch_products(self):
        raise NotImplementedError("Configurez un fournisseur officiel avant toute synchronisation.")

    def fetch_product_details(self, external_product_id):
        raise NotImplementedError("Configurez un fournisseur officiel avant toute synchronisation.")

    def normalize_product(self, payload):
        return payload

    def sync_product(self, normalized):
        raise NotImplementedError("Aucune publication automatique sans fournisseur autorise.")

    def sync_stock_and_price(self, external_product):
        supplier = external_product.supplier
        external_product.final_selling_price = calculate_selling_price(
            external_product.source_price,
            supplier.percentage_margin,
            supplier.fixed_cost,
            supplier.rounding_rule,
            supplier.minimum_profit,
            external_product.manual_price_override,
        )
        external_product.save(update_fields=["final_selling_price", "updated_at"])
        return external_product
