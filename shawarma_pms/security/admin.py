from django.contrib import admin
from .models import Attendance, EmployeeProfile, Shift

admin.site.register(EmployeeProfile)
admin.site.register(Shift)
admin.site.register(Attendance)
