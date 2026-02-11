---
name: django
description: Security testing playbook for Django applications covering ORM injection, CSRF bypass, and admin panel exploitation
---

# Django

Security testing for Django applications. Focus on ORM injection via raw/extra, CSRF middleware bypass, DEBUG information leakage, admin panel exploitation, and Django REST Framework misconfigurations.

## Attack Surface

**Core Components**
- URL routing: `urls.py` patterns, `path()`, `re_path()`, namespaces, app-level includes
- Views: function-based, class-based (CBVs), generic views, mixins, `LoginRequiredMixin`, `PermissionRequiredMixin`
- Middleware stack: `SecurityMiddleware`, `CsrfViewMiddleware`, `AuthenticationMiddleware`, `SessionMiddleware`, custom middleware ordering
- Template engine: Django templates, Jinja2 backend, template tags, filters

**Data Handling**
- ORM: QuerySets, `F()`, `Q()`, `extra()`, `raw()`, `RawSQL()`, aggregation expressions
- Forms: `ModelForm`, `Form`, custom validators, `cleaned_data`, file uploads
- Serializers (DRF): `ModelSerializer`, `Serializer`, `HyperlinkedModelSerializer`, nested serializers

**Authentication & Sessions**
- `django.contrib.auth`: User model, custom user models, backends, password hashers
- Session backends: database, cache, file, cookie-based (signed)
- Token auth (DRF): `TokenAuthentication`, `SessionAuthentication`, `BasicAuthentication`, JWT via third-party

**Deployment**
- WSGI/ASGI (Gunicorn, Daphne, Uvicorn), reverse proxy, static/media file serving

## High-Value Targets

- `/admin/` panel and all model admin pages (user creation, permission editing, data export)
- `DEBUG=True` error pages exposing settings, SQL queries, local variables, traceback
- DRF browsable API (`/api/`, `/api/v1/`) with interactive forms and schema endpoints
- Password reset flow (`/accounts/password_reset/`, token reuse, host header injection)
- File upload endpoints and `MEDIA_ROOT` serving configuration
- Management command exposure via admin or custom endpoints
- Signal handlers performing privileged operations
- Celery task endpoints that accept user-controlled arguments

## Reconnaissance

**Settings Discovery**
```
GET /non-existent-path/  (trigger DEBUG=True error page)
GET /admin/
GET /api/
GET /api/schema/
GET /api/docs/
GET /__debug__/  (Django Debug Toolbar)
```

Extract from DEBUG error pages: `settings.py` values, installed apps, middleware stack, URL patterns, database configuration, `SECRET_KEY` hints.

**URL Pattern Enumeration**
- DEBUG error pages list all registered URL patterns
- DRF schema endpoints (`/api/schema/`, `/api/schema.json`) expose all API routes
- Django Debug Toolbar panels reveal SQL queries, template contexts, signals

**Version Fingerprinting**
- Admin page CSS/JS paths contain version indicators
- `X-Frame-Options` header presence and value
- Default error page formatting differences across versions

## Key Vulnerabilities

### ORM Injection

**extra() and raw() Exploitation**
```python
# Vulnerable: user input in extra()
queryset.extra(where=["title LIKE '%%%s%%'" % user_input])

# Vulnerable: string formatting in raw()
Model.objects.raw("SELECT * FROM app_model WHERE id = %s" % user_id)

# Vulnerable: RawSQL in annotations
queryset.annotate(val=RawSQL("SELECT col FROM t WHERE id = %s" % uid, []))
```

**Safe Bypass Vectors**
- `extra(select={})`, `extra(where=[])`, `extra(tables=[])` all accept raw SQL
- `QuerySet.values()` and `values_list()` with user-controlled field names for column enumeration
- JSON field lookups with crafted keys: `data__key' OR 1=1--`
- `order_by()` with user-controlled field names can leak schema info via error messages

### CSRF Bypass

**Middleware Bypass Vectors**
- `@csrf_exempt` decorator on views (check all API endpoints)
- Subdomain cookie injection when `CSRF_COOKIE_DOMAIN` is set broadly
- `CSRF_TRUSTED_ORIGINS` misconfiguration allowing attacker-controlled origins
- Missing `CsrfViewMiddleware` in `MIDDLEWARE` list entirely
- DRF `SessionAuthentication` enforces CSRF but `TokenAuthentication` does not — mixing auth classes can create gaps

**Token Manipulation**
- CSRF token rotation behavior: tokens remain valid across sessions
- `csrfmiddlewaretoken` in GET parameters (logged in server logs, referer headers)
- Cookie-to-header token comparison exploiting cookie fixation

### Admin Panel Exploitation

- Default admin URL at `/admin/` with weak/default credentials, no rate limiting
- `is_staff=True` grants admin access; `is_superuser=True` grants all permissions
- Custom admin views without proper `has_permission()` checks
- Admin CSV/JSON export actions on sensitive models, `list_display` leaking sensitive fields
- Admin log (`/admin/admin/logentry/`) reveals all admin actions
- Custom admin actions executing OS commands or evaluating code

### DEBUG Mode Leakage

- Full Python traceback with local variable values, `settings.py` contents (credentials, `SECRET_KEY`, API keys)
- SQL queries with parameters, template file paths, installed apps and middleware chain
- Trigger: `GET /non-existent/`, `POST /api/endpoint/` with malformed JSON

### DRF Vulnerabilities

**Mass Assignment via ModelSerializer**
```python
# Vulnerable: all fields exposed
class UserSerializer(ModelSerializer):
    class Meta:
        model = User
        fields = '__all__'  # includes is_staff, is_superuser
```
Test by sending extra fields: `is_staff`, `is_superuser`, `is_active`, `role`, `permissions`.

**Browsable API Exposure**
- Production DRF with `DEFAULT_RENDERER_CLASSES` including `BrowsableAPIRenderer`
- Interactive forms reveal field names, types, choices, and validation rules
- Schema endpoints expose full API surface

**Filter/Search Injection**
- `django-filter` with user-controlled lookup expressions: `field__regex`, `field__contains`
- `SearchFilter` with `search_fields` using `^`, `=`, `@`, `$` prefixes
- `OrderingFilter` with unrestricted `ordering_fields` leaking related model data

### Session Security

- `SECRET_KEY` compromise enables session forging, CSRF token generation, password reset token creation
- Cookie-based session backend: sessions stored client-side, signed but not encrypted
- Session fixation via session ID in URL (legacy configurations)
- Missing `SESSION_COOKIE_HTTPONLY`, `SESSION_COOKIE_SECURE`, `SESSION_COOKIE_SAMESITE`

### Host Header Injection

**Password Reset Poisoning**
```
POST /accounts/password_reset/
Host: attacker.com
```
If `USE_X_FORWARDED_HOST=True` or host validation is weak, reset emails contain attacker-controlled URLs.

### File Handling

- `FileField`/`ImageField` upload path traversal via crafted filenames
- `MEDIA_URL` serving user uploads without authentication
- Missing file type validation beyond extension checking
- `X-Sendfile`/`X-Accel-Redirect` misconfiguration exposing arbitrary files

## Bypass Techniques

- Method switching: Django views accepting multiple HTTP methods with inconsistent auth checks
- Content-type switching between `application/json` and `multipart/form-data` in DRF
- Trailing slash manipulation: Django's `APPEND_SLASH` can cause auth middleware to miss unslashed paths
- Unicode normalization in URL patterns bypassing path-based middleware checks
- `@csrf_exempt` on DRF views combined with session auth creating CSRF-free state-changing endpoints
- Query parameter injection via `request.GET` when view expects `request.POST`

## Testing Methodology

1. **Settings audit** - Check for `DEBUG=True`, weak `SECRET_KEY`, permissive `ALLOWED_HOSTS`, `CORS_ALLOW_ALL_ORIGINS`
2. **Admin enumeration** - Probe `/admin/`, test default credentials, enumerate registered models
3. **ORM injection** - Identify views using `extra()`, `raw()`, `RawSQL()`, test all user-controlled query parameters
4. **Auth matrix** - Test each endpoint as unauthenticated, regular user, staff, superuser
5. **DRF audit** - Check serializer fields, filter backends, renderer classes, permission classes per view
6. **CSRF validation** - Verify all state-changing endpoints enforce CSRF, especially mixed-auth DRF views
7. **Session testing** - Validate cookie flags, session rotation on login/logout, concurrent session handling

## Validation Requirements

- ORM injection proof with SQL execution evidence (time-based, error-based, or data exfiltration)
- CSRF bypass demonstrated with cross-origin request achieving state change
- Admin panel access with non-superuser credentials showing privilege escalation
- Mass assignment proof showing modification of protected fields via API
- DEBUG page screenshots with sensitive data (settings, SQL, tracebacks)
- Host header injection proof with manipulated password reset email
- Side-by-side requests showing IDOR across users/tenants

## False Positives

- `extra()` and `raw()` with properly parameterized queries (using `%s` placeholders with params list)
- CSRF "bypass" on endpoints using `TokenAuthentication` exclusively (by design)
- Admin access by users with legitimate `is_staff` permissions
- DRF browsable API in development/staging environments intentionally enabled
- `DEBUG=True` on non-production instances

## Impact

- ORM injection: full database read/write, potential RCE via database-specific functions
- `SECRET_KEY` compromise: session forging, authentication bypass for all users
- Admin exploitation: complete data access, user account takeover, potential RCE
- CSRF bypass: account takeover, privilege escalation, data modification
- Mass assignment: privilege escalation from regular user to admin

## Pro Tips

- Django's `SECRET_KEY` is the master key: check version control history, environment files, and CI/CD configs for leaked keys
- `manage.py shell` access via exposed management interfaces enables direct ORM manipulation
- Django's `__` lookup syntax in filters (`field__gte`, `field__regex`) is a common injection vector when user input flows into filter kwargs
- Check for cache backends combined with user-controlled cache keys
- DRF's `perform_create`/`perform_update` hooks often set ownership; verify they cannot be bypassed by direct serializer field injection
- Admin `raw_id_fields` and `autocomplete_fields` expose lookup endpoints that may leak data

## Summary

Django provides strong defaults but common misconfigurations create critical vulnerabilities. Priority targets: ORM injection via `extra()`/`raw()`, CSRF bypass on mixed-auth DRF views, admin panel with weak credentials, `DEBUG=True` in production, and `ModelSerializer` mass assignment. Always check `SECRET_KEY` security, session configuration, and host header handling. The admin panel and DRF browsable API are high-value reconnaissance and exploitation targets.
