"""Small shop example for learning how the analyzer records evidence."""
from decimal import Decimal as Money


def calculate_total(price: float, quantity: int = 1, *, tax: float = 0.15) -> float:
    subtotal = price * quantity
    return subtotal + subtotal * tax


def place_order(price: float, quantity: int = 1) -> float:
    total = calculate_total(price, quantity)
    print("Order total:", total)
    return total


class Checkout:
    def submit(self, price: float) -> float:
        return place_order(price)
