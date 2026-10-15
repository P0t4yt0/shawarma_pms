"""inventory app: raw ingredients, stock ledger, recipes (BOM) and low-stock alerts."""
from decimal import Decimal

from django.conf import settings
from django.db import models, transaction
from django.utils import timezone
from django.utils.crypto import get_random_string

from point_of_sale.models import MenuVariant


class Unit(models.TextChoices):
    KG = "kg", "Kilogram"
    L = "L", "Liter"
    PCS = "pcs", "Pieces"


class IngredientCategory(models.TextChoices):
    PROTEIN = "protein", "Proteins"
    CARB = "carb", "Carbohydrates"
    PRODUCE = "produce", "Produce"
    SAUCE = "sauce", "Sauces"
    PACKAGING = "packaging", "Packaging"


class InsufficientStock(Exception):
    def __init__(self, shortages):
        self.shortages = shortages      # list[(Ingredient, needed, available)]
        super().__init__("Insufficient stock: " + ", ".join(f"{i.name} (need {n}, have {a})" for i, n, a in shortages))


class Category(models.Model):
    name = models.CharField(max_length=100, unique=True)
    slug = models.SlugField(max_length=100, unique=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name
    

class Ingredient(models.Model):
    name = models.CharField(max_length=100, unique=True)
    
    # Ang blank=True ay ibig sabihin pwede itong iwang walang laman sa form.
    sku = models.CharField(max_length=30, unique=True, blank=True)
    
    category = models.ForeignKey('point_of_sale.Category', on_delete=models.PROTECT, related_name="inventory_items", null=True, blank=True)
    unit = models.CharField(max_length=5, choices=Unit.choices)
    current_stock = models.DecimalField(max_digits=12, decimal_places=3, default=0)
    reorder_level = models.DecimalField(max_digits=12, decimal_places=3, default=0, help_text="Low-stock alert fires when stock <= this")
    par_level = models.DecimalField(max_digits=12, decimal_places=3, default=0, help_text="Target stock after replenishment")
    cost_per_unit = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    is_perishable = models.BooleanField(default=False)
    shelf_life_days = models.PositiveSmallIntegerField(null=True, blank=True)
    is_active = models.BooleanField(default=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["category", "name"]
        permissions = [("adjust_stock", "Can manually adjust stock levels")]

    def __str__(self):
        return f"{self.name} ({self.current_stock} {self.unit})"

    # =========================================================
    # AUTOMATIC SKU GENERATOR (Nangyayari pag kinlick ang Save)
    # =========================================================
    def save(self, *args, **kwargs):
        # Kapag walang inilagay na SKU ang user...
        if not self.sku:
            is_unique = False
            
            # Paulit-ulit itong gagawa ng code hanggang sa makahanap ng code na walang kaparehas (Unique)
            while not is_unique:
                # Gagawa ng random code, halimbawa: "SKU-A1B2C3D4"
                new_sku = 'SKU-' + get_random_string(8).upper()
                
                # Iche-check sa database kung may nakagamit na nito
                if not Ingredient.objects.filter(sku=new_sku).exists():
                    self.sku = new_sku  # Kung wala pang gumagamit, ito na ang magiging SKU niya!
                    is_unique = True    # Ipapahinto na ang pag-loop
                    
        # I-save na sa database nang tuluyan
        super().save(*args, **kwargs)

    @property
    def is_low_stock(self):
        return self.current_stock <= self.reorder_level

    @property
    def is_out_of_stock(self):
        return self.current_stock <= 0

    @transaction.atomic
    def adjust(self, delta, movement_type, user=None, order=None, note=""):
        """Apply a signed quantity change safely (row-locked) and record it in the ledger."""
        delta = Decimal(delta)
        locked = Ingredient.objects.select_for_update().get(pk=self.pk)
        locked.current_stock += delta
        locked.save(update_fields=["current_stock", "updated_at"])
        StockMovement.objects.create(
            ingredient=locked, movement_type=movement_type, quantity_change=delta,
            balance_after=locked.current_stock, order=order, performed_by=user, note=note)
        LowStockAlert.evaluate(locked)
        self.current_stock = locked.current_stock
        return locked.current_stock


class StockMovement(models.Model):
    class Type(models.TextChoices):
        PURCHASE = "purchase", "Delivery / purchase"
        SALE = "sale", "Deducted by sale"
        SALE_REVERSAL = "sale_reversal", "Restored (void/cancel)"
        WASTE = "waste", "Waste / prep loss"
        SPOILAGE = "spoilage", "Spoilage / expired"
        ADJUSTMENT = "adjustment", "Manual adjustment"
        COUNT = "count", "Stock-count correction"

    ingredient = models.ForeignKey(Ingredient, on_delete=models.PROTECT, related_name="movements")
    movement_type = models.CharField(max_length=20, choices=Type.choices, db_index=True)
    quantity_change = models.DecimalField(max_digits=12, decimal_places=3)      # signed
    balance_after = models.DecimalField(max_digits=12, decimal_places=3)
    order = models.ForeignKey("point_of_sale.Order", null=True, blank=True, on_delete=models.SET_NULL,
                              related_name="stock_movements")
    performed_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                                     on_delete=models.SET_NULL, related_name="+")
    note = models.CharField(max_length=200, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["ingredient", "-created_at"])]

    def __str__(self):
        return f"{self.ingredient.name}: {self.quantity_change:+} ({self.movement_type})"


class Recipe(models.Model):
    """Bill of Materials for ONE unit of a sellable variant."""
    variant = models.OneToOneField("point_of_sale.MenuVariant", on_delete=models.CASCADE, related_name="recipe")
    notes = models.CharField(max_length=200, blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        permissions = []

    def __str__(self):
        return f"Recipe: {self.variant}"

    @property
    def unit_cost(self):
        return sum((l.quantity * l.ingredient.cost_per_unit for l in self.lines.select_related("ingredient")),
                   Decimal("0"))

    def shortages(self, quantity=1):
        out = []
        for line in self.lines.select_related("ingredient"):
            needed = line.quantity * quantity
            if line.ingredient.current_stock < needed:
                out.append((line.ingredient, needed, line.ingredient.current_stock))
        return out

    @transaction.atomic
    def deduct_stock(self, quantity=1, order=None, user=None):
        if getattr(settings, "INVENTORY_BLOCK_ON_INSUFFICIENT", False):
            short = self.shortages(quantity)
            if short:
                raise InsufficientStock(short)
        used = []
        for line in self.lines.select_related("ingredient"):
            amount = line.quantity * quantity
            line.ingredient.adjust(-amount, StockMovement.Type.SALE, user=user, order=order,
                                   note=f"{quantity} x {self.variant}")
            used.append((line.ingredient, amount))
        return used

    @transaction.atomic
    def restore_stock(self, quantity=1, order=None, user=None):
        """Reverse a deduction (order cancelled/voided before being made)."""
        for line in self.lines.select_related("ingredient"):
            line.ingredient.adjust(line.quantity * quantity, StockMovement.Type.SALE_REVERSAL,
                                   user=user, order=order, note=f"Reversal {quantity} x {self.variant}")


class RecipeLine(models.Model):
    recipe = models.ForeignKey(Recipe, on_delete=models.CASCADE, related_name="lines")
    ingredient = models.ForeignKey(Ingredient, on_delete=models.PROTECT, related_name="recipe_lines")
    quantity = models.DecimalField(max_digits=10, decimal_places=4, help_text="Per ONE unit sold, in the ingredient's unit")

    class Meta:
        unique_together = [("recipe", "ingredient")]

    def __str__(self):
        return f"{self.recipe.variant}: {self.quantity} {self.ingredient.unit} {self.ingredient.name}"


class LowStockAlert(models.Model):
    class Status(models.TextChoices):
        OPEN = "open", "Open"
        ACKNOWLEDGED = "ack", "Acknowledged"
        RESOLVED = "resolved", "Resolved"

    ingredient = models.ForeignKey(Ingredient, on_delete=models.CASCADE, related_name="alerts")
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.OPEN, db_index=True)
    stock_at_trigger = models.DecimalField(max_digits=12, decimal_places=3)
    triggered_at = models.DateTimeField(auto_now_add=True)
    acknowledged_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                                        on_delete=models.SET_NULL, related_name="+")
    resolved_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-triggered_at"]

    def __str__(self):
        return f"LOW: {self.ingredient.name} @ {self.stock_at_trigger}"

    @classmethod
    def evaluate(cls, ingredient):
        """Called after every stock change: open one alert while low, auto-resolve when replenished."""
        open_alerts = cls.objects.filter(ingredient=ingredient).exclude(status=cls.Status.RESOLVED)
        if ingredient.is_low_stock:
            if not open_alerts.exists():
                cls.objects.create(ingredient=ingredient, stock_at_trigger=ingredient.current_stock)
        else:
            open_alerts.update(status=cls.Status.RESOLVED, resolved_at=timezone.now())

class RecipeItem(models.Model):
    variant = models.ForeignKey(MenuVariant, on_delete=models.CASCADE, related_name='recipe_items')
    ingredient = models.ForeignKey(Ingredient, on_delete=models.PROTECT)
    quantity_required = models.DecimalField(max_digits=10, decimal_places=3, help_text="Gaano karami ang kailangan per serving?")

    class Meta:
        unique_together = ('variant', 'ingredient') # Para hindi ma-doble ang ingredient sa isang product

    def __str__(self):
        return f"{self.quantity_required} {self.ingredient.unit} of {self.ingredient.name} for {self.variant.name}"