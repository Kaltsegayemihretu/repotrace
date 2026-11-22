# Impact demonstration

Analyze these files; they do not need to run.

`checkout.checkout → orders.place_order → pricing.calculate_total`

Select `pricing.py` / `calculate_total`: expect one direct caller (`place_order`)
and one indirect caller (`checkout`). This reports potential review/test candidates,
not guaranteed breakage. The original `demo_repository` remains unchanged.
