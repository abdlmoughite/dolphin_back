from abc import ABC, abstractmethod
from decimal import Decimal, ROUND_HALF_UP


class SupplierProvider(ABC):
    supplier = None

    @abstractmethod
    def fetch_categories(self):
        raise NotImplementedError

    @abstractmethod
    def fetch_products(self):
        raise NotImplementedError

    @abstractmethod
    def fetch_product_details(self, external_product_id):
        raise NotImplementedError

    @abstractmethod
    def normalize_product(self, payload):
        raise NotImplementedError

    @abstractmethod
    def sync_product(self, normalized):
        raise NotImplementedError

    @abstractmethod
    def sync_stock_and_price(self, external_product):
        raise NotImplementedError


def calculate_selling_price(source_price, percentage_margin=0, fixed_cost=0, rounding_rule="NONE", minimum_profit=0, manual_override=None):
    if manual_override is not None:
        return Decimal(manual_override)
    source = Decimal(source_price or "0.00")
    margin = source * Decimal(percentage_margin or "0.00") / Decimal("100.00")
    price = source + margin + Decimal(fixed_cost or "0.00")
    minimum = source + Decimal(minimum_profit or "0.00")
    if price < minimum:
        price = minimum
    if rounding_rule == "NEAREST_1":
        return price.quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    if rounding_rule == "NEAREST_5":
        return (price / Decimal("5")).quantize(Decimal("1"), rounding=ROUND_HALF_UP) * Decimal("5")
    if rounding_rule == "NEAREST_10":
        return (price / Decimal("10")).quantize(Decimal("1"), rounding=ROUND_HALF_UP) * Decimal("10")
    return price.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

