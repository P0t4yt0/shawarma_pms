from django.contrib import admin
from . import models

# Auto-register all models for this app
for model_name in dir(models):
    model = getattr(models, model_name)
    try:
        if hasattr(model, '_meta') and not model._meta.abstract:
            admin.site.register(model)
    except Exception:
        pass
