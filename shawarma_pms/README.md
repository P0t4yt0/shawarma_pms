# Fake Arab Shawarma Station - Integrated Store Management System (Django)

Same modular layout as jbson_v2: `activity_log`, `billing_payment`, `inventory`, `point_of_sale`,
`reports_analytics`, `security` (+ new `promotions`), with the project package `shawarma_pms` (jbson_pms equivalent).

## 1. Initialise (terminal)
```bash
python -m venv venv && source venv/bin/activate        # Windows: venv\Scripts\activate
pip install "Django>=5.0,<5.3"
django-admin startproject shawarma_pms .               # (already included in this zip)
python manage.py startapp security
python manage.py startapp activity_log
python manage.py startapp inventory
python manage.py startapp point_of_sale
python manage.py startapp promotions
python manage.py startapp billing_payment
python manage.py startapp reports_analytics
```
Then drop in the files from this project, and:
```bash
python manage.py makemigrations security activity_log inventory point_of_sale promotions billing_payment reports_analytics
python manage.py migrate
python manage.py setup_roles            # Cashier / Kitchen / Manager groups + permissions
python manage.py seed_inventory         # 14 ingredients, menu variants, Beef/Chicken recipes
python manage.py seed_payment_methods   # cash, GCash, Maya, card
python manage.py createsuperuser
python manage.py runserver
```
Create staff in /admin/ -> add a `User`, then an `EmployeeProfile` with a role; the matching group is assigned automatically.
Kitchen users land on `/pos/kds/`, cashiers on `/pos/`.

## 2. Recipe automation demo (python manage.py shell)
```python
from inventory.models import Recipe
r = Recipe.objects.get(variant__sku="BSW-S")          # Beef Shawarma Wrap (Standard)
r.deduct_stock(quantity=1)                            # 1 pita, 0.1 kg beef, 0.05 kg veg, 0.02 L toum, 1 foil
```

## 3. Phase 1 endpoints (JS polling every 3-5 s)
| Method | URL | Permission |
|---|---|---|
| GET  | /pos/api/orders/ready/?after=<cursor>&mine=1 | point_of_sale.view_ready_alerts |
| GET  | /pos/api/kds/tickets/ | point_of_sale.view_kds |
| POST | /pos/api/orders/ | point_of_sale.add_order |
| POST | /pos/api/orders/<id>/status/ | bump_order / complete_order / void_order (by target state) |
| POST | /billing/api/orders/<id>/pay/ | billing_payment.add_payment |

## 4. Phase 2 upgrade (Channels + Redis)
1. `pip install channels channels-redis daphne`
2. settings: add `"daphne"`, `"channels"` to INSTALLED_APPS, set `ASGI_APPLICATION`, `CHANNEL_LAYERS`, `KDS_REALTIME = True`
3. uncomment the `ProtocolTypeRouter` block in `shawarma_pms/asgi.py`
4. In the JS, replace `poll()` with a WebSocket to `ws/pos/orders/`; the message is the same `serialize_event()` payload.
No model, migration or view changes are needed.

## 5. Nightly job
`python manage.py generate_daily_report --backfill 14` (cron / Task Scheduler) refreshes summaries,
slow-period promo suggestions and ingredient forecasts.
