from django.contrib import admin
from .models import Payment, PaymentMethod, Receipt

admin.site.register(PaymentMethod)
admin.site.register(Payment)
admin.site.register(Receipt)
